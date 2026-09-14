from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .models import DetailedAuditResult, HospitalDataset


def build_audit_report(
    dataset: HospitalDataset,
    result: DetailedAuditResult,
    *,
    include_correct: bool = False,
    hide_incomplete_expected_totals: bool = False,
    contract_source_sha256: str | None = None,
    mapping_artifact: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    category_counts = Counter(
        category
        for finding in result.findings.values()
        for category in finding.categories
    )
    ordered_findings = sorted(
        result.findings.values(),
        key=lambda finding: finding.canonical_invoice_row,
    )
    selected = [
        finding
        for finding in ordered_findings
        if include_correct or finding.flagged
    ]

    invoices = []
    for finding in selected:
        details = result.line_details.get(finding.invoice_id, ())
        pricing_complete = all(
            detail.matched_service is not None for detail in details
        )
        line_items = []
        for detail in details:
            payload = asdict(detail)
            payload["flagged"] = bool(detail.categories)
            if hide_incomplete_expected_totals and detail.matched_service is None:
                payload["expected_unit_price_cents"] = None
                payload["expected_line_total_cents"] = None
                payload["difference_cents"] = None
                payload["rate_calculation"] = []
            else:
                payload["difference_cents"] = (
                    detail.billed_line_total_cents - detail.expected_line_total_cents
                )
            line_items.append(payload)
        invoices.append(
            {
                "invoice_id": finding.invoice_id,
                "flagged": finding.flagged,
                "categories": list(finding.categories),
                "billed_total_cents": finding.billed_total_cents,
                "pricing_complete": pricing_complete,
                "expected_total_cents": (
                    finding.expected_total_cents
                    if pricing_complete or not hide_incomplete_expected_totals
                    else None
                ),
                "difference_cents": (
                    finding.billed_total_cents - finding.expected_total_cents
                    if pricing_complete or not hide_incomplete_expected_totals
                    else None
                ),
                "line_items": line_items,
            }
        )

    all_details = [
        detail
        for details in result.line_details.values()
        for detail in details
    ]
    report: dict[str, object] = {
        "status": "unlabelled_preliminary_review",
        "hospital": dataset.hospital_number,
        "summary": {
            "invoice_rows": len(dataset.invoices),
            "unique_invoice_ids": len(result.findings),
            "source_line_items": len(dataset.line_items),
            "canonical_line_items": len(all_details),
            "historical_only_line_items": len(dataset.line_items) - len(all_details),
            "reported_invoice_ids": len(invoices),
            "flagged_invoice_ids": sum(
                finding.flagged for finding in result.findings.values()
            ),
            "matched_line_items": sum(
                detail.matched_service is not None for detail in all_details
            ),
            "unknown_line_items": sum(
                detail.matched_service is None for detail in all_details
            ),
            "pricing_complete_invoice_ids": sum(
                all(detail.matched_service is not None for detail in details)
                for details in result.line_details.values()
            ),
            "category_counts": dict(sorted(category_counts.items())),
        },
        "invoices": invoices,
    }
    if contract_source_sha256 is not None:
        report["contract_source_sha256"] = contract_source_sha256
    if mapping_artifact is not None:
        report["agent_mapping"] = {
            "schema_version": mapping_artifact.get("schema_version"),
            "contract_sha256": mapping_artifact.get("contract_sha256"),
            "prompt_version": mapping_artifact.get("prompt_version"),
            "prompt_sha256": mapping_artifact.get("prompt_sha256"),
            "model": mapping_artifact.get("model"),
            "generated_at_utc": mapping_artifact.get("generated_at_utc"),
            "policy": mapping_artifact.get("policy"),
            "mappings": mapping_artifact.get("mappings", []),
        }
    return report
