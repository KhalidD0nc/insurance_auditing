from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import AuditFinding


@dataclass(frozen=True, slots=True)
class BinaryMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True, slots=True)
class DevelopmentLabel:
    invoice_id: str
    is_erroneous: bool
    error_categories: frozenset[str]
    expected_total_cents: int
    ambiguity_sensitive: bool


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def load_labels(path: Path | str) -> dict[str, bool]:
    return {
        invoice_id: label.is_erroneous
        for invoice_id, label in load_development_labels(path).items()
    }


def load_development_labels(path: Path | str) -> dict[str, DevelopmentLabel]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        labels: dict[str, DevelopmentLabel] = {}
        for raw in reader:
            row = {
                key.strip(): value.strip()
                for key, value in raw.items()
                if key is not None and value is not None
            }
            invoice_id = row["invoice_id"]
            labels[invoice_id] = DevelopmentLabel(
                invoice_id=invoice_id,
                is_erroneous=bool(int(row["is_erroneous"])),
                error_categories=frozenset(filter(None, row["error_categories"].split("|"))),
                expected_total_cents=int(row["expected_total_cents"]),
                ambiguity_sensitive=bool(int(row["ambiguity_sensitive"])),
            )
        return labels


def evaluate_flags(findings: dict[str, AuditFinding], labels: dict[str, bool]) -> BinaryMetrics:
    missing = labels.keys() - findings.keys()
    extra = findings.keys() - labels.keys()
    if missing or extra:
        raise ValueError(
            f"prediction/label ID mismatch: missing={len(missing)}, extra={len(extra)}"
        )

    tp = sum(findings[invoice_id].flagged and label for invoice_id, label in labels.items())
    fp = sum(findings[invoice_id].flagged and not label for invoice_id, label in labels.items())
    fn = sum(not findings[invoice_id].flagged and label for invoice_id, label in labels.items())
    tn = sum(not findings[invoice_id].flagged and not label for invoice_id, label in labels.items())
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return BinaryMetrics(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1=_ratio(2 * precision * recall, precision + recall),
    )


def metrics_as_dict(metrics: BinaryMetrics) -> dict[str, int | float]:
    return asdict(metrics)


def evaluate_categories(
    findings: dict[str, AuditFinding],
    labels: dict[str, DevelopmentLabel],
) -> dict[str, BinaryMetrics]:
    categories = sorted(
        {category for label in labels.values() for category in label.error_categories}
        | {category for finding in findings.values() for category in finding.categories}
    )
    results: dict[str, BinaryMetrics] = {}
    for category in categories:
        category_labels = {
            invoice_id: category in label.error_categories
            for invoice_id, label in labels.items()
        }
        category_findings = {
            invoice_id: AuditFinding(
                invoice_id=invoice_id,
                categories=(category,) if category in finding.categories else (),
                billed_total_cents=finding.billed_total_cents,
                expected_total_cents=finding.expected_total_cents,
                canonical_invoice_row=finding.canonical_invoice_row,
            )
            for invoice_id, finding in findings.items()
        }
        results[category] = evaluate_flags(category_findings, category_labels)
    return results
