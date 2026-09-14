# Internal agent

This package is an internal, tool-using agent for the existing application. It
has no CLI or standalone entry point.

## Structure

- `agent.py`: agent loop and public `run_agent()` callable.
- `client.py`: OpenRouter transport.
- `config.py`: environment-backed configuration.
- `tools.py`: reusable tool definition and registry.
- `audit_tools.py`: read-only tools for contracts, invoices, structural audits,
  and bounded Hospital 2 semantic evidence.
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

Hospital 2 uses this loop only during `prepare-hospital-2-mappings`. The agent
never receives billed prices, invoice identifiers, patient identifiers, the
entire contract, or the complete invoice CSV. The resulting mapping is checked
by deterministic code before it can enter the offline pricing pipeline.
