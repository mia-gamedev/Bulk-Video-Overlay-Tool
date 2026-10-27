"""App-wide settings (not per project), stored outside the repo in the user's home folder.

Currently: which API "Parse with AI" uses (Anthropic or DeepSeek) and their keys. Keys are
never sent back to the browser – only whether one is set and its last 4 characters.
"""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("CAROUSEL_SETTINGS") or Path.home() / ".carousel_builder.json")

PROVIDERS = {          # both are called through the Anthropic SDK (ai_parser.py)
    "claude":   {"label": "Anthropic API", "env": "ANTHROPIC_API_KEY", "package": "anthropic"},
    "deepseek": {"label": "DeepSeek API",  "env": "DEEPSEEK_API_KEY",  "package": "anthropic"},
}


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data):
    PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(PATH, 0o600)          # owner only (no effect on Windows, which is per-user anyway)
    except OSError:
        pass


def _keys(data):
    keys = dict(data.get("api_keys") or {})
    if data.get("claude_api_key"):                  # written by the first version
        keys.setdefault("claude", data["claude_api_key"])
    return keys


def saved_key(provider):
    return _keys(load()).get(provider)


def key(provider):
    """Saved key, else the provider's environment variable, else None."""
    return saved_key(provider) or os.environ.get(PROVIDERS[provider]["env"]) or None


def status(provider):
    p = PROVIDERS[provider]
    saved = saved_key(provider)
    installed = True
    if p["package"]:
        try:
            __import__(p["package"])
        except ImportError:
            installed = False
    return {"label": p["label"], "installed": installed, "package": p["package"],
            "source": "settings" if saved else "environment" if os.environ.get(p["env"]) else None,
            "env": p["env"], "hint": ("…" + saved[-4:]) if saved else None}


def provider():
    """API used by "Parse with AI": the one picked in the ⚙ menu (default: Anthropic)."""
    p = load().get("ai_provider")
    return p if p in PROVIDERS else "claude"


def set_provider(name):
    if name not in PROVIDERS:
        raise ValueError(name)
    data = load()
    data["ai_provider"] = name
    save(data)


def all_status():
    return {"active": provider(), "providers": {name: status(name) for name in PROVIDERS}}


def set_key(provider, value):
    data = load()
    keys = _keys(data)
    data.pop("claude_api_key", None)
    if value:
        keys[provider] = value.strip()
    else:
        keys.pop(provider, None)
    data["api_keys"] = keys
    save(data)
