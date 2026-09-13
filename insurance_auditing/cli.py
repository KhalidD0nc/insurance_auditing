from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .audit import StructuralAuditor
from .contracts import contract_for_hospital
from .contract_parser import load_hospital_1_contract
from .evaluation import (
    evaluate_categories,
    evaluate_flags,
    load_development_labels,
    load_labels,
    metrics_as_dict,
)
from .io import load_hospital
from .pricing import Hospital1Auditor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit hospital invoices against contract rules")
    subparsers = parser.add_subparsers(dest="command", required=True)
    structural = subparsers.add_parser(
        "structural-audit",
        help="run high-precision checks that do not require service matching",
    )
    structural.add_argument("--hospital", type=int, required=True, choices=range(1, 6))
    structural.add_argument("--data-root", type=Path, default=Path.cwd())
    structural.add_argument(
        "--labels",
        type=Path,
        help="optional labels CSV for evaluation (for example Hospital 1)",
    )
    full = subparsers.add_parser(
        "evaluate-hospital-1",
        help="parse the Hospital 1 contract, reprice invoices, and evaluate against labels",
    )
    full.add_argument("--data-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "evaluate-hospital-1":
        contract_path = (
            args.data_root / "contracts" / "hospital_1" / "provider_services_agreement.md"
        )
        label_path = args.data_root / "labels" / "hospital_1_labels.csv"
        dataset = load_hospital(args.data_root, 1)
        findings = Hospital1Auditor(load_hospital_1_contract(contract_path)).audit(dataset)
        development_labels = load_development_labels(label_path)
        binary_labels = {
            invoice_id: label.is_erroneous
            for invoice_id, label in development_labels.items()
        }
        category_metrics = evaluate_categories(findings, development_labels)
        absolute_errors = [
            abs(findings[invoice_id].expected_total_cents - label.expected_total_cents)
            for invoice_id, label in development_labels.items()
        ]
        output = {
            "hospital": 1,
            "invoice_level": metrics_as_dict(evaluate_flags(findings, binary_labels)),
            "expected_totals": {
                "exact": sum(error == 0 for error in absolute_errors),
                "total": len(absolute_errors),
                "exact_rate": sum(error == 0 for error in absolute_errors) / len(absolute_errors),
                "mean_absolute_error_cents": sum(absolute_errors) / len(absolute_errors),
            },
            "per_category": {
                category: metrics_as_dict(metrics)
                for category, metrics in category_metrics.items()
            },
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return

    if args.command != "structural-audit":
        raise AssertionError(f"unhandled command: {args.command}")

    dataset = load_hospital(args.data_root, args.hospital)
    findings = StructuralAuditor(contract_for_hospital(args.hospital)).audit(dataset)
    category_counts = Counter(
        category for finding in findings.values() for category in finding.categories
    )
    output: dict[str, object] = {
        "hospital": args.hospital,
        "invoice_rows": len(dataset.invoices),
        "unique_invoice_ids": len(findings),
        "line_items": len(dataset.line_items),
        "flagged_invoice_ids": sum(finding.flagged for finding in findings.values()),
        "category_counts": dict(sorted(category_counts.items())),
    }
    if args.labels:
        output["evaluation"] = metrics_as_dict(
            evaluate_flags(findings, load_labels(args.labels))
        )
    print(json.dumps(output, indent=2, sort_keys=True))
