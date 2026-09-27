"""Runtime configuration, read once from the environment.

Switching between deterministic / LLM / production modes is a config change,
never a code change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

COMPOSER_VERSION = "vera-composer-v1.3.0"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    mode: str = field(default_factory=lambda: _env("MODE", "challenge"))
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "").lower())
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    temperature: float = field(default_factory=lambda: _env_float("TEMPERATURE", 0.0))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT_S", 6.0))
    # Hard wall-clock budget for a whole /v1/tick or /v1/reply call. The official
    # simulator times out at 15s; the brief allows 30s. Stay well under both.
    request_budget_s: float = field(default_factory=lambda: _env_float("REQUEST_BUDGET_S", 9.0))
    max_actions_per_tick: int = 20
    team_name: str = field(default_factory=lambda: _env("TEAM_NAME", "unset — configure TEAM_NAME"))
    team_members: List[str] = field(
        default_factory=lambda: [m.strip() for m in _env("TEAM_MEMBERS").split(",") if m.strip()]
    )
    contact_email: str = field(default_factory=lambda: _env("CONTACT_EMAIL", ""))
    submitted_at: str = field(default_factory=lambda: _env("SUBMITTED_AT", ""))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def llm_enabled(self) -> bool:
        if self.mode == "deterministic":
            return False
        if self.llm_provider == "ollama":
            return bool(self.llm_model)
        return bool(self.llm_provider and self.llm_api_key)

    @property
    def model_label(self) -> str:
        if self.llm_enabled:
            return f"{self.llm_provider}:{self.llm_model or 'default'} (+deterministic planner/validator)"
        return "none — deterministic planner + template realizer (no LLM configured)"


CONFIG = Config()


def reload_config() -> Config:
    """Re-read the environment (used by tests)."""
    global CONFIG
    CONFIG = Config()
    return CONFIG
