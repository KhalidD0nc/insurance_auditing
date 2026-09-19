from pathlib import Path
import tempfile
import unittest

from insurance_auditing.contract_parser import load_hospital_3_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIRECTORY = ROOT / "contracts" / "hospital_3"


class Hospital3ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_3_contract(CONTRACT_DIRECTORY)

    def test_parses_all_documents_and_rule_counts(self) -> None:
        self.assertEqual(len(self.contract.services), 120)
        self.assertEqual(len(self.contract.threshold_premiums), 14)
        self.assertEqual(len(self.contract.non_business_day_uplifts), 12)
        self.assertEqual(
            sum(map(len, self.contract.volume_discounts.values())),
            19,
        )
        self.assertEqual(len(self.contract.bundles), 5)
        self.assertEqual(len(self.contract.exclusions), 10)
        self.assertTrue(self.contract.source_sha256)

    def test_records_amended_and_additional_services(self) -> None:
        amended = self.contract.services[
            "Ambulatory Otolaryngologic Imaging Interpretation"
        ]
        self.assertEqual(amended.rate_cents, 182_625)
        self.assertEqual(amended.scheduled_rates[0].rate_cents, 208_200)
        self.assertEqual(
            amended.scheduled_rates[0].effective_from.isoformat(),
            "2025-01-01",
        )

        additional = self.contract.services[
            "Advanced Dermatologic Nutritional Support"
        ]
        self.assertEqual(additional.rate_cents, 86_525)
        self.assertEqual(additional.effective_from.isoformat(), "2025-01-01")

    def test_rejects_an_amendment_prior_rate_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory)
            for source in CONTRACT_DIRECTORY.glob("*.md"):
                (copied / source.name).write_text(
                    source.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            amendment = copied / "amendment_no_1.md"
            amendment.write_text(
                amendment.read_text(encoding="utf-8").replace(
                    "GBP 1,826.25 | GBP 2,082.00",
                    "GBP 1,826.26 | GBP 2,082.00",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "prior rate mismatch"):
                load_hospital_3_contract(copied)


if __name__ == "__main__":
    unittest.main()
