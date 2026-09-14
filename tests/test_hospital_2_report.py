from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from insurance_auditing.audit import StructuralAuditor
from insurance_auditing.cli import main
from insurance_auditing.contract_parser import load_hospital_2_contract
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.reporting import build_audit_report


ROOT = Path(__file__).resolve().parents[1]


class Hospital2ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_2_contract(
            ROOT / "contracts" / "hospital_2" / "master_services_agreement.md"
        )
        cls.dataset = load_hospital(ROOT, 2)
        cls.structural = StructuralAuditor(cls.contract.identity).audit(cls.dataset)
        cls.result = ContractAuditor(
            cls.contract, conservative_matching=True
        ).audit_detailed(cls.dataset)
        cls.report = build_audit_report(
            cls.dataset,
            cls.result,
            hide_incomplete_expected_totals=True,
            contract_source_sha256=cls.contract.source_sha256,
            mapping_artifact={
                "schema_version": 1,
                "contract_sha256": cls.contract.source_sha256,
                "prompt_version": "test",
                "prompt_sha256": "0" * 64,
                "model": "test-model",
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "policy": {},
                "mappings": [],
            },
        )

    def test_preserves_every_structural_finding(self) -> None:
        self.assertEqual(sum(f.flagged for f in self.structural.values()), 44)
        for invoice_id, structural_finding in self.structural.items():
            self.assertTrue(
                set(structural_finding.categories)
                <= set(self.result.findings[invoice_id].categories)
            )

    def test_hides_authoritative_total_when_service_is_unresolved(self) -> None:
        invoice = next(
            item
            for item in self.report["invoices"]
            if not item["pricing_complete"]
        )
        self.assertIsNone(invoice["expected_total_cents"])
        self.assertIsNone(invoice["difference_cents"])
        self.assertIn("unknown_service", invoice["categories"])
        unknown_line = next(
            line for line in invoice["line_items"] if line["matched_service"] is None
        )
        self.assertIsNone(unknown_line["expected_unit_price_cents"])
        self.assertIsNone(unknown_line["expected_line_total_cents"])
        self.assertIsNone(unknown_line["difference_cents"])
        self.assertIsInstance(unknown_line["match_key"], str)

    def test_report_contains_contract_and_agent_provenance(self) -> None:
        self.assertEqual(self.report["status"], "unlabelled_preliminary_review")
        self.assertEqual(
            self.report["contract_source_sha256"], self.contract.source_sha256
        )
        self.assertEqual(self.report["agent_mapping"]["model"], "test-model")
        self.assertGreater(self.report["summary"]["unknown_line_items"], 0)

    def test_audit_cli_is_offline_and_writes_the_requested_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "hospital_2_report.json"
            argv = [
                "insurance_auditing",
                "audit-hospital-2",
                "--data-root",
                str(ROOT),
                "--output",
                str(output),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch(
                    "insurance_auditing.agent.client.OpenRouterClient.complete",
                    side_effect=AssertionError("offline audit attempted a network call"),
                ),
                redirect_stdout(io.StringIO()),
            ):
                main()
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
