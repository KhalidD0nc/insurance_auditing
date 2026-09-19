from __future__ import annotations

import csv
import os
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import AuditFinding, DetailedAuditResult, LineAuditDetail


SUBMISSION_COLUMNS = (
    "invoice_id",
    "flagged",
    "error_category",
    "expected_total_cents",
    "billed_total_cents",
    "confidence",
)


def _confidence(
    finding: AuditFinding,
    details: tuple[LineAuditDetail, ...],
    *,
    maximum: Decimal = Decimal("1"),
) -> Decimal:
    confidence = Decimal("0.96") if finding.flagged else Decimal("0.97")
    categories = set(finding.categories)

    if "unknown_service" in categories:
        confidence = min(confidence, Decimal("0.92"))
    if "malformed_service_date" in categories:
        confidence = min(confidence, Decimal("0.88"))
    if "duplicate_invoice_id" in categories:
        confidence = min(confidence, Decimal("0.94"))
    if "daily_cap_exceeded" in categories:
        confidence = min(confidence, Decimal("0.72"))

    weak_matched_details = [
        detail
        for detail in details
        if detail.matched_service is not None
        and (detail.match_score < 0.60 or detail.match_margin < 0.15)
    ]
    if finding.flagged and any(detail.categories for detail in weak_matched_details):
        confidence = min(confidence, Decimal("0.84"))
    elif not finding.flagged and weak_matched_details:
        confidence = min(confidence, Decimal("0.95"))

    return min(confidence, maximum)


def _submission_row(
    finding: AuditFinding,
    details: tuple[LineAuditDetail, ...],
    *,
    maximum_confidence: Decimal = Decimal("1"),
) -> dict[str, str]:
    return {
        "invoice_id": finding.invoice_id,
        "flagged": "1" if finding.flagged else "0",
        "error_category": "|".join(finding.categories),
        "expected_total_cents": str(finding.expected_total_cents),
        "billed_total_cents": str(finding.billed_total_cents),
        "confidence": format(
            _confidence(finding, details, maximum=maximum_confidence),
            ".2f",
        ),
    }


def build_hospital_2_submission_rows(
    result: DetailedAuditResult,
) -> list[dict[str, str]]:
    """Build one row for every Hospital 2 invoice identifier.

    Unknown descriptions remain explicit findings. Their line totals preserve
    the arithmetically calculated billed amount because the contract does not
    provide a defensible replacement rate, and their confidence is reduced.
    """

    rows = []
    for finding in sorted(
        result.findings.values(),
        key=lambda item: item.canonical_invoice_row,
    ):
        details = result.line_details.get(finding.invoice_id, ())
        maximum_confidence = (
            Decimal("0.70")
            if "unknown_service" in finding.categories
            else Decimal("0.90")
        )
        rows.append(
            _submission_row(
                finding,
                details,
                maximum_confidence=maximum_confidence,
            )
        )
    return rows


def build_hospital_4_submission_rows(
    result: DetailedAuditResult,
) -> list[dict[str, str]]:
    rows = []
    for finding in sorted(
        result.findings.values(),
        key=lambda item: item.canonical_invoice_row,
    ):
        details = result.line_details.get(finding.invoice_id, ())
        rows.append(_submission_row(finding, details))
    return rows


def build_hospital_3_submission_rows(
    result: DetailedAuditResult,
) -> list[dict[str, str]]:
    """Build complete Hospital 3 coverage with conservative uncertainty caps."""

    rows = []
    for finding in sorted(
        result.findings.values(),
        key=lambda item: item.canonical_invoice_row,
    ):
        details = result.line_details.get(finding.invoice_id, ())
        if "unknown_service" in finding.categories:
            maximum_confidence = Decimal("0.70")
        elif "service_not_contracted_on_date" in finding.categories:
            maximum_confidence = Decimal("0.85")
        else:
            maximum_confidence = Decimal("0.90")
        rows.append(
            _submission_row(
                finding,
                details,
                maximum_confidence=maximum_confidence,
            )
        )
    return rows


def build_hospital_5_submission_rows(
    result: DetailedAuditResult,
) -> list[dict[str, str]]:
    """Build complete Hospital 5 coverage with conservative uncertainty caps."""

    rows = []
    for finding in sorted(
        result.findings.values(),
        key=lambda item: item.canonical_invoice_row,
    ):
        details = result.line_details.get(finding.invoice_id, ())
        maximum_confidence = (
            Decimal("0.70")
            if "unknown_service" in finding.categories
            else Decimal("0.90")
        )
        rows.append(
            _submission_row(
                finding,
                details,
                maximum_confidence=maximum_confidence,
            )
        )
    return rows


def write_submission_rows(
    path: Path | str,
    rows: list[dict[str, str]],
    *,
    replace_invoice_prefix: str,
) -> None:
    destination = Path(path)
    preserved_rows: list[dict[str, str]] = []
    if destination.exists():
        with destination.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source, skipinitialspace=True)
            fieldnames = tuple(name.strip() for name in (reader.fieldnames or ()))
            if fieldnames != SUBMISSION_COLUMNS:
                raise ValueError(
                    f"existing submission columns must be {SUBMISSION_COLUMNS!r}, "
                    f"found {fieldnames!r}"
                )
            for source_row in reader:
                row = {
                    key.strip(): (value or "").strip()
                    for key, value in source_row.items()
                }
                if not row["invoice_id"].startswith(replace_invoice_prefix):
                    preserved_rows.append(row)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            writer = csv.DictWriter(temporary, fieldnames=SUBMISSION_COLUMNS)
            writer.writeheader()
            writer.writerows(preserved_rows)
            writer.writerows(rows)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
