"""LiteRouter client with Standard-plan limits: 1 concurrent, 5s free cooldown."""
import asyncio
import os
import time

from dotenv import load_dotenv
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

load_dotenv()

BASE_URL = os.getenv("LITEROUTER_BASE_URL", "https://api.literouter.com/v1")
MODEL_MAIN = os.getenv("MODEL_MAIN", "mistral-large-2512:full-context")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "qwen3.8-27b:free")

_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
_queue.put_nowait(True)
_last_free = 0.0

MAX_ATTEMPTS = 4            # 1 initial try + up to 3 retries
RETRY_DELAYS = [2, 5, 10]   # seconds before retry 1, 2, 3 (backs off each time)

# Worth retrying: rate limits, dropped/failed connections, and the
# provider's own 5xx failures — these are usually transient. A bad API key,
# insufficient credits, or a malformed request will fail the same way every
# time, so those are surfaced immediately instead of wasting retries.
RETRYABLE_EXCEPTIONS = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)


def is_free_model(model: str) -> bool:
    return ":free" in model


def get_client() -> AsyncOpenAI:
    key = os.getenv("LITEROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("LITEROUTER_API_KEY missing. Copy .env.example to .env and set it locally.")
    return AsyncOpenAI(base_url=BASE_URL, api_key=key)


async def _acquire(model: str):
    global _last_free
    await _queue.get()
    if is_free_model(model):
        wait = 5.0 - (time.time() - _last_free)
        if wait > 0:
            await asyncio.sleep(wait)


def _release(model: str):
    global _last_free
    if is_free_model(model):
        _last_free = time.time()
    try:
        _queue.put_nowait(True)
    except asyncio.QueueFull:
        pass


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, RETRYABLE_EXCEPTIONS):
        return True
    msg = str(e).lower()
    return "429" in msg or "403" in msg or "rate" in msg


async def chat_stream(model: str, messages: list, max_tokens: int = 1000, temperature: float = 0.9):
    """Async generator yielding text chunks. Respects 1-concurrent + free cooldown.
    Retries transient failures (rate limits, connection drops, provider 5xxs)
    up to MAX_ATTEMPTS times with backoff before giving up."""
    try:
        client = get_client()
        await _acquire(model)
    except Exception as e:
        # Getting the client or waiting for the concurrency slot failed
        # (missing/invalid API key, etc) — not transient, retrying won't help.
        yield f"\n[LiteRouter error: {str(e)[:300]}]"
        return

    try:
        for attempt in range(MAX_ATTEMPTS):
            got_any = False
            finish_reason = None
            try:
                stream = await client.chat.completions.create(
                    model=model, messages=messages,
                    max_tokens=max_tokens, temperature=temperature, stream=True,
                )
                async for part in stream:
                    choice = part.choices[0] if part.choices else None
                    if choice is None:
                        continue
                    d = choice.delta.content
                    if d:
                        got_any = True
                        yield d
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                if got_any:
                    return  # finished cleanly with real content
                # The call succeeded and the provider billed it, but nothing
                # came back on .delta.content — e.g. a content-filter block,
                # or max_tokens getting used up on hidden/reasoning tokens
                # before any visible text. This isn't an exception, so the
                # retry/error handling above never sees it; surface it
                # explicitly instead of silently returning nothing.
                yield (f"\n[LiteRouter error: provider returned no visible content "
                       f"(finish_reason={finish_reason or 'unknown'}) — this call "
                       f"still used credits. Check the model's content filter or "
                       f"context-length limit rather than retrying blindly.]")
                return
            except Exception as e:
                is_last_attempt = attempt == MAX_ATTEMPTS - 1
                # Never retry once real content already streamed this attempt —
                # re-calling create() would restart the reply and duplicate/
                # garble what the user already saw.
                if got_any or is_last_attempt or not _is_retryable(e):
                    yield f"\n[LiteRouter error: {str(e)[:300]}]"
                    return
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
    finally:
        _release(model)


async def chat_once(model: str, messages: list, max_tokens: int = 400, temperature: float = 0.3) -> str:
    out = []
    async for ch in chat_stream(model, messages, max_tokens, temperature):
        if not ch.startswith("\n[LiteRouter error"):
            out.append(ch)
        else:
            return ch
    return "".join(out)
