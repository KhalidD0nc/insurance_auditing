import json
from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_4_contract
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.reporting import build_audit_report


ROOT = Path(__file__).resolve().parents[1]


class Hospital4ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_hospital(ROOT, 4)
        contract = load_hospital_4_contract(
            ROOT
            / "contracts"
            / "hospital_4"
            / "conditional_reimbursement_agreement.md"
        )
        cls.result = ContractAuditor(contract).audit_detailed(cls.dataset)
        cls.report = build_audit_report(cls.dataset, cls.result)

    def test_builds_json_serializable_flagged_invoice_report(self) -> None:
        summary = self.report["summary"]
        self.assertEqual(self.report["status"], "unlabelled_preliminary_review")
        self.assertEqual(summary["invoice_rows"], 840)
        self.assertEqual(summary["unique_invoice_ids"], 835)
        self.assertEqual(summary["source_line_items"], 10_560)
        self.assertEqual(summary["canonical_line_items"], 10_510)
        self.assertEqual(summary["historical_only_line_items"], 50)
        self.assertEqual(
            summary["reported_invoice_ids"],
            summary["flagged_invoice_ids"],
        )
        self.assertTrue(all(invoice["flagged"] for invoice in self.report["invoices"]))
        json.dumps(self.report)

    def test_real_cap_finding_contains_auditable_line_calculation(self) -> None:
        invoice = next(
            invoice
            for invoice in self.report["invoices"]
            if invoice["invoice_id"] == "INV-H4-000165"
        )
        line = next(
            line
            for line in invoice["line_items"]
            if line["line_id"] == "H4-L00165-05"
        )

        self.assertEqual(line["matched_service"], "Extended Hepatic Transfusion Service")
        self.assertEqual(line["billed_quantity"], 13)
        self.assertEqual(line["daily_cap"], 8)
        self.assertEqual(line["expected_quantity"], 8)
        self.assertEqual(line["expected_line_total_cents"], 67_600)
        self.assertIn("daily_cap_exceeded", line["categories"])

    def test_can_include_unflagged_invoices_for_complete_review(self) -> None:
        complete_report = build_audit_report(
            self.dataset,
            self.result,
            include_correct=True,
        )
        self.assertEqual(
            complete_report["summary"]["reported_invoice_ids"],
            835,
        )


if __name__ == "__main__":
    unittest.main()
