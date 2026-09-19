from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_3_contract
from insurance_auditing.models import HospitalDataset, InvoiceRecord, LineItem
from insurance_auditing.pricing import ContractAuditor


ROOT = Path(__file__).resolve().parents[1]


def _invoice(invoice_id: str, invoice_date: str) -> InvoiceRecord:
    return InvoiceRecord(
        1,
        invoice_id,
        "H3",
        "INS-H3-2024-0562",
        invoice_date,
        "PAT-TEST",
        "F-MAIN",
        "GOLD",
        "2024-01-01",
        invoice_date,
        0,
    )


def _line(
    invoice_id: str,
    service_date: str,
    description: str,
    basis: str,
    price: int,
) -> LineItem:
    return LineItem(
        1,
        f"{invoice_id}-L1",
        invoice_id,
        1,
        service_date,
        description,
        1,
        basis,
        price,
        price,
    )


class Hospital3PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_3_contract(
            ROOT / "contracts" / "hospital_3"
        )

    def _detail(self, invoice: InvoiceRecord, line: LineItem):
        result = ContractAuditor(self.contract).audit_detailed(
            HospitalDataset(3, (invoice,), (line,))
        )
        return result.line_details[invoice.invoice_id][0]

    def test_selects_amended_rate_by_service_date(self) -> None:
        service = "Ambulatory Otolaryngologic Imaging Interpretation"
        before = self._detail(
            _invoice("INV-BEFORE", "2024-12-31"),
            _line("INV-BEFORE", "2024-12-31", service, "per_procedure", 182_625),
        )
        after = self._detail(
            _invoice("INV-AFTER", "2025-01-01"),
            _line("INV-AFTER", "2025-01-01", service, "per_procedure", 208_200),
        )
        self.assertEqual(before.expected_unit_price_cents, 182_625)
        self.assertEqual(after.expected_unit_price_cents, 208_200)
        self.assertEqual(before.contract_clause_id, "B.1")
        self.assertEqual(after.contract_clause_id, "A1.2")
        self.assertFalse(before.categories)
        self.assertFalse(after.categories)

    def test_additional_service_is_not_billable_before_amendment(self) -> None:
        service = "Advanced Dermatologic Nutritional Support"
        detail = self._detail(
            _invoice("INV-EARLY", "2024-12-31"),
            _line("INV-EARLY", "2024-12-31", service, "per_day", 86_525),
        )
        self.assertEqual(detail.expected_unit_price_cents, 0)
        self.assertEqual(detail.expected_line_total_cents, 0)
        self.assertIn("service_not_contracted_on_date", detail.categories)

    def test_identifies_a_stale_pre_amendment_rate(self) -> None:
        service = "Ambulatory Otolaryngologic Imaging Interpretation"
        detail = self._detail(
            _invoice("INV-STALE", "2025-01-01"),
            _line("INV-STALE", "2025-01-01", service, "per_procedure", 182_625),
        )
        self.assertEqual(detail.expected_unit_price_cents, 208_200)
        self.assertIn("contract_amendment_rate_mismatch", detail.categories)

    def test_additional_service_is_billable_from_amendment_date(self) -> None:
        service = "Elective Pulmonary Imaging Interpretation"
        detail = self._detail(
            _invoice("INV-NEW", "2025-01-01"),
            _line("INV-NEW", "2025-01-01", service, "per_procedure", 456_000),
        )
        self.assertEqual(detail.expected_unit_price_cents, 456_000)
        self.assertEqual(detail.contract_clause_id, "A1.3")
        self.assertFalse(detail.categories)


if __name__ == "__main__":
    unittest.main()
