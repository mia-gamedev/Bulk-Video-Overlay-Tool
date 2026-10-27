"""Parse a brief with an AI model. Returns the same schema as brief_parser.parse_brief.

Always the same code path – the Anthropic SDK (Messages API). The ⚙ menu picks which API
answers it:
    claude    Anthropic API              claude-opus-5-5, structured JSON output
    deepseek  DeepSeek's Anthropic-compatible API (the one Claude Code uses with DeepSeek),
              deepseek-chat; JSON is requested in the prompt and checked afterwards
Keys come from the ⚙ menu (settings.py) or ANTHROPIC_API_KEY / DEEPSEEK_API_KEY.
"""
import json
import re

import anthropic

import ai_prompt
import settings

APIS = {
    "claude":   {"base_url": None, "model": "claude-opus-5-5"},
    "deepseek": {"base_url": "https://api.deepseek.com/anthropic", "model": "deepseek-chat"},
}


def client(provider=None, key=None, **kw):
    provider = provider or settings.provider()
    key = key or settings.key(provider)
    base = APIS[provider]["base_url"]
    if provider == "claude" and not key:
        return anthropic.Anthropic(**kw)          # SDK finds ANTHROPIC_API_KEY / ant login itself
    return anthropic.Anthropic(api_key=key, **({"base_url": base} if base else {}), **kw)


def check_key(provider, key):
    """Raise anthropic.AuthenticationError for a wrong key. No tokens are used:
    Anthropic lists its models; DeepSeek gets a request with an invalid size, which a
    valid key answers with 400 and a wrong key with 401."""
    c = client(provider, key, max_retries=0, timeout=20)
    if provider == "claude":
        c.models.list(limit=1)
        return
    try:
        c.messages.create(model=APIS[provider]["model"], max_tokens=0,
                          messages=[{"role": "user", "content": "."}])
    except anthropic.BadRequestError:
        pass                                       # key accepted, request (deliberately) invalid


def _json_from(text):
    """First JSON object in a reply – tolerates ```json fences and text around it."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("the model did not return JSON – try again or use Parse")
    try:
        return json.loads(m.group(0))
    except ValueError:
        raise RuntimeError("the model returned broken JSON – try again or use Parse") from None


def parse_brief(brief):
    provider = settings.provider()
    api = APIS[provider]
    label = settings.PROVIDERS[provider]["label"]
    if provider == "claude":
        response = client(provider).beta.messages.create(
            model=api["model"],
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": ai_prompt.schema()}},
            system=ai_prompt.system_prompt(),
            messages=[{"role": "user", "content": brief}],
        )
    else:
        if not settings.key(provider):
            raise RuntimeError(f"no {label} key – add one in the ⚙ menu")
        response = client(provider).messages.create(
            model=api["model"],
            max_tokens=8000,
            system=ai_prompt.system_prompt() + "\n\nAnswer with JSON only, exactly this shape "
                   "(JSON schema):\n" + json.dumps(ai_prompt.schema(), ensure_ascii=False),
            messages=[{"role": "user", "content": brief}],
        )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{label} declined to parse this brief – use Parse")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{label}'s answer was cut off – the brief may be too long")
    text = "".join(b.text for b in response.content if b.type == "text")
    data = _json_from(text)
    return ai_prompt.normalize(data.get("slides") if isinstance(data, dict) else data)
