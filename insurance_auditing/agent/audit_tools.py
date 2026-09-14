from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import ContractRules
from ..service_matching import ServiceMatcher, normalise_service_description
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


def build_hospital_2_tools(
    data_root: Path,
    contract: ContractRules,
) -> tuple[AgentTool, ...]:
    """Build narrow, read-only semantic-review tools for Hospital 2."""

    root = data_root.resolve()
    contract_path = root / "contracts" / "hospital_2" / "master_services_agreement.md"
    markdown = contract_path.read_text(encoding="utf-8")
    matcher = ServiceMatcher(
        contract,
        minimum_margin=0.15,
        allow_price_tiebreaker=False,
    )

    clauses: dict[str, str] = {}
    for line in markdown.splitlines():
        if " In respect of " not in line:
            continue
        clause_id = line.split(" ", 1)[0]
        if clause_id.replace(".", "").isdigit():
            clauses[clause_id] = line

    def read_contract_clause(arguments: dict[str, Any]) -> dict[str, str]:
        clause_id = str(arguments["clause_id"])
        try:
            clause = clauses[clause_id]
        except KeyError as exc:
            raise ValueError(f"unknown Hospital 2 service clause: {clause_id}") from exc
        return {"clause_id": clause_id, "text": clause}

    def get_match_contexts(arguments: dict[str, Any]) -> list[dict[str, Any]]:
        descriptions = arguments["descriptions"]
        if not isinstance(descriptions, list) or not 1 <= len(descriptions) <= 25:
            raise ValueError("descriptions must contain between 1 and 25 strings")
        contexts: list[dict[str, Any]] = []
        for raw_description in descriptions:
            if not isinstance(raw_description, str) or not raw_description.strip():
                raise ValueError("every description must be a non-empty string")
            normalized = normalise_service_description(raw_description)
            candidates = []
            for score, service_name in matcher.rank_candidates(normalized, limit=5):
                rule = contract.services[service_name]
                candidates.append(
                    {
                        "service_name": service_name,
                        "text_score": round(score, 6),
                        "unit_basis": rule.unit_basis,
                        "clause_id": rule.clause_id,
                        "clause_text": clauses.get(rule.clause_id or "", ""),
                    }
                )
            contexts.append(
                {
                    "normalized_description": normalized,
                    "candidates": candidates,
                }
            )
        return contexts

    def validate_match_proposal(arguments: dict[str, Any]) -> dict[str, Any]:
        description = normalise_service_description(str(arguments["description"]))
        service_name = str(arguments["service_name"])
        candidates = {
            candidate
            for _, candidate in matcher.rank_candidates(description, limit=5)
        }
        return {
            "valid": service_name in candidates,
            "normalized_description": description,
            "service_exists": service_name in contract.services,
            "candidate_services": sorted(candidates),
        }

    def get_invoice_evidence(arguments: dict[str, Any]) -> dict[str, Any]:
        from ..audit import StructuralAuditor
        from ..io import load_hospital

        invoice_id = str(arguments["invoice_id"])
        dataset = load_hospital(root, 2)
        auditor = StructuralAuditor(contract.identity)
        findings = auditor.audit(dataset)
        if invoice_id not in findings:
            raise ValueError(f"unknown Hospital 2 invoice: {invoice_id}")
        assembled = auditor.resolve(dataset)[invoice_id]
        finding = findings[invoice_id]
        return {
            "invoice_id": invoice_id,
            "structural_categories": finding.categories,
            "billed_total_cents": finding.billed_total_cents,
            "line_items_total_cents": finding.expected_total_cents,
            "line_items": [
                {
                    "line_id": item.line_id,
                    "service_date": item.service_date,
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_basis_as_billed": item.unit_basis_as_billed,
                    "unit_price_cents": item.unit_price_cents,
                    "line_total_cents": item.line_total_cents,
                }
                for item in assembled.line_items
            ],
        }

    return (
        AgentTool(
            name="read_hospital_2_contract_clause",
            description="Read one numbered Hospital 2 contracted-service clause.",
            parameters={
                "type": "object",
                "properties": {"clause_id": {"type": "string"}},
                "required": ["clause_id"],
                "additionalProperties": False,
            },
            handler=read_contract_clause,
        ),
        AgentTool(
            name="get_hospital_2_match_contexts",
            description=(
                "Get the five semantically closest contracted services and exact "
                "contract evidence for normalized Hospital 2 descriptions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "descriptions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 25,
                    }
                },
                "required": ["descriptions"],
                "additionalProperties": False,
            },
            handler=get_match_contexts,
        ),
        AgentTool(
            name="validate_hospital_2_match",
            description=(
                "Validate that a proposed service exists and is among the allowed "
                "semantic candidates for a Hospital 2 description."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "service_name": {"type": "string"},
                },
                "required": ["description", "service_name"],
                "additionalProperties": False,
            },
            handler=validate_match_proposal,
        ),
        AgentTool(
            name="get_hospital_2_invoice_evidence",
            description=(
                "Read one Hospital 2 invoice's deterministic structural finding "
                "and its canonical line items for a focused investigation."
            ),
            parameters={
                "type": "object",
                "properties": {"invoice_id": {"type": "string"}},
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
            handler=get_invoice_evidence,
        ),
    )
