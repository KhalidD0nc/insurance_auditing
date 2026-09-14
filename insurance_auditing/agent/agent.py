from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .client import OpenRouterClient
from .config import AgentConfig
from .errors import OpenRouterError
from .prompts import DEFAULT_SYSTEM_PROMPT
from .tools import AgentTool, ToolRegistry


class OpenRouterAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        self.config = config
        self.system_prompt = system_prompt
        self.client = OpenRouterClient(config)

    def run(
        self,
        prompt: str,
        *,
        tools: Sequence[AgentTool] = (),
        history: Sequence[Mapping[str, Any]] = (),
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        registry = ToolRegistry(tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *(dict(message) for message in history),
            {"role": "user", "content": prompt.strip()},
        ]

        for tool_round in range(self.config.max_tool_rounds + 1):
            assistant = self.client.complete(
                messages,
                tools=registry.definitions,
                response_format=response_format,
            )
            messages.append(assistant)
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                content = assistant.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise OpenRouterError(
                        "model returned neither text content nor tool calls"
                    )
                return content.strip()
            if tool_round >= self.config.max_tool_rounds:
                raise OpenRouterError(
                    f"model exceeded {self.config.max_tool_rounds} tool rounds"
                )

            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                name = str(function.get("name", ""))
                arguments = function.get("arguments", "{}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tool_call.get("id", "")),
                        "name": name,
                        "content": registry.invoke(name, arguments),
                    }
                )

        raise AssertionError("unreachable")


def run_agent(
    prompt: str,
    *,
    tools: Sequence[AgentTool] = (),
    history: Sequence[Mapping[str, Any]] = (),
    response_format: Mapping[str, Any] | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    config: AgentConfig | None = None,
) -> str:
    """Run the internal agent from any application module."""
    resolved_config = config or AgentConfig.from_env()
    return OpenRouterAgent(resolved_config, system_prompt=system_prompt).run(
        prompt,
        tools=tools,
        history=history,
        response_format=response_format,
    )
