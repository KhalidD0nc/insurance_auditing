from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from insurance_auditing.contract_parser import load_hospital_4_contract
from insurance_auditing.models import DEFAULT_PRICING_PIPELINE, DuplicateBillingPolicy
from insurance_auditing.pricing import ContractAuditor


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts" / "hospital_4" / "conditional_reimbursement_agreement.md"
)


class Hospital4ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_4_contract(CONTRACT_PATH)

    def test_extracts_contract_identity_and_all_rule_tables(self) -> None:
        self.assertEqual(self.contract.identity.hospital_number, 4)
        self.assertEqual(self.contract.identity.contract_number, "INS-H4-2024-2049")
        self.assertEqual(len(self.contract.services), 98)
        self.assertEqual(len(self.contract.threshold_premiums), 18)
        self.assertEqual(
            sum(service.daily_cap is not None for service in self.contract.services.values()),
            18,
        )
        self.assertEqual(len(self.contract.bundles), 7)
        self.assertEqual(len(self.contract.volume_discounts), 3)
        self.assertEqual(
            sum(len(rules) for rules in self.contract.volume_discounts.values()),
            4,
        )
        self.assertEqual(len(self.contract.exclusions), 15)
        self.assertEqual(self.contract.non_business_day_uplifts, {})
        self.assertEqual(self.contract.pricing_pipeline, DEFAULT_PRICING_PIPELINE)
        self.assertEqual(
            self.contract.duplicate_billing_policy,
            DuplicateBillingPolicy.REPEATED_SERVICE_PER_PATIENT_DAY,
        )
        ContractAuditor(self.contract)

    def test_extracts_representative_service_premium_and_cap(self) -> None:
        oncology_bed = self.contract.services["Advanced Oncology Ward Bed Occupancy"]
        self.assertEqual(oncology_bed.unit_basis, "per_night")
        self.assertEqual(oncology_bed.rate_cents, 68_700)
        self.assertIsNone(oncology_bed.daily_cap)

        orthopaedic_bed = self.contract.services[
            "Advanced Orthopaedic Ward Bed Occupancy"
        ]
        self.assertEqual(orthopaedic_bed.daily_cap, 12)

        premium = self.contract.threshold_premiums["Standard Hepatic Infusion Therapy"]
        self.assertEqual(premium.threshold, 6)
        self.assertEqual(premium.multiplier, Decimal("1.15"))

    def test_extracts_bundle_columns_in_hospital_4_order(self) -> None:
        bundle = next(
            rule
            for rule in self.contract.bundles
            if rule.service_a == "Specialist Immunologic Consultation"
        )
        self.assertEqual(bundle.rate_a_cents, 91_600)
        self.assertEqual(bundle.service_b, "Supervised Rheumatologic Dialysis Session")
        self.assertEqual(bundle.rate_b_cents, 13_900)

    def test_extracts_sorted_discount_thresholds_and_exclusion_direction(self) -> None:
        discounts = self.contract.volume_discounts[
            "Ambulatory Musculoskeletal Ventilation Support"
        ]
        self.assertEqual([rule.threshold for rule in discounts], [80, 240])
        self.assertEqual(
            [rule.multiplier for rule in discounts],
            [Decimal("0.85"), Decimal("0.70")],
        )

        exclusion = next(
            rule
            for rule in self.contract.exclusions
            if rule.excluded_service == "Advanced Paediatric Theatre Time"
        )
        self.assertEqual(exclusion.window_days, 30)
        self.assertEqual(exclusion.trigger_service, "Bedside Cardiac Home Visit")

    def test_rejects_an_unsupported_rounding_convention(self) -> None:
        markdown = CONTRACT_PATH.read_text(encoding="utf-8").replace(
            "**Rounding convention:** half_up_cent",
            "**Rounding convention:** bankers_rounding",
        )
        with TemporaryDirectory() as directory:
            altered_contract = Path(directory) / "contract.md"
            altered_contract.write_text(markdown, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported rounding convention"):
                load_hospital_4_contract(altered_contract)

    def test_rejects_a_rule_that_references_an_unknown_service(self) -> None:
        markdown = CONTRACT_PATH.read_text(encoding="utf-8").replace(
            "| Advanced Vascular Endoscopic Procedure | more than 10 procedures | +25% |",
            "| Missing Contract Service | more than 10 procedures | +25% |",
        )
        with TemporaryDirectory() as directory:
            altered_contract = Path(directory) / "contract.md"
            altered_contract.write_text(markdown, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown services"):
                load_hospital_4_contract(altered_contract)


if __name__ == "__main__":
    unittest.main()
