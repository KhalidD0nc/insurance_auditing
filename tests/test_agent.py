from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from insurance_auditing.agent import (
    AgentConfig,
    AgentConfigurationError,
    AgentTool,
    OpenRouterAgent,
    ToolRegistry,
    build_audit_tools,
)
from insurance_auditing.pricing import ContractAuditor


class _FakeClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def complete(self, messages: object, **kwargs: object) -> dict[str, object]:
        self.calls.append(copy.deepcopy({"messages": messages, **kwargs}))
        return self.responses.pop(0)


class AgentConfigTests(unittest.TestCase):
    def test_loads_key_and_requested_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "OPENROUTER_API_KEY=test-key\nOPENROUTER_MODEL=z-ai/glm-5.3-flash\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = AgentConfig.from_env(env_path)

        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.model, "z-ai/glm-5.3-flash")

    def test_missing_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("OPENROUTER_MODEL=z-ai/glm-5.3-flash\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(AgentConfigurationError, "OPENROUTER_API_KEY"):
                    AgentConfig.from_env(env_path)


class ToolTests(unittest.TestCase):
    def test_registry_invokes_a_python_tool(self) -> None:
        tool = AgentTool(
            name="double",
            description="Double an integer.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
            handler=lambda arguments: int(arguments["value"]) * 2,
        )
        result = ToolRegistry([tool]).invoke("double", '{"value": 21}')
        self.assertEqual(result, '{"ok": true, "result": 42}')

    def test_audit_file_tool_blocks_paths_outside_root(self) -> None:
        tools = ToolRegistry(build_audit_tools(Path(__file__).resolve().parents[1]))
        result = tools.invoke("read_workspace_file", '{"path":"../outside.txt"}')
        self.assertIn('"ok": false', result)
        self.assertIn("path must stay inside the data root", result)


class AgentLoopTests(unittest.TestCase):
    def test_executes_tool_call_and_returns_final_text(self) -> None:
        config = AgentConfig(api_key="test-key", max_tool_rounds=2)
        agent = OpenRouterAgent(config)
        fake_client = _FakeClient(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"id":"INV-1"}'},
                        }
                    ],
                },
                {"role": "assistant", "content": "Invoice INV-1 was reviewed."},
            ]
        )
        agent.client = fake_client  # type: ignore[assignment]
        tool = AgentTool(
            name="lookup",
            description="Look up an invoice.",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            handler=lambda arguments: {"invoice_id": arguments["id"]},
        )

        result = agent.run("Review INV-1", tools=[tool])

        self.assertEqual(result, "Invoice INV-1 was reviewed.")
        second_messages = fake_client.calls[1]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")  # type: ignore[index]
        self.assertIn("INV-1", second_messages[-1]["content"])  # type: ignore[index]

    def test_agent_package_can_be_imported_with_pricing(self) -> None:
        self.assertEqual(ContractAuditor.__name__, "ContractAuditor")


if __name__ == "__main__":
    unittest.main()
