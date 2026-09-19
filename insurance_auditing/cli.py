from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .audit import StructuralAuditor
from .contracts import contract_for_hospital
from .contract_parser import (
    load_hospital_1_contract,
    load_hospital_2_contract,
    load_hospital_4_contract,
)
from .evaluation import (
    evaluate_categories,
    evaluate_flags,
    load_development_labels,
    load_labels,
    metrics_as_dict,
)
from .io import load_hospital
from .hospital_2_mappings import (
    DEFAULT_MAPPING_PATH,
    load_hospital_2_mapping_artifact,
    prepare_hospital_2_mappings,
    write_json_atomic,
)
from .pricing import ContractAuditor
from .reporting import build_audit_report
from .submission import (
    build_hospital_2_submission_rows,
    build_hospital_4_submission_rows,
    write_submission_rows,
)


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
    hospital_4 = subparsers.add_parser(
        "audit-hospital-4",
        help="produce a line-level preliminary review report for Hospital 4",
    )
    hospital_4.add_argument("--data-root", type=Path, default=Path.cwd())
    hospital_4.add_argument(
        "--include-correct",
        action="store_true",
        help="include invoices with no detected issue in the report",
    )
    hospital_4_submission = subparsers.add_parser(
        "generate-hospital-4-submission",
        help="write reviewed Hospital 4 predictions to the submission CSV",
    )
    hospital_4_submission.add_argument("--data-root", type=Path, default=Path.cwd())
    hospital_4_submission.add_argument(
        "--output",
        type=Path,
        default=Path("submission.csv"),
    )
    hospital_2_mappings = subparsers.add_parser(
        "prepare-hospital-2-mappings",
        help="classify ambiguous Hospital 2 descriptions with the audit agent",
    )
    hospital_2_mappings.add_argument("--data-root", type=Path, default=Path.cwd())
    hospital_2_mappings.add_argument("--output", type=Path, default=DEFAULT_MAPPING_PATH)
    hospital_2_mappings.add_argument("--refresh", action="store_true")
    hospital_2_mappings.add_argument("--batch-size", type=int, default=20)
    hospital_2 = subparsers.add_parser(
        "audit-hospital-2",
        help="produce an offline line-level Hospital 2 review report",
    )
    hospital_2.add_argument("--data-root", type=Path, default=Path.cwd())
    hospital_2.add_argument("--mappings", type=Path, default=DEFAULT_MAPPING_PATH)
    hospital_2.add_argument(
        "--output", type=Path, default=Path("hospital_2_audit_report.json")
    )
    hospital_2.add_argument("--include-correct", action="store_true")
    hospital_2_submission = subparsers.add_parser(
        "generate-hospital-2-submission",
        help="write complete Hospital 2 predictions to the submission CSV",
    )
    hospital_2_submission.add_argument("--data-root", type=Path, default=Path.cwd())
    hospital_2_submission.add_argument(
        "--mappings", type=Path, default=DEFAULT_MAPPING_PATH
    )
    hospital_2_submission.add_argument(
        "--output",
        type=Path,
        default=Path("submission.csv"),
    )
    return parser


