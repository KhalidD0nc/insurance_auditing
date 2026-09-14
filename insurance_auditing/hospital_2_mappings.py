from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent import AgentConfig, build_hospital_2_tools, run_agent
from .agent.errors import AgentError
from .io import load_hospital
from .models import ContractRules, ServiceMappingOverride
from .service_matching import ServiceMatcher, normalise_service_description


MAPPING_SCHEMA_VERSION = 1
PROMPT_VERSION = "hospital-2-service-mapping-v1"
MINIMUM_LLM_CONFIDENCE = 0.90
DEFAULT_MAPPING_PATH = Path("mappings/hospital_2_service_mappings.json")
CLASSIFIER_PROMPT_PATH = Path("prompts/007_hospital_2_service_classifier.md")
VERIFIER_PROMPT_PATH = Path("prompts/008_hospital_2_service_verifier.md")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision_schema() -> dict[str, Any]:
    decision = {
        "type": "object",
        "properties": {
            "normalized_description": {"type": "string"},
            "selected_service": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_clause_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reason": {"type": "string"},
            "needs_review": {"type": "boolean"},
        },
        "required": [
            "normalized_description",
            "selected_service",
            "confidence",
            "evidence_clause_ids",
            "reason",
            "needs_review",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "hospital_2_service_decisions",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "decisions": {"type": "array", "items": decision}
                },
                "required": ["decisions"],
                "additionalProperties": False,
            },
        },
    }


