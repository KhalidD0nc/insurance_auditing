import json
from pathlib import Path
import tempfile
import unittest

from insurance_auditing.contract_parser import load_hospital_3_contract
from insurance_auditing.hospital_3_mappings import (
    load_hospital_3_mapping_artifact,
)
from insurance_auditing.io import load_hospital
from insurance_auditing.service_matching import normalise_service_description


ROOT = Path(__file__).resolve().parents[1]


class Hospital3MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_3_contract(
            ROOT / "contracts" / "hospital_3"
        )
        cls.path = ROOT / "mappings" / "hospital_3_service_mappings.json"

    def test_loads_reviewed_recurring_ambiguities(self) -> None:
        mappings, artifact = load_hospital_3_mapping_artifact(
            self.path, self.contract
        )
        self.assertEqual(len(mappings), 6)
        self.assertEqual(artifact["hospital"], 3)
        self.assertEqual(
            mappings["advanced telemetry monitoring"].service_name,
            "Advanced Paediatric Telemetry Monitoring",
        )

    def test_rejects_stale_or_incomplete_evidence(self) -> None:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["mappings"][0]["observations"]["observed_rates_cents"] = []
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            (temporary_root / "mappings").mkdir()
            (temporary_root / "prompts").mkdir()
            (temporary_root / "prompts" / "010_hospital_3_service_review.md").write_text(
                (ROOT / "prompts" / "010_hospital_3_service_review.md").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            path = temporary_root / "mappings" / "mappings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence is invalid"):
                load_hospital_3_mapping_artifact(path, self.contract)

    def test_review_observations_match_the_versioned_dataset(self) -> None:
        _, artifact = load_hospital_3_mapping_artifact(self.path, self.contract)
        dataset = load_hospital(ROOT, 3)
        for entry in artifact["mappings"]:
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


if __name__ == "__main__":
    unittest.main()
