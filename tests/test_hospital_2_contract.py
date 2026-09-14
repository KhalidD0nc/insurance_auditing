from decimal import Decimal
from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_2_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "hospital_2" / "master_services_agreement.md"


class Hospital2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_2_contract(CONTRACT_PATH)

    def test_extracts_identity_and_all_prose_rules(self) -> None:
        self.assertEqual(self.contract.identity.contract_number, "INS-H2-2024-1183")
        self.assertEqual(len(self.contract.services), 76)
        self.assertEqual(len(self.contract.threshold_premiums), 9)
        self.assertEqual(len(self.contract.non_business_day_uplifts), 8)
        # The agreement has twelve payable thresholds. The additional phrase in
        # Article III is the ordering definition, not a thirteenth discount.
        self.assertEqual(sum(map(len, self.contract.volume_discounts.values())), 12)
        self.assertEqual(len(self.contract.bundles), 3)
        self.assertEqual(sum(len(rule.clause_ids) for rule in self.contract.bundles), 6)
        self.assertEqual(len(self.contract.exclusions), 6)
        self.assertRegex(self.contract.source_sha256 or "", r"^[0-9a-f]{64}$")

    def test_extracts_representative_money_units_and_provenance(self) -> None:
        service = self.contract.services["Advanced Infectious Isolation Room Occupancy"]
        self.assertEqual(service.rate_cents, 164_825)
        self.assertEqual(service.unit_basis, "per_day")
        self.assertEqual(service.daily_cap, 24)
        self.assertEqual(service.clause_id, "4.1")
        self.assertEqual(
            self.contract.non_business_day_uplifts["Emergency Renal Infusion Therapy"],
            Decimal("1.12"),
        )

    def test_collapses_only_symmetric_bundle_mentions(self) -> None:
        bundle = next(
            rule
            for rule in self.contract.bundles
            if "Bedside Obstetric Physiotherapy Session"
            in {rule.service_a, rule.service_b}
        )
        rates = {
            bundle.service_a: bundle.rate_a_cents,
            bundle.service_b: bundle.rate_b_cents,
        }
        self.assertEqual(rates["Bedside Obstetric Physiotherapy Session"], 5_200)
        self.assertEqual(
            rates["Postoperative Orthopaedic Isolation Room Occupancy"], 90_350
        )
        self.assertEqual(set(bundle.clause_ids), {"6.2", "14.5"})

    def test_rejects_contract_identity_drift(self) -> None:
        text = CONTRACT_PATH.read_text(encoding="utf-8").replace(
            "INS-H2-2024-1183", "INS-H2-CHANGED", 1
        )
        temporary = ROOT / "contracts" / "hospital_2" / ".invalid-contract-test.md"
        try:
            temporary.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                load_hospital_2_contract(temporary)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