def _parse_decisions(raw: str, expected: list[str]) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("agent returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        raise ValueError("agent response must contain a decisions array")

    decisions: dict[str, dict[str, Any]] = {}
    for item in payload["decisions"]:
        if not isinstance(item, dict):
            raise ValueError("every agent decision must be an object")
        required = {
            "normalized_description",
            "selected_service",
            "confidence",
            "evidence_clause_ids",
            "reason",
            "needs_review",
        }
        if set(item) != required:
            raise ValueError("agent decision has an invalid shape")
        key = item["normalized_description"]
        selected = item["selected_service"]
        confidence = item["confidence"]
        evidence = item["evidence_clause_ids"]
        if not isinstance(key, str) or key not in expected or key in decisions:
            raise ValueError("agent returned an unexpected or duplicate description")
        if selected is not None and not isinstance(selected, str):
            raise ValueError("selected_service must be a string or null")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("confidence must be between zero and one")
        if not isinstance(evidence, list) or not all(
            isinstance(clause, str) for clause in evidence
        ):
            raise ValueError("evidence_clause_ids must be strings")
        if not isinstance(item["reason"], str) or not isinstance(
            item["needs_review"], bool
        ):
            raise ValueError("agent reason or review flag has an invalid type")
        decisions[key] = dict(item)
    if set(decisions) != set(expected):
        raise ValueError("agent did not return exactly one decision per description")
    return decisions


def _run_agent_pass(
    *,
    descriptions: list[str],
    system_prompt: str,
    prompt_payload: dict[str, Any],
    tools: tuple[Any, ...],
    config: AgentConfig,
    agent_runner: Callable[..., str],
) -> dict[str, dict[str, Any]]:
    response = agent_runner(
        json.dumps(prompt_payload, ensure_ascii=True, sort_keys=True),
        tools=tools,
        response_format=_decision_schema(),
        system_prompt=system_prompt,
        config=config,
    )
    return _parse_decisions(response, descriptions)


def _candidate_payload(
    matcher: ServiceMatcher,
    contract: ContractRules,
    description: str,
) -> list[dict[str, Any]]:
    return [
        {
            "service_name": service_name,
            "text_score": round(score, 6),
            "unit_basis": contract.services[service_name].unit_basis,
            "clause_id": contract.services[service_name].clause_id,
        }
        for score, service_name in matcher.rank_candidates(description, limit=5)
    ]


def _unresolved_entry(
    description: str,
    examples: list[str],
    candidates: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "normalized_description": description,
        "raw_examples": examples,
        "candidates": candidates,
        "classifier": None,
        "verifier": None,
        "selected_service": None,
        "confidence": 0.0,
        "evidence_clause_ids": [],
        "status": "unresolved",
        "failure_reason": reason,
    }


def _validated_entry(
    description: str,
    examples: list[str],
    candidates: list[dict[str, Any]],
    classifier: dict[str, Any],
    verifier: dict[str, Any],
    contract: ContractRules,
) -> dict[str, Any]:
    allowed = {candidate["service_name"] for candidate in candidates}
    selected = classifier["selected_service"]
    confidence = min(float(classifier["confidence"]), float(verifier["confidence"]))
    clause_id = contract.services[selected].clause_id if selected in contract.services else None
    accepted = (
        selected is not None
        and selected == verifier["selected_service"]
        and selected in allowed
        and selected in contract.services
        and not classifier["needs_review"]
        and not verifier["needs_review"]
        and confidence >= MINIMUM_LLM_CONFIDENCE
        and clause_id in classifier["evidence_clause_ids"]
        and clause_id in verifier["evidence_clause_ids"]
    )
    evidence = sorted(
        set(classifier["evidence_clause_ids"]) & set(verifier["evidence_clause_ids"])
    )
    return {
        "normalized_description": description,
        "raw_examples": examples,
        "candidates": candidates,
        "classifier": classifier,
        "verifier": verifier,
        "selected_service": selected if accepted else None,
        "confidence": confidence if accepted else 0.0,
        "evidence_clause_ids": evidence if accepted else [],
        "status": "accepted" if accepted else "unresolved",
        "failure_reason": None if accepted else "conservative_gate_rejected",
    }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o644)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def load_hospital_2_mapping_artifact(
    path: Path,
    contract: ContractRules,
) -> tuple[dict[str, ServiceMappingOverride], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Hospital 2 mapping artifact does not exist: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Hospital 2 mapping artifact is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Hospital 2 mapping artifact must be an object")
    if payload.get("schema_version") != MAPPING_SCHEMA_VERSION:
        raise ValueError("unsupported Hospital 2 mapping schema version")
    if payload.get("hospital") != 2:
        raise ValueError("mapping artifact is not for Hospital 2")
    if not contract.source_sha256 or payload.get("contract_sha256") != contract.source_sha256:
        raise ValueError("Hospital 2 mapping artifact has a stale contract fingerprint")
    entries = payload.get("mappings")
    if not isinstance(entries, list):
        raise ValueError("Hospital 2 mapping artifact must contain a mappings array")
    for metadata_key in ("prompt_version", "prompt_sha256", "model", "generated_at_utc"):
        if not isinstance(payload.get(metadata_key), str) or not payload[metadata_key]:
            raise ValueError(f"mapping artifact metadata is missing {metadata_key}")

    overrides: dict[str, ServiceMappingOverride] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Hospital 2 mapping entry must be an object")
        key = entry.get("normalized_description")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("mapping descriptions must be unique non-empty strings")
        if normalise_service_description(key) != key:
            raise ValueError(f"mapping description is not normalized: {key!r}")
        seen.add(key)
        candidates = entry.get("candidates")
        if not isinstance(candidates, list) or not all(
            isinstance(candidate, dict)
            and isinstance(candidate.get("service_name"), str)
            and candidate["service_name"] in contract.services
            for candidate in candidates
        ):
            raise ValueError(f"mapping candidates are invalid for {key!r}")
        status = entry.get("status")
        if status not in {"accepted", "unresolved"}:
            raise ValueError(f"invalid mapping status for {key!r}")
        if status == "unresolved":
            if entry.get("selected_service") is not None or entry.get("confidence") != 0.0:
                raise ValueError("unresolved mappings cannot select a service")
            continue
        service_name = entry.get("selected_service")
        confidence = entry.get("confidence")
        classifier = entry.get("classifier")
        verifier = entry.get("verifier")
        if service_name not in contract.services:
            raise ValueError(f"mapping references unknown service: {service_name!r}")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or confidence < MINIMUM_LLM_CONFIDENCE
        ):
            raise ValueError(f"accepted mapping confidence is invalid for {key!r}")
        if not isinstance(classifier, dict) or not isinstance(verifier, dict):
            raise ValueError(f"accepted mapping lacks two-pass evidence for {key!r}")
        clause_id = contract.services[service_name].clause_id
        for label, decision in (("classifier", classifier), ("verifier", verifier)):
            decision_confidence = decision.get("confidence")
            decision_evidence = decision.get("evidence_clause_ids")
            if (
                decision.get("normalized_description") != key
                or decision.get("selected_service") != service_name
                or decision.get("needs_review") is not False
                or isinstance(decision_confidence, bool)
                or not isinstance(decision_confidence, (int, float))
                or decision_confidence < MINIMUM_LLM_CONFIDENCE
                or not isinstance(decision_evidence, list)
                or not all(isinstance(item, str) for item in decision_evidence)
                or clause_id not in decision_evidence
            ):
                raise ValueError(f"accepted {label} evidence is invalid for {key!r}")
        if service_name not in {
            candidate["service_name"] for candidate in candidates
        }:
            raise ValueError(f"accepted service is outside candidates for {key!r}")
        expected_confidence = min(
            float(classifier["confidence"]), float(verifier["confidence"])
        )
        if abs(float(confidence) - expected_confidence) > 1e-12:
            raise ValueError(f"accepted mapping confidence is inconsistent for {key!r}")
        overrides[key] = ServiceMappingOverride(service_name, float(confidence))
    return overrides, payload


