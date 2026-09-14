from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import AgentConfigurationError


DEFAULT_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_ENV_PATH = Path(__file__).with_name(".env")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AgentConfigurationError(
                f"invalid agent .env entry on line {line_number}"
            )
        name, value = (part.strip() for part in line.split("=", 1))
        if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
            raise AgentConfigurationError(
                f"invalid agent .env variable on line {line_number}"
            )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    api_url: str = DEFAULT_API_URL
    timeout_seconds: float = 120.0
    max_tokens: int = 4096
    max_tool_rounds: int = 8
    temperature: float = 0.1

    @classmethod
    def from_env(cls, env_path: Path = DEFAULT_ENV_PATH) -> AgentConfig:
        _load_env_file(env_path)
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise AgentConfigurationError(
                f"OPENROUTER_API_KEY is missing in {env_path}"
            )
        model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip()
        if not model:
            raise AgentConfigurationError("OPENROUTER_MODEL cannot be empty")
        return cls(api_key=api_key, model=model)
