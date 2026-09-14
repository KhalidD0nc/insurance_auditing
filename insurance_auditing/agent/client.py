from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from .config import AgentConfig
from .errors import OpenRouterError


class OpenRouterClient:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        response_format: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [dict(message) for message in messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        if response_format is not None:
            payload["response_format"] = dict(response_format)

        request = urllib.request.Request(
            self.config.api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost/insurance-auditing",
                "X-OpenRouter-Title": "Insurance Auditing",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise OpenRouterError(
                f"OpenRouter returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpenRouterError("OpenRouter returned invalid JSON") from exc

        try:
            message = result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("OpenRouter response has no assistant message") from exc
        if not isinstance(message, dict):
            raise OpenRouterError("OpenRouter assistant message has an invalid shape")
        return message
