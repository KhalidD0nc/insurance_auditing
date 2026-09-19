from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_5_contract
from insurance_auditing.models import HospitalDataset, InvoiceRecord, LineItem
from insurance_auditing.pricing import ContractAuditor


ROOT = Path(__file__).resolve().parents[1]


def _invoice(
    row: int,
    invoice_id: str,
    patient_id: str,
    total_cents: int,
    *,
    facility: str = "F-MAIN",
    tier: str = "BRONZE",
    invoice_date: str = "2024-02-01",
) -> InvoiceRecord:
    return InvoiceRecord(
        row_number=row,
        invoice_id=invoice_id,
        hospital_id="H5",
        contract_number="INS-H5-2024-0731",
        invoice_date=invoice_date,
        patient_id=patient_id,
        facility_code=facility,
        plan_tier=tier,
        admission_date="2024-01-01",
        discharge_date=invoice_date,
        invoice_total_cents=total_cents,
    )


def _line(
    row: int,
    line_id: str,
    invoice_id: str,
    service_date: str,
    service: str,
    quantity: int,
    unit_basis: str,
    unit_price_cents: int,
) -> LineItem:
    return LineItem(
        row_number=row,
        line_id=line_id,
        invoice_id=invoice_id,
        line_no=row,
        service_date=service_date,
        description=service,
        quantity=quantity,
        unit_basis_as_billed=unit_basis,
        unit_price_cents=unit_price_cents,
        line_total_cents=quantity * unit_price_cents,
    )


class Hospital5PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_5_contract(
            ROOT
            / "contracts"
            / "hospital_5"
            / "network_reimbursement_agreement.md"
        )

    def _audit(self, invoices, lines):
        dataset = HospitalDataset(5, tuple(invoices), tuple(lines))
        return ContractAuditor(self.contract).audit_detailed(dataset)

    def test_rounds_facility_tier_and_weekend_uplift_after_each_step(self) -> None:
        line = _line(
            1,
            "H5-TEST-NETWORK-01",
            "INV-H5-TEST-NETWORK",
            "2024-01-13",
            "Assisted Cardiac Ventilation Support",
            1,
            "per_day",
            156_675,
        )
        invoice = _invoice(
            1,
            line.invoice_id,
            "PT-NETWORK",
            line.line_total_cents,
            facility="F-NORTH",
            tier="GOLD",
        )
        result = self._audit([invoice], [line])
        finding = result.findings[line.invoice_id]
        self.assertEqual(finding.expected_total_cents, 190_267)
        self.assertIn("unit_price_mismatch", finding.categories)
        self.assertEqual(
            [step.stage for step in result.line_details[line.invoice_id][0].rate_calculation],
            ["base", "facility", "plan_tier", "premium"],
        )
        self.assertEqual(
            [
                step.output_rate_cents
                for step in result.line_details[line.invoice_id][0].rate_calculation
            ],
            [156_675, 172_343, 158_556, 190_267],
        )

    def test_applies_bundle_before_network_multipliers(self) -> None:
        first = _line(
            1,
            "H5-TEST-BUNDLE-01",
            "INV-H5-TEST-BUNDLE-1",
            "2024-01-10",
            "Emergency Metabolic Discharge Planning",
            1,
            "per_visit",
            20_822,
        )
        second = _line(
            2,
            "H5-TEST-BUNDLE-02",
            "INV-H5-TEST-BUNDLE-2",
            "2024-01-10",
            "Specialist Gastrointestinal Pharmaceutical Dispensing",
            1,
            "per_unit_dispensed",
            4_311,
        )
        invoices = [
            _invoice(
                1,
                first.invoice_id,
                "PT-BUNDLE",
                first.line_total_cents,
                facility="F-NORTH",
                tier="GOLD",
            ),
            _invoice(
                2,
                second.invoice_id,
                "PT-BUNDLE",
                second.line_total_cents,
                facility="F-NORTH",
                tier="GOLD",
            ),
        ]
        result = self._audit(invoices, [first, second])
        self.assertEqual(result.findings[first.invoice_id].expected_total_cents, 17_710)
        self.assertEqual(result.findings[second.invoice_id].expected_total_cents, 3_658)
        self.assertIn("bundle_not_applied", result.findings[first.invoice_id].categories)

    def test_applies_threshold_premium_after_network_multipliers(self) -> None:
        line = _line(
            1,
            "H5-TEST-PREMIUM-01",
            "INV-H5-TEST-PREMIUM",
            "2024-01-10",
            "Comprehensive Palliative Consultation",
            9,
            "per_visit",
            36_803,
        )
        invoice = _invoice(
            1,
            line.invoice_id,
            "PT-PREMIUM",
            line.line_total_cents,
            tier="BRONZE",
        )
        finding = self._audit([invoice], [line]).findings[line.invoice_id]
        self.assertEqual(finding.expected_total_cents, 9 * 47_844)
        self.assertIn("premium_omitted", finding.categories)

    def test_applies_volume_discount_after_network_multipliers(self) -> None:
        first = _line(
            1,
            "H5-TEST-DISCOUNT-01",
            "INV-H5-TEST-DISCOUNT-1",
            "2024-01-10",
            "Ambulatory Hepatic Case Conference",
            81,
            "per_hour",
            20_764,
        )
        second = _line(
            2,
            "H5-TEST-DISCOUNT-02",
            "INV-H5-TEST-DISCOUNT-2",
            "2024-01-11",
            "Ambulatory Hepatic Case Conference",
            1,
            "per_hour",
            20_764,
        )
        invoices = [
            _invoice(1, first.invoice_id, "PT-DISCOUNT-1", first.line_total_cents),
            _invoice(2, second.invoice_id, "PT-DISCOUNT-2", second.line_total_cents),
        ]
        findings = self._audit(invoices, [first, second]).findings
        self.assertEqual(findings[first.invoice_id].expected_total_cents, 81 * 20_764)
        self.assertEqual(findings[second.invoice_id].expected_total_cents, 18_272)
        self.assertIn("volume_discount_omitted", findings[second.invoice_id].categories)

    def test_identifies_another_facility_multiplier(self) -> None:
        line = _line(
            1,
            "H5-TEST-FACILITY-01",
            "INV-H5-TEST-FACILITY",
            "2024-01-10",
            "Advanced Cardiac Ventilation Support",
            1,
            "per_hour",
            3_105,
        )
        invoice = _invoice(
            1,
            line.invoice_id,
            "PT-FACILITY",
            line.line_total_cents,
            facility="F-NORTH",
        )
        finding = self._audit([invoice], [line]).findings[line.invoice_id]
        self.assertEqual(finding.expected_total_cents, 3_713)
        self.assertIn("facility_multiplier_mismatch", finding.categories)


if __name__ == "__main__":
    unittest.main()
