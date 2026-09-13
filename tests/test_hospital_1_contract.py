from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_1_contract
from insurance_auditing.service_matching import ServiceMatcher


ROOT = Path(__file__).resolve().parents[1]


class Hospital1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_1_contract(
            ROOT / "contracts" / "hospital_1" / "provider_services_agreement.md"
        )
        cls.matcher = ServiceMatcher(cls.contract)

    def test_extracts_all_contract_tables(self) -> None:
        self.assertEqual(len(self.contract.services), 108)
        self.assertEqual(len(self.contract.threshold_premiums), 9)
        self.assertEqual(len(self.contract.non_business_day_uplifts), 7)
        self.assertEqual(len(self.contract.bundles), 3)
        self.assertEqual(len(self.contract.exclusions), 6)
        service = self.contract.services["Advanced Rheumatologic Laboratory Panel"]
        self.assertEqual(service.rate_cents, 14_775)
        self.assertEqual(service.daily_cap, 4)

    def test_matches_reordered_abbreviated_description(self) -> None:
        match = self.matcher.match(
            "Procedure Routine Urologic Biop /NG-3022",
            "per_procedure",
            226_950,
        )
        self.assertEqual(match.service_name, "Routine Urologic Biopsy Procedure")

    def test_rejects_conflicting_service_identity(self) -> None:
        match = self.matcher.match(
            "Adv Renal Consultation",
            "per_procedure",
            123_456,
        )
        self.assertIsNone(match.service_name)

    def test_uses_plausible_rate_only_to_break_textual_tie(self) -> None:
        match = self.matcher.match(
            "Procedure Immun Endosc",
            "per_night",
            121_580,
        )
        self.assertEqual(
            match.service_name,
            "Preoperative Immunologic Endoscopic Procedure",
        )


if __name__ == "__main__":
    unittest.main()
