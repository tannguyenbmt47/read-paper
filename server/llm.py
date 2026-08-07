"""Lớp mỏng bọc OpenRouter (API tương thích OpenAI).

Hai thứ đáng quan tâm ở đây:

1. **Prompt caching.** Toàn văn paper được nhét vào system prompt và giữ
   nguyên byte-for-byte giữa các lần gọi. Với model `anthropic/*` phải đánh
   dấu `cache_control` thủ công; OpenAI / Gemini / DeepSeek cache tự động.
   Nhờ vậy dịch 30 mẻ vẫn chỉ trả tiền đọc toàn văn ~1 lần.
2. **Sticky routing.** `session_id` giữ mọi request của cùng một paper đi
   về cùng một provider endpoint, nếu không thì cache gần như không bao giờ hit.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from openai import AsyncOpenAI

# đổi được để trỏ sang proxy nội bộ hoặc server giả khi chạy test
BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

DEFAULT_MODEL = os.getenv("OR_MODEL") or "anthropic/claude-sonnet-4.5"
FAST_MODEL = os.getenv("OR_MODEL_FAST") or DEFAULT_MODEL


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cached_tokens += other.cached_tokens
        self.cost += other.cost

    def dict(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "cost": round(self.cost, 5),
        }


def _client() -> AsyncOpenAI:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "Chưa có OPENROUTER_API_KEY. Copy .env.example thành .env và điền key "
            "lấy từ https://openrouter.ai/keys"
        )
    return AsyncOpenAI(
        api_key=key,
        base_url=BASE_URL,
        default_headers={
            "HTTP-Referer": os.getenv("OR_APP_URL", "http://localhost:8000"),
            "X-Title": os.getenv("OR_APP_TITLE", "Paper Reader VI"),
        },
        timeout=300,
    )


def _needs_explicit_cache(model: str) -> bool:
    m = model.lower()
    return m.startswith("anthropic/") or m.startswith("qwen/") or m.startswith("alibaba/")


def system_message(cached_prefix: str, volatile_suffix: str = "", *, model: str) -> dict:
    """System message với ranh giới cache đặt đúng chỗ.

    `cached_prefix` phải giống hệt nhau giữa các request (luật dịch + toàn văn
    paper + glossary). `volatile_suffix` là phần đổi theo từng request và luôn
    nằm SAU điểm cache — đặt ngược lại là cache không bao giờ hit.
    """
    if not _needs_explicit_cache(model):
        text = cached_prefix + ("\n\n" + volatile_suffix if volatile_suffix else "")
        return {"role": "system", "content": text}

    parts = [{
        "type": "text",
        "text": cached_prefix,
        "cache_control": {"type": "ephemeral"},
    }]
    if volatile_suffix:
        parts.append({"type": "text", "text": volatile_suffix})
    return {"role": "system", "content": parts}


def _usage_from(raw) -> Usage:
    if not raw:
        return Usage()
    d = raw if isinstance(raw, dict) else raw.model_dump()
    details = d.get("prompt_tokens_details") or {}
    return Usage(
        prompt_tokens=d.get("prompt_tokens", 0) or 0,
        completion_tokens=d.get("completion_tokens", 0) or 0,
        cached_tokens=(details.get("cached_tokens") or 0),
        cost=float(d.get("cost") or 0.0),
    )


async def stream_text(
    messages: list[dict],
    *,
    model: str | None = None,
    session_id: str | None = None,
    max_tokens: int = 8000,
    temperature: float = 0.2,
    reasoning: dict | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Yield ('delta', text) nhiều lần, rồi ('usage', json) một lần ở cuối.

    `reasoning`: tham số suy luận của OpenRouter. Với model suy luận (DeepSeek V4,
    dòng GPT-5.x…) mà prompt dài và nhiều luật, chúng có thể tiêu **toàn bộ**
    max_tokens vào phần suy luận nội bộ rồi không sinh ra chữ nào. Dịch là việc
    chuyển đổi chứ không phải giải đố, nên tắt bớt suy luận vừa nhanh vừa rẻ vừa
    tránh hẳn tình trạng trả về rỗng.
    """
    model = model or DEFAULT_MODEL
    extra: dict = {"usage": {"include": True}}
    if reasoning is not None:
        extra["reasoning"] = reasoning
    if session_id:
        extra["session_id"] = session_id

    stream = await _client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
        extra_body=extra,
    )
    usage = Usage()
    async for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = _usage_from(chunk.usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            yield "delta", piece
    yield "usage", json.dumps(usage.dict())


async def complete(
    messages: list[dict],
    *,
    model: str | None = None,
    session_id: str | None = None,
    max_tokens: int = 8000,
    temperature: float = 0.2,
    reasoning: dict | None = None,
) -> tuple[str, Usage]:
    model = model or DEFAULT_MODEL
    extra: dict = {"usage": {"include": True}}
    if reasoning is not None:
        extra["reasoning"] = reasoning
    if session_id:
        extra["session_id"] = session_id
    resp = await _client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=extra,
    )
    return resp.choices[0].message.content or "", _usage_from(resp.usage)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str):
    """Bóc JSON ra khỏi câu trả lời kể cả khi model bọc trong ``` hoặc thêm lời dẫn."""
    text = text.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    # strict=False cho phép ký tự xuống dòng thật nằm trong chuỗi JSON. Model rất
    # hay xuống dòng thật thay vì \n khi trả về mã sơ đồ, và json.loads mặc định
    # sẽ ném "Invalid control character" rồi hỏng cả lượt gọi.
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError("Không tìm thấy JSON trong phản hồi của model")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1], strict=False)
    raise ValueError("JSON trong phản hồi bị cắt cụt")


async def list_models() -> list[dict]:
    """Danh sách model OpenRouter đang phục vụ (endpoint này không cần key)."""
    import httpx
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE_URL}/models")
        r.raise_for_status()
        return r.json().get("data", [])
