from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import ContractRules, ServiceMappingOverride
from .service_matching import ServiceMatcher, normalise_service_description


MAPPING_SCHEMA_VERSION = 1
DEFAULT_MAPPING_PATH = Path("mappings/hospital_5_service_mappings.json")


def load_hospital_5_mapping_artifact(
    path: Path,
    contract: ContractRules,
) -> tuple[dict[str, ServiceMappingOverride], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Hospital 5 mapping artifact does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Hospital 5 mapping artifact is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Hospital 5 mapping artifact must be an object")
    if payload.get("schema_version") != MAPPING_SCHEMA_VERSION:
        raise ValueError("unsupported Hospital 5 mapping schema version")
    if payload.get("hospital") != 5:
        raise ValueError("mapping artifact is not for Hospital 5")
    if not contract.source_sha256 or payload.get("contract_sha256") != contract.source_sha256:
        raise ValueError("Hospital 5 mapping artifact has a stale contract fingerprint")
    if payload.get("review_method") != "aggregate_network_rate_tiebreaker":
        raise ValueError("Hospital 5 mapping artifact has an unsupported review method")

    prompt_path = payload.get("prompt_path")
    prompt_sha256 = payload.get("prompt_sha256")
    if not isinstance(prompt_path, str) or not isinstance(prompt_sha256, str):
        raise ValueError("Hospital 5 mapping artifact is missing prompt provenance")
    resolved_prompt = path.parent.parent / prompt_path
    try:
        actual_prompt_sha256 = hashlib.sha256(resolved_prompt.read_bytes()).hexdigest()
    except FileNotFoundError as exc:
        raise ValueError(
            f"Hospital 5 mapping prompt does not exist: {resolved_prompt}"
        ) from exc
    if actual_prompt_sha256 != prompt_sha256:
        raise ValueError("Hospital 5 mapping prompt fingerprint is stale")

    entries = payload.get("mappings")
    if not isinstance(entries, list):
        raise ValueError("Hospital 5 mapping artifact must contain a mappings array")
    matcher = ServiceMatcher(contract)
    overrides: dict[str, ServiceMappingOverride] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Hospital 5 mapping entries must be objects")
        key = entry.get("normalized_description")
        service_name = entry.get("selected_service")
        confidence = entry.get("confidence")
        observations = entry.get("observations")
        if (
            not isinstance(key, str)
            or not key
            or normalise_service_description(key) != key
            or key in overrides
        ):
            raise ValueError("Hospital 5 mapping descriptions must be unique and normalized")
        if service_name not in contract.services:
            raise ValueError(f"Hospital 5 mapping references unknown service: {service_name!r}")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.80 <= confidence <= 0.95
        ):
            raise ValueError(f"Hospital 5 mapping confidence is invalid for {key!r}")
        if service_name not in {
            candidate for _, candidate in matcher.rank_candidates(key, limit=5)
        }:
            raise ValueError(f"Hospital 5 mapping selection is outside candidates for {key!r}")
        if (
            entry.get("evidence_clause_id") != contract.services[service_name].clause_id
            or not isinstance(entry.get("reason"), str)
            or not entry["reason"]
            or not isinstance(observations, dict)
            or not isinstance(observations.get("line_count"), int)
            or observations["line_count"] <= 0
            or observations.get("unit_basis") != contract.services[service_name].unit_basis
            or not isinstance(observations.get("observed_rates_cents"), list)
            or not observations["observed_rates_cents"]
            or not all(
                isinstance(rate, int) for rate in observations["observed_rates_cents"]
            )
        ):
            raise ValueError(f"Hospital 5 mapping evidence is invalid for {key!r}")
        overrides[key] = ServiceMappingOverride(service_name, float(confidence))
    return overrides, payload
