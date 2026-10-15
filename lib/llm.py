"""LLM 客户端：DeepSeek v4.1 flash（OpenAI 兼容 /v1）。

密钥只放环境变量或 .env，不入 Git。免费额度有限，调用方必须
控制 max_tokens 并避免重复调用。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

DEFAULT_BASE = os.environ.get("LLM_BASE_URL", "http://111.231.174.164:6010/v1")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _api_key() -> str:
    key = os.environ.get("LLM_API_KEY", "")
    if key:
        return key
    # 兼容 python -c 直接 import 的场景：从项目 .env 读
    from dotenv import load_dotenv
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
        key = os.environ.get("LLM_API_KEY", "")
        if key:
            return key
    raise RuntimeError(
        f"缺少 LLM_API_KEY：请写入 {_ENV_FILE} 或设置环境变量"
    )


def chat(
    prompt: str,
    *,
    system: str = "你是一个严谨的中文内容分析助手。只输出被要求的内容，不要解释。",
    max_tokens: int = 1200,
    temperature: float = 0.7,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int = 120,
) -> str:
    """单轮对话。返回模型文本。失败抛 RuntimeError。"""
    r = requests.post(
        f"{base_url or DEFAULT_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {_api_key()}"},
        json={
            "model": model or DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _salvage_json(text: str) -> dict | list | None:
    """截断 JSON 抢救：丢最后一个不完整元素后重试。"""
    for cut in range(1, 12):
        s = text[:len(text) - cut * 0 or len(text)]
    # 逐字符回退找最后一个能闭合的位置
    for i in range(len(text), max(0, len(text) - 4000), -1):
        seg = text[:i].rstrip()
        for suffix in ("}", "]", '"'):
            if seg.endswith(suffix):
                try:
                    return json.loads(seg)
                except json.JSONDecodeError:
                    continue
    return None


def chat_json(prompt: str, *, max_tokens: int = 1200, retries: int = 2, **kw) -> dict:
    """要求模型输出 JSON 并解析。空返回/解析失败自动重试，仍失败返回 {}。截断时抢救。"""
    last: dict = {}
    for attempt in range(retries + 1):
        text = chat(
            prompt + "\n\n只输出合法 JSON，不要 markdown 代码块，不要解释。",
            max_tokens=max_tokens,
            **kw,
        )
        if not text or not text.strip():
            last = {}
            continue
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json\n").rstrip("```").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            salv = _salvage_json(text)
            if isinstance(salv, dict) and salv:
                return salv
            last = {}
    return last