def _beneath_data_root(data_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else data_root / path


def main() -> None:
    args = _parser().parse_args()
    if args.command == "prepare-hospital-2-mappings":
        contract_path = (
            args.data_root
            / "contracts"
            / "hospital_2"
            / "master_services_agreement.md"
        )
        contract = load_hospital_2_contract(contract_path)
        output_path = _beneath_data_root(args.data_root, args.output)
        artifact = prepare_hospital_2_mappings(
            args.data_root,
            contract,
            output_path,
            refresh=args.refresh,
            batch_size=args.batch_size,
        )
        print(
            json.dumps(
                {
                    "hospital": 2,
                    "output": str(output_path.resolve()),
                    "accepted": sum(
                        entry["status"] == "accepted"
                        for entry in artifact["mappings"]
                    ),
                    "unresolved": sum(
                        entry["status"] == "unresolved"
                        for entry in artifact["mappings"]
                    ),
                    "model": artifact["model"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.command == "audit-hospital-2":
        contract_path = (
            args.data_root
            / "contracts"
            / "hospital_2"
            / "master_services_agreement.md"
        )
        contract = load_hospital_2_contract(contract_path)
        mapping_path = _beneath_data_root(args.data_root, args.mappings)
        mappings, artifact = load_hospital_2_mapping_artifact(
            mapping_path, contract
        )
        dataset = load_hospital(args.data_root, 2)
        result = ContractAuditor(
            contract,
            mappings,
            conservative_matching=True,
        ).audit_detailed(dataset)
        report = build_audit_report(
            dataset,
            result,
            include_correct=args.include_correct,
            hide_incomplete_expected_totals=True,
            contract_source_sha256=contract.source_sha256,
            mapping_artifact=artifact,
        )
        output_path = _beneath_data_root(args.data_root, args.output)
        write_json_atomic(output_path, report)
        print(
            json.dumps(
                {
                    "hospital": 2,
                    "output": str(output_path.resolve()),
                    **report["summary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.command == "generate-hospital-2-submission":
        contract_path = (
            args.data_root
            / "contracts"
            / "hospital_2"
            / "master_services_agreement.md"
        )
        contract = load_hospital_2_contract(contract_path)
        mapping_path = _beneath_data_root(args.data_root, args.mappings)
        mappings, _ = load_hospital_2_mapping_artifact(mapping_path, contract)
        dataset = load_hospital(args.data_root, 2)
        result = ContractAuditor(
            contract,
            mappings,
            conservative_matching=True,
        ).audit_detailed(dataset)
        rows = build_hospital_2_submission_rows(result)
        unknown_service_lines = sum(
            detail.matched_service is None
            for details in result.line_details.values()
            for detail in details
        )
        write_submission_rows(
            args.output,
            rows,
            replace_invoice_prefix="INV-H2-",
        )
        print(
            json.dumps(
                {
                    "hospital": 2,
                    "output": str(args.output.resolve()),
                    "rows": len(rows),
                    "flagged": sum(row["flagged"] == "1" for row in rows),
                    "unknown_service_lines": unknown_service_lines,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if args.command == "evaluate-hospital-1":
        contract_path = (
            args.data_root / "contracts" / "hospital_1" / "provider_services_agreement.md"
        )
        label_path = args.data_root / "labels" / "hospital_1_labels.csv"
        dataset = load_hospital(args.data_root, 1)
        findings = ContractAuditor(load_hospital_1_contract(contract_path)).audit(dataset)
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

    if args.command == "audit-hospital-4":
        contract_path = (
            args.data_root
            / "contracts"
            / "hospital_4"
            / "conditional_reimbursement_agreement.md"
        )
        dataset = load_hospital(args.data_root, 4)
        result = ContractAuditor(
            load_hospital_4_contract(contract_path)
        ).audit_detailed(dataset)
        report = build_audit_report(
            dataset,
            result,
            include_correct=args.include_correct,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    if args.command == "generate-hospital-4-submission":
        contract_path = (
            args.data_root
            / "contracts"
            / "hospital_4"
            / "conditional_reimbursement_agreement.md"
        )
        dataset = load_hospital(args.data_root, 4)
        result = ContractAuditor(
            load_hospital_4_contract(contract_path)
        ).audit_detailed(dataset)
        rows = build_hospital_4_submission_rows(result)
        write_submission_rows(
            args.output,
            rows,
            replace_invoice_prefix="INV-H4-",
        )
        print(
            json.dumps(
                {
                    "hospital": 4,
                    "output": str(args.output.resolve()),
                    "rows": len(rows),
                    "flagged": sum(row["flagged"] == "1" for row in rows),
                },
                indent=2,
                sort_keys=True,
            )
        )
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
