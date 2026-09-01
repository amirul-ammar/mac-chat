"""Minimal Ollama HTTP client — stdlib only, no data leaves localhost."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class OllamaError(RuntimeError):
    pass


def _post(url: str, payload: dict, timeout: float = 600.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Could not reach Ollama at {url}. Start it with: brew services start ollama\n({exc})"
        ) from exc


def chat(base_url: str, model: str, messages: list[dict], tools: list[dict] | None = None,
         num_ctx: int = 16384, think: bool = False, temperature: float = 0.3) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if tools:
        payload["tools"] = tools
    data = _post(f"{base_url}/api/chat", payload)
    if "error" in data:
        raise OllamaError(data["error"])
    return data.get("message", {})


def available(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def models(base_url: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as resp:
            return [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except Exception:
        return []
