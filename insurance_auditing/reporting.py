from __future__ import annotations

from collections import Counter
from dataclasses import asdict

from .models import DetailedAuditResult, HospitalDataset


def build_audit_report(
    dataset: HospitalDataset,
    result: DetailedAuditResult,
    *,
    include_correct: bool = False,
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
        line_items = []
        for detail in details:
            payload = asdict(detail)
            payload["flagged"] = bool(detail.categories)
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
                "expected_total_cents": finding.expected_total_cents,
                "difference_cents": (
                    finding.billed_total_cents - finding.expected_total_cents
                ),
                "line_items": line_items,
            }
        )

    all_details = [
        detail
        for details in result.line_details.values()
        for detail in details
    ]
    return {
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
            "category_counts": dict(sorted(category_counts.items())),
        },
        "invoices": invoices,
    }
