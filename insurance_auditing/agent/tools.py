from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


ToolHandler = Callable[[Mapping[str, Any]], Any]
_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name):
            raise ValueError(f"invalid tool name: {self.name!r}")
        if not self.description.strip():
            raise ValueError("tool description cannot be empty")
        if self.parameters.get("type") != "object":
            raise ValueError("tool parameters must be a JSON Schema object")

    @property
    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }

    def invoke(self, arguments: str | Mapping[str, Any]) -> str:
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            if not isinstance(parsed, dict):
                raise ValueError("tool arguments must be a JSON object")
            result = self.handler(parsed)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except Exception as exc:  # Tool failures are observations for the model to handle.
            return json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )


class ToolRegistry:
    def __init__(self, tools: Iterable[AgentTool] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition for tool in self._tools.values()]

    def invoke(self, name: str, arguments: str | Mapping[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"ok": False, "error": f"unknown tool: {name}"})
        return tool.invoke(arguments)
