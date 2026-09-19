import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from insurance_auditing.contract_parser import load_hospital_5_contract
from insurance_auditing.hospital_5_mappings import load_hospital_5_mapping_artifact
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.submission import (
    SUBMISSION_COLUMNS,
    build_hospital_5_submission_rows,
    write_submission_rows,
)


ROOT = Path(__file__).resolve().parents[1]


class Hospital5SubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract = load_hospital_5_contract(
            ROOT
            / "contracts"
            / "hospital_5"
            / "network_reimbursement_agreement.md"
        )
        mappings, _ = load_hospital_5_mapping_artifact(
            ROOT / "mappings" / "hospital_5_service_mappings.json",
            contract,
        )
        dataset = load_hospital(ROOT, 5)
        result = ContractAuditor(
            contract,
            mappings,
            conservative_matching=True,
        ).audit_detailed(dataset)
        cls.rows = build_hospital_5_submission_rows(result)
        cls.by_invoice = {row["invoice_id"]: row for row in cls.rows}

    def test_covers_every_hospital_5_invoice_identifier_once(self) -> None:
        self.assertEqual(len(self.rows), 1_050)
        self.assertEqual(len(self.by_invoice), 1_050)
        self.assertEqual(sum(row["flagged"] == "1" for row in self.rows), 76)
        self.assertTrue(
            all(row["invoice_id"].startswith("INV-H5-") for row in self.rows)
        )
        self.assertTrue(
            all(
                bool(row["error_category"]) == (row["flagged"] == "1")
                for row in self.rows
            )
        )
        self.assertTrue(all(float(row["confidence"]) <= 0.90 for row in self.rows))
        self.assertTrue(
            all(int(row["expected_total_cents"]) >= 0 for row in self.rows)
        )

    def test_caps_unknown_service_confidence(self) -> None:
        self.assertEqual(self.by_invoice["INV-H5-000034"]["confidence"], "0.70")
        self.assertIn(
            "unknown_service",
            self.by_invoice["INV-H5-000034"]["error_category"],
        )
        self.assertEqual(self.by_invoice["INV-H5-000002"]["confidence"], "0.90")

    def test_writes_template_columns_and_preserves_other_hospitals(self) -> None:
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
            write_submission_rows(
                output,
                self.rows,
                replace_invoice_prefix="INV-H5-",
            )
            with output.open(newline="", encoding="utf-8") as source:
                written = list(csv.DictReader(source))
            self.assertEqual(tuple(written[0]), SUBMISSION_COLUMNS)
            self.assertEqual(written[0]["invoice_id"], "INV-H4-EXISTING")
            self.assertEqual(len(written), 1_051)


if __name__ == "__main__":
    unittest.main()
