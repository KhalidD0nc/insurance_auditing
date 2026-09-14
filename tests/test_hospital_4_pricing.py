from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_4_contract
from insurance_auditing.models import (
    BundleRule,
    HospitalDataset,
    InvoiceRecord,
    LineItem,
    ThresholdPremium,
    VolumeDiscount,
)
from insurance_auditing.pricing import ContractAuditor


ROOT = Path(__file__).resolve().parents[1]


def _invoice(
    row: int,
    invoice_id: str,
    patient_id: str,
    total_cents: int,
    invoice_date: str = "2024-02-01",
) -> InvoiceRecord:
    return InvoiceRecord(
        row_number=row,
        invoice_id=invoice_id,
        hospital_id="H4",
        contract_number="INS-H4-2024-2049",
        invoice_date=invoice_date,
        patient_id=patient_id,
        facility_code="F-MAIN",
        plan_tier="GOLD",
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


class Hospital4PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_4_contract(
            ROOT
            / "contracts"
            / "hospital_4"
            / "conditional_reimbursement_agreement.md"
        )

    def _audit(self, invoices, lines, contract=None):
        return self._audit_detailed(invoices, lines, contract).findings

    def _audit_detailed(self, invoices, lines, contract=None):
        dataset = HospitalDataset(4, tuple(invoices), tuple(lines))
        return ContractAuditor(contract or self.contract).audit_detailed(dataset)

    def test_applies_bundled_rates_to_both_services(self) -> None:
        lines = [
            _line(
                1,
                "H4-TEST-BUNDLE-01",
                "INV-H4-TEST-BUNDLE-1",
                "2024-01-10",
                "Ambulatory Obstetric Case Conference",
                1,
                "per_hour",
                13_975,
            ),
            _line(
                2,
                "H4-TEST-BUNDLE-02",
                "INV-H4-TEST-BUNDLE-2",
                "2024-01-10",
                "Focused Vascular Infusion Therapy",
                1,
                "per_unit_dispensed",
                5_775,
            ),
        ]
        invoices = [
            _invoice(1, "INV-H4-TEST-BUNDLE-1", "PT-BUNDLE", 13_975),
            _invoice(2, "INV-H4-TEST-BUNDLE-2", "PT-BUNDLE", 5_775),
        ]

        result = self._audit_detailed(invoices, lines)
        findings = result.findings

        self.assertEqual(
            findings["INV-H4-TEST-BUNDLE-1"].expected_total_cents,
            11_875,
        )
        self.assertEqual(
            findings["INV-H4-TEST-BUNDLE-2"].expected_total_cents,
            4_900,
        )
        self.assertIn(
            "bundle_not_applied",
            findings["INV-H4-TEST-BUNDLE-1"].categories,
        )
        first_detail = result.line_details["INV-H4-TEST-BUNDLE-1"][0]
        self.assertEqual(
            first_detail.bundle_partner_line_ids,
            ("H4-TEST-BUNDLE-02",),
        )
        self.assertEqual(
            [step.output_rate_cents for step in first_detail.rate_calculation],
            [13_975, 11_875],
        )

    def test_aggregates_daily_quantity_before_applying_premium(self) -> None:
        lines = [
            _line(
                1,
                "H4-TEST-PREMIUM-01",
                "INV-H4-TEST-PREMIUM",
                "2024-01-10",
                "Standard Hepatic Infusion Therapy",
                4,
                "per_unit_dispensed",
                3_975,
            ),
            _line(
                2,
                "H4-TEST-PREMIUM-02",
                "INV-H4-TEST-PREMIUM",
                "2024-01-10",
                "Standard Hepatic Infusion Therapy",
                3,
                "per_unit_dispensed",
                3_975,
            ),
        ]
        invoices = [
            _invoice(1, "INV-H4-TEST-PREMIUM", "PT-PREMIUM", 7 * 3_975)
        ]

        finding = self._audit(invoices, lines)["INV-H4-TEST-PREMIUM"]

        self.assertEqual(finding.expected_total_cents, 4 * 4_571)
        self.assertIn("premium_omitted", finding.categories)
        self.assertIn("duplicate_service", finding.categories)

    def test_applies_volume_discount_only_after_threshold_is_crossed(self) -> None:
        first_line = _line(
            1,
            "H4-TEST-DISCOUNT-01",
            "INV-H4-TEST-DISCOUNT-1",
            "2024-01-10",
            "Standard Oncology Ward Bed Occupancy",
            121,
            "per_day",
            57_950,
        )
        second_line = _line(
            2,
            "H4-TEST-DISCOUNT-02",
            "INV-H4-TEST-DISCOUNT-2",
            "2024-01-11",
            "Standard Oncology Ward Bed Occupancy",
            1,
            "per_day",
            57_950,
        )
        invoices = [
            _invoice(
                1,
                "INV-H4-TEST-DISCOUNT-1",
                "PT-DISCOUNT-1",
                first_line.line_total_cents,
            ),
            _invoice(
                2,
                "INV-H4-TEST-DISCOUNT-2",
                "PT-DISCOUNT-2",
                second_line.line_total_cents,
            ),
        ]

        findings = self._audit(invoices, [first_line, second_line])

        self.assertEqual(
            findings["INV-H4-TEST-DISCOUNT-1"].expected_total_cents,
            first_line.line_total_cents,
        )
        discounted = findings["INV-H4-TEST-DISCOUNT-2"]
        self.assertEqual(discounted.expected_total_cents, 52_155)
        self.assertIn("volume_discount_omitted", discounted.categories)

    def test_caps_the_payable_daily_quantity(self) -> None:
        line = _line(
            1,
            "H4-TEST-CAP-01",
            "INV-H4-TEST-CAP",
            "2024-01-10",
            "Extended Hepatic Transfusion Service",
            13,
            "per_unit_dispensed",
            8_450,
        )
        invoices = [
            _invoice(1, "INV-H4-TEST-CAP", "PT-CAP", line.line_total_cents)
        ]

        finding = self._audit(invoices, [line])["INV-H4-TEST-CAP"]

        self.assertEqual(finding.expected_total_cents, 8 * 8_450)
        self.assertIn("daily_cap_exceeded", finding.categories)

    def test_aggregates_daily_cap_across_invoices(self) -> None:
        first = _line(
            1,
            "H4-TEST-CROSS-CAP-01",
            "INV-H4-TEST-CROSS-CAP-1",
            "2024-01-10",
            "Extended Hepatic Transfusion Service",
            6,
            "per_unit_dispensed",
            8_450,
        )
        second = _line(
            2,
            "H4-TEST-CROSS-CAP-02",
            "INV-H4-TEST-CROSS-CAP-2",
            "2024-01-10",
            "Extended Hepatic Transfusion Service",
            4,
            "per_unit_dispensed",
            8_450,
        )
        invoices = [
            _invoice(
                1,
                "INV-H4-TEST-CROSS-CAP-1",
                "PT-CROSS-CAP",
                first.line_total_cents,
            ),
            _invoice(
                2,
                "INV-H4-TEST-CROSS-CAP-2",
                "PT-CROSS-CAP",
                second.line_total_cents,
            ),
        ]

        findings = self._audit(invoices, [first, second])

        self.assertEqual(
            findings["INV-H4-TEST-CROSS-CAP-1"].expected_total_cents,
            6 * 8_450,
        )
        second_finding = findings["INV-H4-TEST-CROSS-CAP-2"]
        self.assertEqual(second_finding.expected_total_cents, 0)
        self.assertIn("daily_cap_exceeded", second_finding.categories)
        self.assertIn("duplicate_service", second_finding.categories)

    def test_applies_exclusion_window_when_trigger_is_later(self) -> None:
        excluded = _line(
            1,
            "H4-TEST-EXCLUSION-01",
            "INV-H4-TEST-EXCLUSION-1",
            "2024-01-01",
            "Advanced Paediatric Theatre Time",
            1,
            "per_hour",
            37_100,
        )
        trigger = _line(
            2,
            "H4-TEST-EXCLUSION-02",
            "INV-H4-TEST-EXCLUSION-2",
            "2024-01-20",
            "Bedside Cardiac Home Visit",
            1,
            "per_visit",
            64_800,
        )
        invoices = [
            _invoice(
                1,
                "INV-H4-TEST-EXCLUSION-1",
                "PT-EXCLUSION",
                excluded.line_total_cents,
            ),
            _invoice(
                2,
                "INV-H4-TEST-EXCLUSION-2",
                "PT-EXCLUSION",
                trigger.line_total_cents,
            ),
        ]

        result = self._audit_detailed(invoices, [excluded, trigger])
        findings = result.findings
        finding = findings["INV-H4-TEST-EXCLUSION-1"]

        self.assertEqual(finding.expected_total_cents, 0)
        self.assertIn("exclusion_window_violation", finding.categories)
        self.assertEqual(
            findings["INV-H4-TEST-EXCLUSION-2"].expected_total_cents,
            trigger.line_total_cents,
        )
        self.assertEqual(
            result.line_details["INV-H4-TEST-EXCLUSION-1"][0].exclusion_trigger_line_ids,
            ("H4-TEST-EXCLUSION-02",),
        )

    def test_flags_repeated_service_inside_and_across_invoices(self) -> None:
        first = _line(
            1,
            "H4-TEST-DUPLICATE-01",
            "INV-H4-TEST-DUPLICATE-1",
            "2024-01-10",
            "Advanced Palliative Home Visit",
            1,
            "per_visit",
            11_450,
        )
        same_invoice = replace(
            first,
            row_number=2,
            line_id="H4-TEST-DUPLICATE-02",
            line_no=2,
        )
        later_invoice = replace(
            first,
            row_number=3,
            line_id="H4-TEST-DUPLICATE-03",
            invoice_id="INV-H4-TEST-DUPLICATE-2",
        )
        invoices = [
            _invoice(
                1,
                "INV-H4-TEST-DUPLICATE-1",
                "PT-DUPLICATE",
                first.line_total_cents + same_invoice.line_total_cents,
            ),
            _invoice(
                2,
                "INV-H4-TEST-DUPLICATE-2",
                "PT-DUPLICATE",
                later_invoice.line_total_cents,
            ),
        ]

        result = self._audit_detailed(
            invoices,
            [first, same_invoice, later_invoice],
        )
        findings = result.findings

        first_finding = findings["INV-H4-TEST-DUPLICATE-1"]
        self.assertEqual(first_finding.expected_total_cents, 11_450)
        self.assertIn("duplicate_service", first_finding.categories)
        later_finding = findings["INV-H4-TEST-DUPLICATE-2"]
        self.assertEqual(later_finding.expected_total_cents, 0)
        self.assertIn("duplicate_service", later_finding.categories)
        self.assertEqual(
            result.line_details["INV-H4-TEST-DUPLICATE-2"][0].duplicate_of_line_id,
            "H4-TEST-DUPLICATE-01",
        )

    def test_rounds_after_each_overlapping_adjustment(self) -> None:
        service = "Standard Hepatic Infusion Therapy"
        paired_service = "Routine Cardiac Home Visit"
        overlapping_contract = replace(
            self.contract,
            threshold_premiums={
                service: ThresholdPremium(service, 0, Decimal("1.15"))
            },
            volume_discounts={
                service: (VolumeDiscount(service, 0, Decimal("0.90")),)
            },
            bundles=(BundleRule(service, paired_service, 10_001, 16_200),),
            exclusions=(),
        )
        prior = _line(
            1,
            "H4-TEST-ROUND-01",
            "INV-H4-TEST-ROUND-1",
            "2024-01-01",
            service,
            1,
            "per_unit_dispensed",
            3_975,
        )
        adjusted = _line(
            2,
            "H4-TEST-ROUND-02",
            "INV-H4-TEST-ROUND-2",
            "2024-01-02",
            service,
            1,
            "per_unit_dispensed",
            10_001,
        )
        paired = _line(
            3,
            "H4-TEST-ROUND-03",
            "INV-H4-TEST-ROUND-2",
            "2024-01-02",
            paired_service,
            1,
            "per_visit",
            16_200,
        )
        invoices = [
            _invoice(1, "INV-H4-TEST-ROUND-1", "PT-ROUND", 3_975),
            _invoice(2, "INV-H4-TEST-ROUND-2", "PT-ROUND", 10_001 + 16_200),
        ]

        result = self._audit_detailed(
            invoices,
            [prior, adjusted, paired],
            overlapping_contract,
        )
        finding = result.findings["INV-H4-TEST-ROUND-2"]

        # 10,001 * 1.15 = 11,501.15 -> 11,501; then * 0.90 = 10,350.9 -> 10,351.
        self.assertEqual(finding.expected_total_cents, 10_351 + 16_200)
        adjusted_detail = next(
            detail
            for detail in result.line_details["INV-H4-TEST-ROUND-2"]
            if detail.line_id == "H4-TEST-ROUND-02"
        )
        self.assertEqual(
            [step.stage for step in adjusted_detail.rate_calculation],
            ["base", "bundle", "premium", "discount"],
        )
        self.assertEqual(
            [step.output_rate_cents for step in adjusted_detail.rate_calculation],
            [3_975, 10_001, 11_501, 10_351],
        )


if __name__ == "__main__":
    unittest.main()
