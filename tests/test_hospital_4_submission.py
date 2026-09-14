import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from insurance_auditing.contract_parser import load_hospital_4_contract
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.submission import (
    SUBMISSION_COLUMNS,
    build_hospital_4_submission_rows,
    write_submission_rows,
)


ROOT = Path(__file__).resolve().parents[1]


class Hospital4SubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dataset = load_hospital(ROOT, 4)
        contract = load_hospital_4_contract(
            ROOT
            / "contracts"
            / "hospital_4"
            / "conditional_reimbursement_agreement.md"
        )
        result = ContractAuditor(contract).audit_detailed(dataset)
        cls.rows = build_hospital_4_submission_rows(result)
        cls.by_invoice = {row["invoice_id"]: row for row in cls.rows}

    def test_covers_every_hospital_4_invoice_identifier_once(self) -> None:
        self.assertEqual(len(self.rows), 835)
        self.assertEqual(len(self.by_invoice), 835)
        self.assertEqual(sum(row["flagged"] == "1" for row in self.rows), 63)
        self.assertTrue(
            all(row["invoice_id"].startswith("INV-H4-") for row in self.rows)
        )
        self.assertTrue(
            all(
                bool(row["error_category"]) == (row["flagged"] == "1")
                for row in self.rows
            )
        )
        self.assertTrue(
            all(0 < float(row["confidence"]) <= 1 for row in self.rows)
        )
        self.assertTrue(
            all(int(row["expected_total_cents"]) >= 0 for row in self.rows)
        )

    def test_calibrates_known_ambiguity_types(self) -> None:
        self.assertEqual(self.by_invoice["INV-H4-000165"]["confidence"], "0.72")
        self.assertEqual(self.by_invoice["INV-H4-000125"]["confidence"], "0.92")
        self.assertEqual(self.by_invoice["INV-H4-000724"]["confidence"], "0.84")
        self.assertEqual(self.by_invoice["INV-H4-000002"]["confidence"], "0.95")
        self.assertEqual(self.by_invoice["INV-H4-000001"]["confidence"], "0.97")

    def test_writes_template_columns_and_preserves_other_hospitals(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "submission.csv"
            with output.open("w", newline="", encoding="utf-8") as destination:
                writer = csv.DictWriter(destination, fieldnames=SUBMISSION_COLUMNS)
                writer.writeheader()
                writer.writerow(
                    {
                        "invoice_id": "INV-H2-EXISTING",
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
                replace_invoice_prefix="INV-H4-",
            )

            with output.open(newline="", encoding="utf-8") as source:
                written = list(csv.DictReader(source))
            self.assertEqual(tuple(written[0]), SUBMISSION_COLUMNS)
            self.assertEqual(written[0]["invoice_id"], "INV-H2-EXISTING")
            self.assertEqual(len(written), 836)


if __name__ == "__main__":
    unittest.main()
