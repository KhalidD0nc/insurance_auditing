from __future__ import annotations

from pathlib import Path
from typing import Any

from .tools import AgentTool


_READABLE_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt"}


def build_audit_tools(data_root: Path) -> tuple[AgentTool, ...]:
    """Build read-only tools bound to one insurance-auditing workspace."""
    root = data_root.resolve()

    def safe_path(relative_path: str) -> Path:
        candidate = (root / relative_path).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("path must stay inside the data root")
        return candidate

    def read_workspace_file(arguments: dict[str, Any]) -> dict[str, Any]:
        path = safe_path(str(arguments["path"]))
        if path.name == ".env" or path.suffix.lower() not in _READABLE_SUFFIXES:
            raise ValueError("only non-secret text and data files may be read")
        if not path.is_file():
            raise ValueError("file does not exist")
        maximum = int(arguments.get("max_characters", 30_000))
        if not 1 <= maximum <= 100_000:
            raise ValueError("max_characters must be between 1 and 100000")
        content = path.read_text(encoding="utf-8")
        return {
            "path": str(path.relative_to(root)),
            "content": content[:maximum],
            "truncated": len(content) > maximum,
        }

    def run_structural_audit(arguments: dict[str, Any]) -> dict[str, Any]:
        # Imports stay local so pricing.py can safely import the agent package.
        from ..audit import StructuralAuditor
        from ..contracts import contract_for_hospital
        from ..io import load_hospital

        hospital = int(arguments["hospital"])
        if hospital not in range(1, 6):
            raise ValueError("hospital must be between 1 and 5")
        dataset = load_hospital(root, hospital)
        findings = StructuralAuditor(contract_for_hospital(hospital)).audit(dataset)
        return {
            "hospital": hospital,
            "invoice_count": len(findings),
            "flagged_count": sum(finding.flagged for finding in findings.values()),
            "flagged": [
                {
                    "invoice_id": finding.invoice_id,
                    "categories": finding.categories,
                    "billed_total_cents": finding.billed_total_cents,
                    "expected_total_cents": finding.expected_total_cents,
                }
                for finding in findings.values()
                if finding.flagged
            ],
        }

    return (
        AgentTool(
            name="read_workspace_file",
            description=(
                "Read a contract, invoice, label, prompt, or report file beneath "
                "the insurance auditing data root."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Data-root-relative file path.",
                    },
                    "max_characters": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100_000,
                        "default": 30_000,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_workspace_file,
        ),
        AgentTool(
            name="run_structural_audit",
            description="Run deterministic structural checks for one hospital.",
            parameters={
                "type": "object",
                "properties": {
                    "hospital": {"type": "integer", "minimum": 1, "maximum": 5}
                },
                "required": ["hospital"],
                "additionalProperties": False,
            },
            handler=run_structural_audit,
        ),
    )
