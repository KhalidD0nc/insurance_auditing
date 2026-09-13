from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from insurance_auditing.audit import StructuralAuditor
from insurance_auditing.io import load_hospital
from insurance_auditing.models import ContractIdentity


INVOICE_HEADER = (
    "invoice_id,hospital_id,contract_number,invoice_date,patient_id,facility_code,"
    "plan_tier,admission_date,discharge_date,invoice_total_cents\n"
)
LINE_HEADER = (
    "line_id,invoice_id,line_no,service_date,description,quantity,"
    "unit_basis_as_billed,unit_price_cents,line_total_cents\n"
)


class StructuralAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "invoices").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, invoices: str, lines: str) -> None:
        (self.root / "invoices" / "hospital_1_invoices.csv").write_text(
            invoices, encoding="utf-8"
        )
        (self.root / "invoices" / "hospital_1_line_items.csv").write_text(
            lines, encoding="utf-8"
        )

    def _audit(self):
        contract = ContractIdentity(
            1, "H1", "EXPECTED", date(2024, 1, 1), date(2025, 12, 31)
        )
        return StructuralAuditor(contract).audit(load_hospital(self.root, 1))

    def test_strips_padded_csv_headers_and_values(self) -> None:
        invoices = INVOICE_HEADER.replace(",", " , ") + (
            " INV-1 , H1 , EXPECTED , 2024-01-03 , P1 , F-MAIN , GOLD , "
            "2024-01-01 , 2024-01-02 , 200 \n"
        )
        lines = LINE_HEADER.replace(",", " , ") + (
            " L1 , INV-1 , 1 , 2024-01-02 , Service , 2 , per_item , 100 , 200 \n"
        )
        self._write(invoices, lines)

        findings = self._audit()

        self.assertFalse(findings["INV-1"].flagged)

    def test_detects_arithmetic_dates_contract_and_invoice_total(self) -> None:
        self._write(
            INVOICE_HEADER
            + "INV-1,H1,WRONG,2024-01-03,P1,F-MAIN,GOLD,2024-01-01,2024-01-02,999\n",
            LINE_HEADER
            + "L1,INV-1,1,2026-01-02,Service,2,per_item,100,250\n",
        )

        finding = self._audit()["INV-1"]

        self.assertEqual(
            set(finding.categories),
            {
                "contract_number_mismatch",
                "invoice_total_mismatch",
                "line_total_arithmetic",
                "service_date_out_of_window",
            },
        )

    def test_duplicate_resolution_selects_record_matching_line_items(self) -> None:
        self._write(
            INVOICE_HEADER
            + "INV-1,H1,EXPECTED,2024-01-03,P1,F-MAIN,GOLD,2024-01-01,2024-01-02,500\n"
            + "INV-1,H1,EXPECTED,2024-02-03,P2,F-MAIN,GOLD,2024-02-01,2024-02-02,200\n",
            LINE_HEADER
            + "L1,INV-1,1,2024-02-02,Service,2,per_item,100,200\n",
        )

        finding = self._audit()["INV-1"]

        self.assertEqual(finding.categories, ("duplicate_invoice_id",))
        self.assertEqual(finding.billed_total_cents, 200)
        self.assertEqual(finding.canonical_invoice_row, 3)

    def test_duplicate_metadata_violations_are_not_hidden_by_resolution(self) -> None:
        self._write(
            INVOICE_HEADER
            + "INV-1,H1,WRONG,2024-01-03,P1,F-MAIN,GOLD,2024-01-01,2024-01-02,500\n"
            + "INV-1,H1,EXPECTED,2024-02-03,P2,F-MAIN,GOLD,2024-02-01,2024-02-02,200\n",
            LINE_HEADER
            + "L1,INV-1,1,2024-02-02,Service,2,per_item,100,200\n",
        )

        finding = self._audit()["INV-1"]

        self.assertEqual(
            set(finding.categories),
            {"contract_number_mismatch", "duplicate_invoice_id"},
        )

    def test_duplicate_resolution_uses_the_later_occurrence(self) -> None:
        self._write(
            INVOICE_HEADER
            + "INV-1,H1,EXPECTED,2024-01-03,P1,F-MAIN,GOLD,2024-01-01,2024-01-02,200\n"
            + "INV-1,H1,EXPECTED,2024-02-03,P2,F-MAIN,GOLD,2024-02-01,2024-02-02,500\n",
            LINE_HEADER
            + "L1,INV-1,1,2024-01-02,Service,2,per_item,100,200\n"
            + "L2,INV-1,2,2024-02-02,Service,5,per_item,100,500\n",
        )

        finding = self._audit()["INV-1"]

        self.assertEqual(finding.billed_total_cents, 500)
        self.assertEqual(finding.expected_total_cents, 500)


if __name__ == "__main__":
    unittest.main()
