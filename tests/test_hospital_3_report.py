from pathlib import Path
import unittest

from insurance_auditing.audit import StructuralAuditor
from insurance_auditing.contract_parser import load_hospital_3_contract
from insurance_auditing.hospital_3_mappings import load_hospital_3_mapping_artifact
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.reporting import build_audit_report


ROOT = Path(__file__).resolve().parents[1]


class Hospital3ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_3_contract(
            ROOT / "contracts" / "hospital_3"
        )
        mappings, artifact = load_hospital_3_mapping_artifact(
            ROOT / "mappings" / "hospital_3_service_mappings.json",
            cls.contract,
        )
        cls.dataset = load_hospital(ROOT, 3)
        cls.structural = StructuralAuditor(cls.contract.identity).audit(cls.dataset)
        cls.result = ContractAuditor(
            cls.contract,
            mappings,
            conservative_matching=True,
        ).audit_detailed(cls.dataset)
        cls.report = build_audit_report(
            cls.dataset,
            cls.result,
            hide_incomplete_expected_totals=True,
            contract_source_sha256=cls.contract.source_sha256,
            mapping_artifact=artifact,
        )

    def test_preserves_structural_findings_and_expected_summary(self) -> None:
        for invoice_id, structural in self.structural.items():
            self.assertTrue(
                set(structural.categories)
                <= set(self.result.findings[invoice_id].categories)
            )
        summary = self.report["summary"]
        self.assertEqual(summary["invoice_rows"], 939)
        self.assertEqual(summary["unique_invoice_ids"], 932)
        self.assertEqual(summary["source_line_items"], 11_655)
        self.assertEqual(summary["canonical_line_items"], 11_541)
        self.assertEqual(summary["historical_only_line_items"], 114)
        self.assertEqual(summary["flagged_invoice_ids"], 70)
        self.assertEqual(summary["unknown_line_items"], 14)

    def test_report_exposes_amendment_findings_and_hides_unknown_totals(self) -> None:
        self.assertEqual(
            self.report["summary"]["category_counts"][
                "contract_amendment_rate_mismatch"
            ],
            4,
        )
        invoice = next(
            item for item in self.report["invoices"] if not item["pricing_complete"]
        )
        self.assertIsNone(invoice["expected_total_cents"])
        self.assertIn("unknown_service", invoice["categories"])

    def test_report_contains_merged_contract_and_mapping_provenance(self) -> None:
        self.assertEqual(
            self.report["contract_source_sha256"],
            self.contract.source_sha256,
        )
        self.assertEqual(self.report["agent_mapping"]["schema_version"], 1)
        self.assertEqual(
            self.report["agent_mapping"]["contract_sha256"],
            self.contract.source_sha256,
        )


if __name__ == "__main__":
    unittest.main()
