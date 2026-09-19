from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from insurance_auditing.contract_parser import load_hospital_5_contract
from insurance_auditing.models import DEFAULT_PRICING_PIPELINE, DuplicateBillingPolicy
from insurance_auditing.pricing import ContractAuditor


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "contracts" / "hospital_5" / "network_reimbursement_agreement.md"
)


class Hospital5ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_5_contract(CONTRACT_PATH)

    def test_extracts_identity_and_every_rule_table(self) -> None:
        contract = self.contract
        self.assertEqual(contract.identity.hospital_number, 5)
        self.assertEqual(contract.identity.contract_number, "INS-H5-2024-0731")
        self.assertEqual(len(contract.services), 84)
        self.assertEqual(
            sum(rule.daily_cap is not None for rule in contract.services.values()),
            9,
        )
        self.assertEqual(len(contract.facility_multipliers), 84)
        self.assertEqual(len(contract.plan_tier_multipliers), 84)
        self.assertEqual(len(contract.threshold_premiums), 10)
        self.assertEqual(len(contract.non_business_day_uplifts), 9)
        self.assertEqual(len(contract.bundles), 3)
        self.assertEqual(sum(map(len, contract.volume_discounts.values())), 15)
        self.assertEqual(len(contract.exclusions), 7)
        self.assertEqual(contract.pricing_pipeline, DEFAULT_PRICING_PIPELINE)
        self.assertEqual(
            contract.duplicate_billing_policy,
            DuplicateBillingPolicy.REPEATED_SERVICE_PER_PATIENT_DAY,
        )
        ContractAuditor(contract)

    def test_extracts_representative_network_rates(self) -> None:
        service = "Advanced Cardiac Ventilation Support"
        rule = self.contract.services[service]
        self.assertEqual(rule.rate_cents, 3_375)
        self.assertEqual(rule.unit_basis, "per_hour")
        self.assertEqual(rule.daily_cap, 24)
        self.assertEqual(
            self.contract.facility_multipliers[service],
            {
                "F-MAIN": Decimal("1"),
                "F-NORTH": Decimal("1.1"),
                "F-COAST": Decimal("0.92"),
            },
        )
        self.assertEqual(
            self.contract.plan_tier_multipliers[service]["GOLD"],
            Decimal("0.92"),
        )

    def test_extracts_bundle_discount_and_exclusion_direction(self) -> None:
        bundle = next(
            rule
            for rule in self.contract.bundles
            if rule.service_a == "Emergency Metabolic Discharge Planning"
        )
        self.assertEqual(bundle.rate_a_cents, 17_500)
        self.assertEqual(
            bundle.service_b,
            "Specialist Gastrointestinal Pharmaceutical Dispensing",
        )
        self.assertEqual(bundle.rate_b_cents, 3_500)
        discounts = self.contract.volume_discounts[
            "Elective Metabolic Pharmaceutical Dispensing"
        ]
        self.assertEqual([rule.threshold for rule in discounts], [100, 300])
        self.assertEqual(
            [rule.multiplier for rule in discounts],
            [Decimal("0.88"), Decimal("0.75")],
        )
        exclusion = self.contract.exclusions[0]
        self.assertEqual(
            exclusion.excluded_service,
            "Comprehensive Ophthalmic Isolation Room Occupancy",
        )
        self.assertEqual(exclusion.window_days, 30)
        self.assertEqual(
            exclusion.trigger_service,
            "Ambulatory Paediatric Isolation Room Occupancy",
        )

    def test_rejects_a_partial_facility_table(self) -> None:
        markdown = CONTRACT_PATH.read_text(encoding="utf-8").replace(
            "| Advanced Cardiac Ventilation Support | 1 | 1.1 | 0.92 |\n",
            "",
            1,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "contract.md"
            path.write_text(markdown, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "facility-multiplier rows"):
                load_hospital_5_contract(path)


if __name__ == "__main__":
    unittest.main()
