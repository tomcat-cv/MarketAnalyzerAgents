from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Tuple


class OpenAIError(RuntimeError):
    pass


@dataclass
class OpenAIResult:
    text: str
    raw: Dict[str, Any]


def _iter_text_fragments(output: Iterable[Mapping[str, Any]]) -> Iterable[str]:
    for item in output:
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text", "")
                    if text:
                        yield text
        elif item.get("type") in {"output_text", "text"}:
            text = item.get("text", "")
            if text:
                yield text


def extract_response_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = payload.get("output", [])
    if isinstance(output, list):
        text = "\n".join(_iter_text_fragments(output)).strip()
        if text:
            return text
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_payload(
    *,
    model: str,
    system: str,
    user: str,
    reasoning_effort: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def call_responses_api(
    *,
    api_key: str,
    api_base: str,
    payload: Mapping[str, Any],
    timeout: int = 180,
    max_retries: int = 2,
    retry_backoff_seconds: float = 1.0,
) -> OpenAIResult:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    last_error: OpenAIError | None = None
    for attempt in range(max(0, max_retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            return OpenAIResult(text=extract_response_text(raw), raw=raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = OpenAIError(f"OpenAI API returned HTTP {exc.code}: {detail}")
            if exc.code != 429 and exc.code < 500:
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = OpenAIError(f"Could not reach OpenAI API: {exc.reason}")
        if attempt < max_retries:
            time.sleep(max(0.0, retry_backoff_seconds) * (2 ** attempt))

    assert last_error is not None
    raise last_error


def run_openai(
    *,
    settings: Mapping[str, Any],
    system: str,
    user: str,
    model_override: str | None = None,
) -> Tuple[OpenAIResult, Dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise OpenAIError(
            "OPENAI_API_KEY is missing. Copy .env.example to .env, fill it, "
            "or run with --backend dry-run."
        )

    openai_settings = settings.get("openai", {})
    model = (
        model_override
        or os.environ.get("OPENAI_MODEL")
        or openai_settings.get("model")
        or settings.get("model")
        or "gpt-5.4"
    )
    payload = build_payload(
        model=str(model),
        system=system,
        user=user,
        reasoning_effort=str(openai_settings.get("reasoning_effort", "medium")),
    )
    result = call_responses_api(
        api_key=api_key,
        api_base=str(openai_settings.get("api_base", "https://api.openai.com/v1")),
        payload=payload,
        max_retries=int(openai_settings.get("max_retries", 2)),
        retry_backoff_seconds=float(openai_settings.get("retry_backoff_seconds", 1.0)),
    )
    return result, payload
