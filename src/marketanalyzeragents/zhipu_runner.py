from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Tuple


class ZhipuError(RuntimeError):
    pass


@dataclass
class ZhipuResult:
    text: str
    raw: Dict[str, Any]


def _iter_choice_text(choices: Iterable[Mapping[str, Any]]) -> Iterable[str]:
    for choice in choices:
        message = choice.get("message", {})
        if isinstance(message, Mapping):
            content = message.get("content", "")
            if isinstance(content, str) and content:
                yield content


def extract_zhipu_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices", [])
    if isinstance(choices, list):
        text = "\n".join(_iter_choice_text(choices)).strip()
        if text:
            return text
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_zhipu_payload(
    *,
    model: str,
    system: str,
    user: str,
    zhipu_settings: Mapping[str, Any],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": float(zhipu_settings.get("temperature", 0.7)),
        "stream": False,
    }

    max_tokens = zhipu_settings.get("max_tokens")
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)

    thinking_type = str(zhipu_settings.get("thinking", "")).strip()
    if thinking_type:
        payload["thinking"] = {"type": thinking_type}

    return payload


def call_zhipu_api(
    *,
    api_key: str,
    api_base: str,
    payload: Mapping[str, Any],
    timeout: int = 180,
    max_retries: int = 2,
    retry_backoff_seconds: float = 1.0,
) -> ZhipuResult:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    last_error: ZhipuError | None = None
    for attempt in range(max(0, max_retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            return ZhipuResult(text=extract_zhipu_text(raw), raw=raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = ZhipuError(f"Zhipu API returned HTTP {exc.code}: {detail}")
            if exc.code != 429 and exc.code < 500:
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = ZhipuError(f"Could not reach Zhipu API: {exc.reason}")
        if attempt < max_retries:
            time.sleep(max(0.0, retry_backoff_seconds) * (2 ** attempt))

    assert last_error is not None
    raise last_error


def run_zhipu(
    *,
    settings: Mapping[str, Any],
    system: str,
    user: str,
    model_override: str | None = None,
) -> Tuple[ZhipuResult, Dict[str, Any]]:
    api_key = (
        os.environ.get("ZHIPU_API_KEY", "").strip()
        or os.environ.get("ZAI_API_KEY", "").strip()
        or os.environ.get("BIGMODEL_API_KEY", "").strip()
    )
    if not api_key:
        raise ZhipuError(
            "ZHIPU_API_KEY is missing. Add it to .env, for example: ZHIPU_API_KEY=your-key"
        )

    zhipu_settings = settings.get("zhipu", {})
    model = model_override or zhipu_settings.get("model") or settings.get("model") or "glm-5.1"
    payload = build_zhipu_payload(
        model=str(model),
        system=system,
        user=user,
        zhipu_settings=zhipu_settings,
    )
    result = call_zhipu_api(
        api_key=api_key,
        api_base=str(zhipu_settings.get("api_base", "https://open.bigmodel.cn/api/paas/v4")),
        payload=payload,
        max_retries=int(zhipu_settings.get("max_retries", 2)),
        retry_backoff_seconds=float(zhipu_settings.get("retry_backoff_seconds", 1.0)),
    )
    return result, payload
