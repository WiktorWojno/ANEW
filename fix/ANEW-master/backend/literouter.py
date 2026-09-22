"""LiteRouter client with Standard-plan limits: 1 concurrent, 5s free cooldown."""
import asyncio
import os
import time

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

BASE_URL = os.getenv("LITEROUTER_BASE_URL", "https://api.literouter.com/v1")
MODEL_MAIN = os.getenv("MODEL_MAIN", "mistral-large-2512:full-context")
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "qwen3.8-27b:free")

_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
_queue.put_nowait(True)
_last_free = 0.0


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


async def chat_stream(model: str, messages: list, max_tokens: int = 1000, temperature: float = 0.9):
    """Async generator yielding text chunks. Respects 1-concurrent + free cooldown. Retries once on 429/403."""
    client = get_client()
    await _acquire(model)
    try:
        for attempt in range(2):
            try:
                stream = await client.chat.completions.create(
                    model=model, messages=messages,
                    max_tokens=max_tokens, temperature=temperature, stream=True,
                )
                async for part in stream:
                    d = part.choices[0].delta.content if part.choices else None
                    if d:
                        yield d
                return
            except Exception as e:
                msg = str(e)
                if attempt == 0 and ("429" in msg or "403" in msg or "rate" in msg.lower()):
                    await asyncio.sleep(3)
                    continue
                yield f"\n[LiteRouter error: {msg[:300]}]"
                return
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
