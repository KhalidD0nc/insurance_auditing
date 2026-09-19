import json
from pathlib import Path
import tempfile
import unittest

from insurance_auditing.contract_parser import load_hospital_5_contract
from insurance_auditing.hospital_5_mappings import load_hospital_5_mapping_artifact
from insurance_auditing.io import load_hospital
from insurance_auditing.service_matching import normalise_service_description


ROOT = Path(__file__).resolve().parents[1]


class Hospital5MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_5_contract(
            ROOT
            / "contracts"
            / "hospital_5"
            / "network_reimbursement_agreement.md"
        )
        cls.path = ROOT / "mappings" / "hospital_5_service_mappings.json"

    def test_loads_the_reviewed_recurring_ambiguity(self) -> None:
        mappings, artifact = load_hospital_5_mapping_artifact(
            self.path, self.contract
        )
        self.assertEqual(len(mappings), 1)
        self.assertEqual(artifact["hospital"], 5)
        self.assertEqual(
            mappings["comprehensive consultation"].service_name,
            "Comprehensive Palliative Consultation",
        )

    def test_review_observations_match_the_versioned_dataset(self) -> None:
        _, artifact = load_hospital_5_mapping_artifact(self.path, self.contract)
        dataset = load_hospital(ROOT, 5)
        entry = artifact["mappings"][0]
        matching = [
            item
            for item in dataset.line_items
            if normalise_service_description(item.description)
            == entry["normalized_description"]
        ]
        observations = entry["observations"]
        self.assertEqual(len(matching), observations["line_count"])
        self.assertEqual(
            {item.unit_basis_as_billed for item in matching},
            {observations["unit_basis"]},
        )
        self.assertEqual(
            sorted({item.unit_price_cents for item in matching}),
            observations["observed_rates_cents"],
        )

    def test_rejects_a_stale_contract_fingerprint(self) -> None:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["contract_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mappings").mkdir()
            (root / "prompts").mkdir()
            (root / "prompts" / "011_hospital_5_service_review.md").write_text(
                (ROOT / "prompts" / "011_hospital_5_service_review.md").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            path = root / "mappings" / "mapping.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale contract fingerprint"):
                load_hospital_5_mapping_artifact(path, self.contract)


if __name__ == "__main__":
    unittest.main()
