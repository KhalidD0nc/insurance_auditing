# Internal OpenRouter agent

This package is an internal, tool-using agent for the existing application. It
has no CLI or standalone entry point.

## Structure

- `agent.py`: agent loop and public `run_agent()` callable.
- `client.py`: OpenRouter transport.
- `config.py`: environment-backed configuration.
- `tools.py`: reusable tool definition and registry.
- `audit_tools.py`: read-only tools for contracts, invoices, and structural audits.
- `prompts.py`: default insurance-auditing instructions.

Set `OPENROUTER_API_KEY` in `insurance_auditing/agent/.env`. The configured model
is `z-ai/glm-5.3-flash`.

Call it from `pricing.py` or another application module:

```python
from pathlib import Path

from .agent import build_audit_tools, run_agent

answer = run_agent(
    "Inspect Hospital 2 and summarize any structural issues.",
    tools=build_audit_tools(Path.cwd()),
)
```

Application-specific functions can also be exposed to the model by constructing
an `AgentTool` with a JSON Schema and a Python handler, then passing it to
`run_agent()`.
