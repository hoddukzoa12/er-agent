"""Thin wrapper over Nemotron served by NVIDIA NIM (OpenAI-compatible endpoint)."""
from __future__ import annotations

import json
import os
import random
import threading
import time

from openai import OpenAI

from .config import MODEL_REASON, NIM_BASE_URL, NVIDIA_API_KEY

_client: OpenAI | None = None
# build.nvidia.com rate-limits bursts (429); cap in-flight requests across all call threads
_inflight = threading.BoundedSemaphore(int(os.getenv("ER_NIM_CONCURRENCY", "3")))
RETRYABLE = ("RateLimitError", "InternalServerError", "APIConnectionError", "APITimeoutError")


def available() -> bool:
    return bool(NVIDIA_API_KEY)


def client() -> OpenAI:
    global _client
    if _client is None:
        # Fail fast: a stalled NIM call should surface in the UI, not hang the dispatch.
        _client = OpenAI(base_url=NIM_BASE_URL, api_key=NVIDIA_API_KEY, timeout=45, max_retries=0)
    return _client


def chat(messages: list[dict], tools: list[dict] | None = None, tool_choice="auto",
         model: str = MODEL_REASON, max_tokens: int = 4096, retries: int = 4, think: bool = False):
    """Return the assistant message; retries transient NIM errors.

    think=False disables Nemotron's reasoning trace (≈7x faster on structured calls).
    """
    kwargs = dict(model=model, messages=messages, temperature=0, max_tokens=max_tokens,
                  extra_body={"chat_template_kwargs": {"enable_thinking": think}})
    if tools:
        kwargs.update(tools=tools, tool_choice=tool_choice)
    for attempt in range(retries + 1):
        try:
            with _inflight:
                return client().chat.completions.create(**kwargs).choices[0].message
        except Exception as exc:
            if attempt == retries or type(exc).__name__ not in RETRYABLE:
                raise
            print(f"[llm] {type(exc).__name__} attempt {attempt + 1}/{retries}: {str(exc)[:120]}", flush=True)
            time.sleep(min(15.0, 1.0 * 2 ** attempt) + random.uniform(0, 1.0))  # exponential backoff + jitter


def call_tool(messages: list[dict], tool: dict, model: str = MODEL_REASON, think: bool = False) -> dict:
    """Force a single structured tool call and return its parsed arguments."""
    name = tool["function"]["name"]
    for attempt in range(2):  # occasionally the tool call comes back as plain text; ask once more
        msg = chat(messages, tools=[tool], tool_choice={"type": "function", "function": {"name": name}},
                   model=model, think=think)
        if msg.tool_calls:
            return json.loads(msg.tool_calls[0].function.arguments)
    raise RuntimeError(f"model did not call {name}: {(msg.content or '')[:200]}")
