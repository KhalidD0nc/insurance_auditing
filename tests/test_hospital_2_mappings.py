from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from insurance_auditing.agent import AgentConfig
from insurance_auditing.contract_parser import load_hospital_2_contract
from insurance_auditing.hospital_2_mappings import (
    load_hospital_2_mapping_artifact,
    prepare_hospital_2_mappings,
)
from insurance_auditing.service_matching import ServiceMatcher


ROOT = Path(__file__).resolve().parents[1]


class _StructuredRunner:
    def __init__(
        self,
        matcher: ServiceMatcher,
        contract: object,
        *,
        disagree: bool = False,
        confidence: float = 0.97,
        invent_service: bool = False,
    ):
        self.matcher = matcher
        self.contract = contract
        self.disagree = disagree
        self.confidence = confidence
        self.invent_service = invent_service
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, prompt: str, **kwargs: object) -> str:
        payload = json.loads(prompt)
        descriptions = payload["normalized_descriptions"]
        self.calls.append((prompt, kwargs))
        verifier = "classifier_proposals" in payload
        decisions = []
        for description in descriptions:
            candidates = self.matcher.rank_candidates(description, limit=5)
            index = 1 if verifier and self.disagree else 0
            service = candidates[index][1]
            clause_id = self.contract.services[service].clause_id
            if self.invent_service:
                service = "Invented Service"
            decisions.append(
                {
                    "normalized_description": description,
                    "selected_service": service,
                    "confidence": self.confidence,
                    "evidence_clause_ids": [clause_id],
                    "reason": "The normalized clinical terms identify this candidate.",
                    "needs_review": False,
                }
            )
        return json.dumps({"decisions": decisions})


class Hospital2MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_2_contract(
            ROOT / "contracts" / "hospital_2" / "master_services_agreement.md"
        )
        cls.matcher = ServiceMatcher(
            cls.contract,
            minimum_margin=0.15,
            allow_price_tiebreaker=False,
        )
        cls.config = AgentConfig(api_key="test-key", model="test-model")

    def test_two_pass_agreement_produces_validated_versioned_mappings(self) -> None:
        runner = _StructuredRunner(self.matcher, self.contract)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "mappings.json"
            artifact = prepare_hospital_2_mappings(
                ROOT,
                self.contract,
                output,
                batch_size=25,
                agent_runner=runner,
                agent_config=self.config,
            )
            overrides, loaded = load_hospital_2_mapping_artifact(
                output, self.contract
            )

        self.assertEqual(loaded["schema_version"], 1)
        self.assertEqual(loaded["contract_sha256"], self.contract.source_sha256)
        self.assertEqual(loaded["model"], "test-model")
        self.assertGreater(len(overrides), 0)
        self.assertTrue(all(entry["status"] == "accepted" for entry in artifact["mappings"]))
        self.assertTrue(runner.calls)
        for prompt, kwargs in runner.calls:
            self.assertNotIn("invoice_id", prompt)
            self.assertNotIn("patient_id", prompt)
            self.assertNotIn("unit_price", prompt)
            self.assertTrue(kwargs["tools"])

    def test_classifier_verifier_disagreement_remains_unresolved(self) -> None:
        runner = _StructuredRunner(self.matcher, self.contract, disagree=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "mappings.json"
            artifact = prepare_hospital_2_mappings(
                ROOT,
                self.contract,
                output,
                batch_size=25,
                agent_runner=runner,
                agent_config=self.config,
            )
            overrides, _ = load_hospital_2_mapping_artifact(output, self.contract)
        self.assertFalse(overrides)
        self.assertTrue(
            all(entry["status"] == "unresolved" for entry in artifact["mappings"])
        )

    def test_low_confidence_and_unknown_services_are_rejected(self) -> None:
        for runner in (
            _StructuredRunner(self.matcher, self.contract, confidence=0.89),
            _StructuredRunner(self.matcher, self.contract, invent_service=True),
        ):
            with self.subTest(runner=type(runner).__name__), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "mappings.json"
                artifact = prepare_hospital_2_mappings(
                    ROOT,
                    self.contract,
                    output,
                    batch_size=25,
                    agent_runner=runner,
                    agent_config=self.config,
                )
            self.assertTrue(
                all(entry["status"] == "unresolved" for entry in artifact["mappings"])
            )

    def test_failed_refresh_preserves_previously_accepted_entries(self) -> None:
        runner = _StructuredRunner(self.matcher, self.contract)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "mappings.json"
            initial = prepare_hospital_2_mappings(
                ROOT,
                self.contract,
                output,
                batch_size=25,
                agent_runner=runner,
                agent_config=self.config,
            )
            refreshed = prepare_hospital_2_mappings(
                ROOT,
                self.contract,
                output,
                refresh=True,
                batch_size=25,
                agent_runner=lambda *_args, **_kwargs: "not-json",
                agent_config=self.config,
            )
        self.assertEqual(initial["mappings"], refreshed["mappings"])

    def test_rejects_a_stale_contract_fingerprint(self) -> None:
        runner = _StructuredRunner(self.matcher, self.contract)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "mappings.json"
            prepare_hospital_2_mappings(
                ROOT,
                self.contract,
                output,
                batch_size=25,
                agent_runner=runner,
                agent_config=self.config,
            )
            with self.assertRaisesRegex(ValueError, "stale contract fingerprint"):
                load_hospital_2_mapping_artifact(
                    output,
                    replace(self.contract, source_sha256="0" * 64),
                )

    def test_versioned_live_mapping_artifact_passes_the_same_validator(self) -> None:
        path = ROOT / "mappings" / "hospital_2_service_mappings.json"
        overrides, artifact = load_hospital_2_mapping_artifact(path, self.contract)
        self.assertEqual(len(overrides), 6)
        self.assertEqual(len(artifact["mappings"]), 87)
        serialized = path.read_text(encoding="utf-8")
        self.assertNotIn("OPENROUTER_API_KEY", serialized)
        self.assertNotIn("Bearer ", serialized)


if __name__ == "__main__":
    unittest.main()
