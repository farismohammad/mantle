"""Model providers Mantle can authenticate on behalf of every agent.

Provider IDs, API-key variables, OAuth support and default models follow
Pi 1.0.0 (`@earendil-works/pi-ai` env-api-keys and OAuth loaders, and
`pi-coding-agent` defaultModelPerProvider). Unknown providers still work: they
can be authenticated with an API key, but Mantle cannot verify or default them.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class KeyCheck:
    url: str
    style: str  # "bearer", "anthropic" or "google"


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    env: tuple[str, ...] = ()
    oauth: bool = False
    default_model: str | None = None
    check: KeyCheck | None = None

    @property
    def api_key(self) -> bool:
        return bool(self.env)

    @property
    def primary_env(self) -> str | None:
        return self.env[-1] if self.env else None


def _bearer(url: str) -> KeyCheck:
    return KeyCheck(url, "bearer")


PROVIDERS: dict[str, Provider] = {provider.id: provider for provider in (
    # Mantle keeps its historical OpenAI default so existing agents behave the same.
    Provider("openai", "OpenAI", ("OPENAI_API_KEY",), True, "gpt-5.6", _bearer("https://api.openai.com/v1/models")),
    # Pi accepts ANTHROPIC_OAUTH_TOKEN and ANTHROPIC_API_KEY; the API key is the one users paste.
    Provider("anthropic", "Anthropic", ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"), True, "claude-opus-4-8",
             KeyCheck("https://api.anthropic.com/v1/models", "anthropic")),
    Provider("openai-codex", "OpenAI Codex (ChatGPT subscription)", (), True, "gpt-6.1-sol"),
    Provider("github-copilot", "GitHub Copilot", ("COPILOT_GITHUB_TOKEN",), True, "gpt-5.4"),
    Provider("google", "Google Gemini", ("GEMINI_API_KEY",), False, "gemini-3.1-pro-preview",
             KeyCheck("https://generativelanguage.googleapis.com/v1beta/models", "google")),
    Provider("deepseek", "DeepSeek", ("DEEPSEEK_API_KEY",), False, "deepseek-v4-pro", _bearer("https://api.deepseek.com/models")),
    Provider("openrouter", "OpenRouter", ("OPENROUTER_API_KEY",), True, "moonshotai/kimi-k2.6", _bearer("https://openrouter.ai/api/v1/key")),
    Provider("xai", "xAI", ("XAI_API_KEY",), True, "grok-4.7", _bearer("https://api.x.ai/v1/models")),
    Provider("mistral", "Mistral", ("MISTRAL_API_KEY",), False, "devstral-medium-latest", _bearer("https://api.mistral.ai/v1/models")),
    Provider("groq", "Groq", ("GROQ_API_KEY",), False, "openai/gpt-oss-120b", _bearer("https://api.groq.com/openai/v1/models")),
    Provider("cerebras", "Cerebras", ("CEREBRAS_API_KEY",), False, "gpt-oss-120b", _bearer("https://api.cerebras.ai/v1/models")),
    Provider("together", "Together AI", ("TOGETHER_API_KEY",), False, "moonshotai/Kimi-K3", _bearer("https://api.together.xyz/v1/models")),
    Provider("fireworks", "Fireworks", ("FIREWORKS_API_KEY",), False, "accounts/fireworks/models/kimi-k3",
             _bearer("https://api.fireworks.ai/inference/v1/models")),
    Provider("huggingface", "Hugging Face", ("HF_TOKEN",), False, "moonshotai/Kimi-K2.6"),
    Provider("vercel-ai-gateway", "Vercel AI Gateway", ("AI_GATEWAY_API_KEY",), False, "zai/glm-5.1"),
    Provider("zai", "ZAI Coding Plan", ("ZAI_API_KEY",), False, "glm-5.3"),
    Provider("moonshotai", "Moonshot AI", ("MOONSHOT_API_KEY",), False, "kimi-k2.6"),
    Provider("kimi-coding", "Kimi For Coding", ("KIMI_API_KEY",), True, "kimi-for-coding"),
    Provider("minimax", "MiniMax", ("MINIMAX_API_KEY",), False, "MiniMax-M2.7"),
    Provider("nvidia", "NVIDIA NIM", ("NVIDIA_API_KEY",), False, "nvidia/nemotron-3-super-120b-a12b"),
    Provider("meta", "Meta", ("META_API_KEY",), True, "muse-spark-1.3"),
    Provider("radius", "Radius", ("RADIUS_API_KEY",), True, "balanced"),
)}

# Shown first in pickers; everything else is still accepted by ID.
COMMON = ("anthropic", "openai", "openai-codex", "github-copilot", "google", "deepseek", "openrouter", "xai", "mistral", "groq")


def lookup(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


def env_variables() -> dict[str, str]:
    """Map every known provider API-key variable to its provider ID."""
    return {variable: provider.id for provider in PROVIDERS.values() for variable in provider.env}


@dataclass(frozen=True)
class KeyCheckResult:
    status: str  # "valid", "invalid" or "unknown"
    detail: str


def check_api_key(provider: Provider, key: str, *, client: httpx.Client | None = None) -> KeyCheckResult:
    """Make one read-only request that only succeeds with a working key."""
    if provider.check is None:
        return KeyCheckResult("unknown", f"Mantle cannot verify {provider.name} keys; it will be checked on first use.")
    headers = {"user-agent": "mantle-cli"}
    if provider.check.style == "anthropic":
        headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif provider.check.style == "google":
        headers["x-goog-api-key"] = key
    else:
        headers["authorization"] = f"Bearer {key}"
    owned = client is None
    client = client or httpx.Client(timeout=15)
    try:
        response = client.get(provider.check.url, headers=headers)
    except httpx.HTTPError as error:
        return KeyCheckResult("unknown", f"Could not reach {provider.name} to verify the key ({type(error).__name__}).")
    finally:
        if owned:
            client.close()
    if response.is_success:
        return KeyCheckResult("valid", f"{provider.name} accepted the key.")
    if response.status_code in (401, 403):
        return KeyCheckResult("invalid", f"{provider.name} rejected the key (HTTP {response.status_code}).")
    return KeyCheckResult("unknown", f"{provider.name} returned HTTP {response.status_code}; the key could not be verified.")
