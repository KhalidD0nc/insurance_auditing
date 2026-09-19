from contextlib import redirect_stdout
import csv
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from insurance_auditing.cli import main
from insurance_auditing.contract_parser import load_hospital_2_contract
from insurance_auditing.hospital_2_mappings import load_hospital_2_mapping_artifact
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.submission import (
    SUBMISSION_COLUMNS,
    build_hospital_2_submission_rows,
    write_submission_rows,
)


ROOT = Path(__file__).resolve().parents[1]


class Hospital2SubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract = load_hospital_2_contract(
            ROOT / "contracts" / "hospital_2" / "master_services_agreement.md"
        )
        mappings, _ = load_hospital_2_mapping_artifact(
            ROOT / "mappings" / "hospital_2_service_mappings.json",
            contract,
        )
        dataset = load_hospital(ROOT, 2)
        cls.result = ContractAuditor(
            contract,
            mappings,
            conservative_matching=True,
        ).audit_detailed(dataset)
        cls.rows = build_hospital_2_submission_rows(cls.result)
        cls.by_invoice = {row["invoice_id"]: row for row in cls.rows}

    def test_includes_every_hospital_2_invoice_identifier(self) -> None:
        self.assertEqual(len(self.rows), 1125)
        self.assertEqual(set(self.by_invoice), set(self.result.findings))
        self.assertEqual(sum(row["flagged"] == "1" for row in self.rows), 76)
        self.assertTrue(
            all(row["invoice_id"].startswith("INV-H2-") for row in self.rows)
        )
        self.assertTrue(
            all(
                bool(row["error_category"]) == (row["flagged"] == "1")
                for row in self.rows
            )
        )
        self.assertTrue(
            all(0 < float(row["confidence"]) <= 0.90 for row in self.rows)
        )

    def test_includes_unknown_services_with_explicit_low_confidence(self) -> None:
        unknown_ids = {
            invoice_id
            for invoice_id, details in self.result.line_details.items()
            if any(detail.matched_service is None for detail in details)
        }

        self.assertEqual(len(unknown_ids), 13)
        self.assertTrue(unknown_ids <= set(self.by_invoice))
        for invoice_id in unknown_ids:
            row = self.by_invoice[invoice_id]
            self.assertEqual(row["flagged"], "1")
            self.assertIn("unknown_service", row["error_category"])
            self.assertLessEqual(float(row["confidence"]), 0.70)
            self.assertTrue(row["expected_total_cents"])

    def test_caps_unlabelled_hospital_confidence(self) -> None:
        self.assertEqual(self.by_invoice["INV-H2-000002"]["confidence"], "0.90")
        self.assertEqual(self.by_invoice["INV-H2-000561"]["confidence"], "0.88")
        self.assertEqual(self.by_invoice["INV-H2-000595"]["confidence"], "0.72")

    def test_replaces_hospital_2_rows_and_preserves_other_hospitals(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "submission.csv"
            with output.open("w", newline="", encoding="utf-8") as destination:
                writer = csv.DictWriter(destination, fieldnames=SUBMISSION_COLUMNS)
                writer.writeheader()
                writer.writerow(
                    {
                        "invoice_id": "INV-H4-EXISTING",
                        "flagged": "0",
                        "error_category": "",
                        "expected_total_cents": "100",
                        "billed_total_cents": "100",
                        "confidence": "0.80",
                    }
                )
                writer.writerow(
                    {
                        "invoice_id": "INV-H2-STALE",
                        "flagged": "1",
                        "error_category": "stale",
                        "expected_total_cents": "0",
                        "billed_total_cents": "100",
                        "confidence": "0.10",
                    }
                )

            write_submission_rows(
                output,
                self.rows,
                replace_invoice_prefix="INV-H2-",
            )

            with output.open(newline="", encoding="utf-8") as source:
                written = list(csv.DictReader(source))
            self.assertEqual(tuple(written[0]), SUBMISSION_COLUMNS)
            self.assertEqual(written[0]["invoice_id"], "INV-H4-EXISTING")
            self.assertNotIn("INV-H2-STALE", {row["invoice_id"] for row in written})
            self.assertEqual(len(written), 1126)

    def test_submission_cli_is_offline_and_writes_complete_coverage(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "submission.csv"
            argv = [
                "insurance_auditing",
                "generate-hospital-2-submission",
                "--data-root",
                str(ROOT),
                "--output",
                str(output),
            ]
            stdout = io.StringIO()
            with (
                patch.object(sys, "argv", argv),
                patch(
                    "insurance_auditing.agent.client.OpenRouterClient.complete",
                    side_effect=AssertionError(
                        "submission generation attempted a network call"
                    ),
                ),
                redirect_stdout(stdout),
            ):
                main()

            with output.open(newline="", encoding="utf-8") as source:
                written = list(csv.DictReader(source))
            self.assertEqual(written, self.rows)
            self.assertIn('"rows": 1125', stdout.getvalue())
            self.assertIn('"unknown_service_lines": 14', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
