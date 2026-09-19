from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_5_contract
from insurance_auditing.hospital_5_mappings import load_hospital_5_mapping_artifact
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.reporting import build_audit_report


ROOT = Path(__file__).resolve().parents[1]


class Hospital5ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract = load_hospital_5_contract(
            ROOT
            / "contracts"
            / "hospital_5"
            / "network_reimbursement_agreement.md"
        )
        mappings, artifact = load_hospital_5_mapping_artifact(
            ROOT / "mappings" / "hospital_5_service_mappings.json",
            contract,
        )
        dataset = load_hospital(ROOT, 5)
        result = ContractAuditor(
            contract,
            mappings,
            conservative_matching=True,
        ).audit_detailed(dataset)
        cls.report = build_audit_report(
            dataset,
            result,
            hide_incomplete_expected_totals=True,
            contract_source_sha256=contract.source_sha256,
            mapping_artifact=artifact,
        )

    def test_reports_complete_hospital_5_audit_summary(self) -> None:
        summary = self.report["summary"]
        self.assertEqual(summary["invoice_rows"], 1_057)
        self.assertEqual(summary["unique_invoice_ids"], 1_050)
        self.assertEqual(summary["source_line_items"], 13_221)
        self.assertEqual(summary["canonical_line_items"], 13_126)
        self.assertEqual(summary["historical_only_line_items"], 95)
        self.assertEqual(summary["flagged_invoice_ids"], 76)
        self.assertEqual(summary["unknown_line_items"], 12)
        self.assertEqual(summary["pricing_complete_invoice_ids"], 1_038)
        self.assertEqual(summary["reported_invoice_ids"], 76)

    def test_preserves_uncertainty_for_unknown_services(self) -> None:
        invoice = next(
            item
            for item in self.report["invoices"]
            if item["invoice_id"] == "INV-H5-000034"
        )
        self.assertFalse(invoice["pricing_complete"])
        self.assertIsNone(invoice["expected_total_cents"])
        unknown = next(
            item
            for item in invoice["line_items"]
            if "unknown_service" in item["categories"]
        )
        self.assertIsNone(unknown["expected_unit_price_cents"])
        self.assertIsNone(unknown["expected_line_total_cents"])


if __name__ == "__main__":
    unittest.main()
