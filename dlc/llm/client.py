"""LLM client wrapper supporting Anthropic + OpenAI in one surface."""

import json
import os
import time
from pathlib import Path

try:
    from anthropic import Anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


MODEL_CATALOG: dict[str, dict] = {
    "claude-haiku-4-5-20251001": {
        "label": "Claude Haiku 4.5", "provider": "anthropic", "tier": "fast",
    },
    "claude-sonnet-4-6": {
        "label": "Claude Sonnet 4.6", "provider": "anthropic", "tier": "balanced",
    },
    "claude-opus-4-8": {
        "label": "Claude Opus 4.8", "provider": "anthropic", "tier": "premium",
    },
    "gpt-4o-mini": {
        "label": "GPT-4o mini", "provider": "openai", "tier": "fast",
    },
    "gpt-4o": {
        "label": "GPT-4o", "provider": "openai", "tier": "balanced",
    },
    "gpt-5": {
        "label": "GPT-5", "provider": "openai", "tier": "premium",
    },
}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 2000

PROVIDER_KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "openai":    "openai_api_key",
}
PROVIDER_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
}


def _config_path() -> Path:
    return Path.home() / ".dlc" / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(_config_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))


def get_api_key(provider: str = "anthropic") -> str | None:
    if provider not in PROVIDER_ENV_VARS:
        return None
    env = os.environ.get(PROVIDER_ENV_VARS[provider])
    if env:
        return env
    return _load_config().get(PROVIDER_KEY_FIELDS[provider])


def set_api_key(provider: str, key: str) -> None:
    if provider not in PROVIDER_KEY_FIELDS:
        raise ValueError(f"Unknown provider: {provider}")
    cfg = _load_config()
    cfg[PROVIDER_KEY_FIELDS[provider]] = key
    _save_config(cfg)


def clear_api_key(provider: str) -> None:
    """Saved key removing function"""
    if provider not in PROVIDER_KEY_FIELDS:
        return
    cfg = _load_config()
    if PROVIDER_KEY_FIELDS[provider] in cfg:
        del cfg[PROVIDER_KEY_FIELDS[provider]]
        _save_config(cfg)


def has_api_key(provider: str = "anthropic") -> bool:
    return bool(get_api_key(provider))


def model_provider(model: str) -> str | None:
    info = MODEL_CATALOG.get(model)
    return info["provider"] if info else None


def _call_anthropic(prompt, model, key, max_tokens, system) -> dict:
    if not _ANTHROPIC_AVAILABLE:
        return {"ok": False, "text": None,
                "error": "anthropic SDK not installed (uv add anthropic)",
                "usage": None, "model": model}
    client = Anthropic(api_key=key)
    last_err = None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system or "You are a helpful circuit reasoning assistant.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in resp.content
                if getattr(block, "type", None) == "text"
            )
            return {"ok": True, "text": text, "error": None,
                    "usage": {"input_tokens": resp.usage.input_tokens,
                              "output_tokens": resp.usage.output_tokens},
                    "model": model}
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            if ("rate" in msg or "429" in msg or "overload" in msg) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break
    return {"ok": False, "text": None,
            "error": f"{type(last_err).__name__ if last_err else 'Error'}: {last_err}",
            "usage": None, "model": model}


def _call_openai(prompt, model, key, max_tokens, system) -> dict:
    if not _OPENAI_AVAILABLE:
        return {"ok": False, "text": None,
                "error": "openai SDK not installed (uv add openai)",
                "usage": None, "model": model}
    client = OpenAI(api_key=key)
    last_err = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_completion_tokens=max_tokens,
                messages=[
                    {"role": "system",
                     "content": system or "You are a helpful circuit reasoning assistant."},
                    {"role": "user", "content": prompt},
                ],
            )
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            usage_out = {
                "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            }
            return {"ok": True, "text": text, "error": None,
                    "usage": usage_out, "model": model}
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            if ("rate" in msg or "429" in msg) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            break
    return {"ok": False, "text": None,
            "error": f"{type(last_err).__name__ if last_err else 'Error'}: {last_err}",
            "usage": None, "model": model}


def call_llm(prompt, *, api_key=None, model=DEFAULT_MODEL,
             max_tokens=DEFAULT_MAX_TOKENS, system=None):
    info = MODEL_CATALOG.get(model)
    if info is None:
        return {"ok": False, "text": None,
                "error": f"Unknown model: {model!r}",
                "usage": None, "model": model}
    provider = info["provider"]
    key = api_key or get_api_key(provider)
    if not key:
        return {"ok": False, "text": None,
                "error": (f"No {provider} API key configured. Open the "
                          f"API keys chip in the toolbar and paste your "
                          f"{PROVIDER_ENV_VARS[provider]}."),
                "usage": None, "model": model}
    if provider == "anthropic":
        return _call_anthropic(prompt, model, key, max_tokens, system)
    if provider == "openai":
        return _call_openai(prompt, model, key, max_tokens, system)
    return {"ok": False, "text": None,
            "error": f"Provider {provider!r} not implemented.",
            "usage": None, "model": model}