def prepare_hospital_2_mappings(
    data_root: Path,
    contract: ContractRules,
    output_path: Path,
    *,
    refresh: bool = False,
    batch_size: int = 20,
    agent_runner: Callable[..., str] | None = None,
    agent_config: AgentConfig | None = None,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 25:
        raise ValueError("batch_size must be between 1 and 25")
    if not contract.source_sha256:
        raise ValueError("Hospital 2 contract is missing its source fingerprint")

    dataset = load_hospital(data_root, 2)
    matcher = ServiceMatcher(
        contract,
        minimum_margin=0.15,
        allow_price_tiebreaker=False,
    )
    examples: dict[str, list[str]] = defaultdict(list)
    for item in dataset.line_items:
        match = matcher.match(
            item.description,
            item.unit_basis_as_billed,
            item.unit_price_cents,
        )
        if match.service_name is not None:
            continue
        key = normalise_service_description(item.description)
        if item.description not in examples[key] and len(examples[key]) < 3:
            examples[key].append(item.description)

    existing_entries: dict[str, dict[str, Any]] = {}
    if output_path.is_file():
        _, existing = load_hospital_2_mapping_artifact(output_path, contract)
        existing_entries = {
            entry["normalized_description"]: entry
            for entry in existing["mappings"]
        }

    pending = sorted(
        key for key in examples if refresh or key not in existing_entries
    )
    classifier_prompt = (data_root / CLASSIFIER_PROMPT_PATH).read_text(encoding="utf-8")
    verifier_prompt = (data_root / VERIFIER_PROMPT_PATH).read_text(encoding="utf-8")
    prompt_sha256 = _sha256_text(classifier_prompt + "\n" + verifier_prompt)
    config = replace(agent_config or AgentConfig.from_env(), temperature=0.0)
    tools = build_hospital_2_tools(data_root, contract)
    runner = agent_runner or run_agent

    updated_entries = dict(existing_entries)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        candidates = {
            key: _candidate_payload(matcher, contract, key) for key in batch
        }
        try:
            classifier = _run_agent_pass(
                descriptions=batch,
                system_prompt=classifier_prompt,
                prompt_payload={
                    "normalized_descriptions": batch,
                    "candidate_contexts": candidates,
                },
                tools=tools,
                config=config,
                agent_runner=runner,
            )
            verifier = _run_agent_pass(
                descriptions=batch,
                system_prompt=verifier_prompt,
                prompt_payload={
                    "normalized_descriptions": batch,
                    "candidate_contexts": candidates,
                    "classifier_proposals": [classifier[key] for key in batch],
                },
                tools=tools,
                config=config,
                agent_runner=runner,
            )
        except (AgentError, ValueError) as exc:
            for key in batch:
                if refresh and existing_entries.get(key, {}).get("status") == "accepted":
                    continue
                updated_entries[key] = _unresolved_entry(
                    key,
                    examples[key],
                    candidates[key],
                    f"{type(exc).__name__}: {exc}",
                )
            continue

        for key in batch:
            updated_entries[key] = _validated_entry(
                key,
                examples[key],
                candidates[key],
                classifier[key],
                verifier[key],
                contract,
            )

    payload: dict[str, Any] = {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "hospital": 2,
        "contract_sha256": contract.source_sha256,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "model": config.model,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "minimum_confidence": MINIMUM_LLM_CONFIDENCE,
            "requires_classifier_verifier_agreement": True,
            "candidate_limit": 5,
            "billed_price_excluded": True,
        },
        "mappings": [updated_entries[key] for key in sorted(updated_entries)],
    }
    write_json_atomic(output_path, payload)
    return payload
