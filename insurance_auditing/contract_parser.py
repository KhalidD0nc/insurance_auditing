from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .contracts import contract_for_hospital
from .models import (
    BundleRule,
    ContractRules,
    DEFAULT_PRICING_PIPELINE,
    DuplicateBillingPolicy,
    ExclusionRule,
    ServiceRule,
    ThresholdPremium,
    VolumeDiscount,
)


_UNIT_BASIS = {
    "per hour": "per_hour",
    "per day of service": "per_day",
    "per visit": "per_visit",
    "per procedure": "per_procedure",
    "per test": "per_test",
    "per item supplied": "per_item",
    "per night of occupancy": "per_night",
    "per unit dispensed": "per_unit_dispensed",
    "per hour, per item": "per_hour_per_item",
}


def _table_rows(markdown: str, section_number: int) -> list[list[str]]:
    in_section = False
    rows: list[list[str]] = []
    heading = f"## {section_number}."
    for line in markdown.splitlines():
        if line.startswith(heading):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        raise ValueError(f"section {section_number} contains no Markdown table")
    return rows[1:]


def _money_to_cents(value: str) -> int:
    match = re.fullmatch(r"GBP\s+([\d,]+)\.(\d{2})", value)
    if not match:
        raise ValueError(f"invalid money value: {value!r}")
    pounds = int(match.group(1).replace(",", ""))
    return pounds * 100 + int(match.group(2))


def _quantity(value: str) -> int:
    match = re.search(r"\d+", value)
    if not match:
        raise ValueError(f"quantity missing from: {value!r}")
    return int(match.group())


def _uplift_multiplier(value: str) -> Decimal:
    percent = _quantity(value)
    return Decimal(100 + percent) / Decimal(100)


def _discount_multiplier(value: str) -> Decimal:
    percent = _quantity(value)
    return Decimal(100 - percent) / Decimal(100)


def _metadata_value(markdown: str, label: str) -> str:
    match = re.search(
        rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$",
        markdown,
        flags=re.MULTILINE,
    )
    if not match:
        raise ValueError(f"contract metadata is missing {label!r}")
    return match.group(1)


def _contract_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d %B %Y").date()
    except ValueError as exc:
        raise ValueError(f"invalid contract date: {value!r}") from exc


def _section_text(markdown: str, section_number: int) -> str:
    match = re.search(
        rf"^## {section_number}\..*?(?=^## |\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"section {section_number} is missing")
    return match.group()


def _validate_rule_references(
    services: dict[str, ServiceRule],
    premiums: dict[str, ThresholdPremium],
    weekend_uplifts: dict[str, Decimal],
    discounts: dict[str, tuple[VolumeDiscount, ...]],
    bundles: tuple[BundleRule, ...],
    exclusions: tuple[ExclusionRule, ...],
) -> None:
    unknown_references = (
        set(premiums)
        | set(weekend_uplifts)
        | set(discounts)
        | {bundle.service_a for bundle in bundles}
        | {bundle.service_b for bundle in bundles}
        | {rule.excluded_service for rule in exclusions}
        | {rule.trigger_service for rule in exclusions}
    ) - services.keys()
    if unknown_references:
        raise ValueError(
            f"contract rules reference unknown services: {sorted(unknown_references)}"
        )


def _require_count(name: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise ValueError(f"Hospital 4 must contain {expected} {name}, found {actual}")


def load_hospital_1_contract(path: Path | str) -> ContractRules:
    markdown = Path(path).read_text(encoding="utf-8")
    services: dict[str, ServiceRule] = {}
    for service, basis, rate, cap in _table_rows(markdown, 4):
        if basis not in _UNIT_BASIS:
            raise ValueError(f"unknown unit basis for {service}: {basis!r}")
        services[service] = ServiceRule(
            name=service,
            unit_basis=_UNIT_BASIS[basis],
            rate_cents=_money_to_cents(rate),
            daily_cap=None if cap == "—" else _quantity(cap),
        )

    premiums = {
        service: ThresholdPremium(service, _quantity(threshold), _uplift_multiplier(uplift))
        for service, threshold, uplift in _table_rows(markdown, 5)
    }
    weekend_uplifts = {
        service: _uplift_multiplier(uplift)
        for service, uplift in _table_rows(markdown, 6)
    }
    discounts: dict[str, list[VolumeDiscount]] = defaultdict(list)
    for service, threshold, discount in _table_rows(markdown, 7):
        discounts[service].append(
            VolumeDiscount(service, _quantity(threshold), _discount_multiplier(discount))
        )
    for rules in discounts.values():
        rules.sort(key=lambda rule: rule.threshold)

    bundles = tuple(
        BundleRule(service_a, service_b, _money_to_cents(rate_a), _money_to_cents(rate_b))
        for service_a, service_b, rate_a, rate_b in _table_rows(markdown, 9)
    )
    exclusions = tuple(
        ExclusionRule(excluded, _quantity(window), trigger)
        for excluded, window, trigger in _table_rows(markdown, 10)
    )

    _validate_rule_references(
        services,
        premiums,
        weekend_uplifts,
        {service: tuple(rules) for service, rules in discounts.items()},
        bundles,
        exclusions,
    )

    return ContractRules(
        identity=contract_for_hospital(1),
        services=services,
        threshold_premiums=premiums,
        non_business_day_uplifts=weekend_uplifts,
        volume_discounts={service: tuple(rules) for service, rules in discounts.items()},
        bundles=bundles,
        exclusions=exclusions,
    )


def load_hospital_4_contract(path: Path | str) -> ContractRules:
    markdown = Path(path).read_text(encoding="utf-8")
    identity = contract_for_hospital(4)

    parsed_identity = (
        _metadata_value(markdown, "Contract number"),
        _contract_date(_metadata_value(markdown, "Effective from")),
        _contract_date(_metadata_value(markdown, "Effective to")),
    )
    expected_identity = (
        identity.contract_number,
        identity.effective_from,
        identity.effective_to,
    )
    if parsed_identity != expected_identity:
        raise ValueError(
            f"Hospital 4 contract identity mismatch: expected {expected_identity!r}, "
            f"found {parsed_identity!r}"
        )
    if _metadata_value(markdown, "Currency") != "GBP":
        raise ValueError("Hospital 4 uses an unsupported currency")
    if _metadata_value(markdown, "Rounding convention") != "half_up_cent":
        raise ValueError("Hospital 4 uses an unsupported rounding convention")

    scope = _section_text(markdown, 2)
    if "Main Campus (F-MAIN)" not in scope or "no facility differential" not in scope:
        raise ValueError("Hospital 4 facility scope is missing or unsupported")
    if "plan tier does not affect the rate" not in scope:
        raise ValueError("Hospital 4 plan-tier scope is missing or unsupported")

    adjustment_order = _section_text(markdown, 4)
    order_markers = (
        "substitution of a bundled rate",
        "the facility multiplier",
        "the plan-tier multiplier",
        "any premium or uplift",
        "any cumulative volume discount",
    )
    positions = [adjustment_order.find(marker) for marker in order_markers]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("Hospital 4 adjustment order is missing or unsupported")
    if "Rounding is applied after each individual step" not in adjustment_order:
        raise ValueError("Hospital 4 per-step rounding rule is missing or unsupported")

    service_rows = _table_rows(markdown, 3)
    _require_count("base-rate rows", len(service_rows), 98)
    services: dict[str, ServiceRule] = {}
    for service, basis, rate in service_rows:
        if service in services:
            raise ValueError(f"duplicate Hospital 4 base-rate service: {service}")
        if basis not in _UNIT_BASIS:
            raise ValueError(f"unknown unit basis for {service}: {basis!r}")
        services[service] = ServiceRule(
            name=service,
            unit_basis=_UNIT_BASIS[basis],
            rate_cents=_money_to_cents(rate),
        )

    cap_rows = _table_rows(markdown, 6)
    _require_count("daily-cap rows", len(cap_rows), 18)
    seen_caps: set[str] = set()
    for service, cap in cap_rows:
        if service in seen_caps:
            raise ValueError(f"duplicate Hospital 4 daily cap: {service}")
        if service not in services:
            raise ValueError(f"Hospital 4 daily cap references unknown service: {service}")
        seen_caps.add(service)
        services[service] = replace(services[service], daily_cap=_quantity(cap))

    premium_rows = _table_rows(markdown, 5)
    _require_count("threshold-premium rows", len(premium_rows), 18)
    premiums: dict[str, ThresholdPremium] = {}
    for service, threshold, uplift in premium_rows:
        if service in premiums:
            raise ValueError(f"duplicate Hospital 4 threshold premium: {service}")
        premiums[service] = ThresholdPremium(
            service,
            _quantity(threshold),
            _uplift_multiplier(uplift),
        )

    discount_rows = _table_rows(markdown, 8)
    _require_count("volume-discount rows", len(discount_rows), 4)
    discounts: dict[str, list[VolumeDiscount]] = defaultdict(list)
    seen_discount_thresholds: set[tuple[str, int]] = set()
    for service, threshold_text, discount in discount_rows:
        threshold = _quantity(threshold_text)
        key = (service, threshold)
        if key in seen_discount_thresholds:
            raise ValueError(f"duplicate Hospital 4 volume discount: {key!r}")
        seen_discount_thresholds.add(key)
        discounts[service].append(
            VolumeDiscount(service, threshold, _discount_multiplier(discount))
        )
    for rules in discounts.values():
        rules.sort(key=lambda rule: rule.threshold)
    immutable_discounts = {
        service: tuple(rules) for service, rules in discounts.items()
    }

    bundle_rows = _table_rows(markdown, 7)
    _require_count("bundled-service rows", len(bundle_rows), 7)
    bundles_list: list[BundleRule] = []
    bundled_services: set[str] = set()
    for service_a, rate_a, service_b, rate_b in bundle_rows:
        repeated_services = {service_a, service_b} & bundled_services
        if repeated_services:
            raise ValueError(
                "Hospital 4 service appears in more than one bundle: "
                f"{sorted(repeated_services)}"
            )
        bundled_services.update((service_a, service_b))
        bundles_list.append(
            BundleRule(
                service_a,
                service_b,
                _money_to_cents(rate_a),
                _money_to_cents(rate_b),
            )
        )
    bundles = tuple(bundles_list)

    exclusion_rows = _table_rows(markdown, 9)
    _require_count("exclusion-window rows", len(exclusion_rows), 15)
    exclusions_list: list[ExclusionRule] = []
    seen_exclusions: set[tuple[str, int, str]] = set()
    for excluded, window, trigger in exclusion_rows:
        rule_key = (excluded, _quantity(window), trigger)
        if rule_key in seen_exclusions:
            raise ValueError(f"duplicate Hospital 4 exclusion rule: {rule_key!r}")
        seen_exclusions.add(rule_key)
        exclusions_list.append(ExclusionRule(*rule_key))
    exclusions = tuple(exclusions_list)

    non_business_days = _section_text(markdown, 10)
    if "_None._" not in non_business_days or "|" in non_business_days:
        raise ValueError("Hospital 4 non-business-day uplift section is unsupported")
    weekend_uplifts: dict[str, Decimal] = {}

    _validate_rule_references(
        services,
        premiums,
        weekend_uplifts,
        immutable_discounts,
        bundles,
        exclusions,
    )

    return ContractRules(
        identity=identity,
        services=services,
        threshold_premiums=premiums,
        non_business_day_uplifts=weekend_uplifts,
        volume_discounts=immutable_discounts,
        bundles=bundles,
        exclusions=exclusions,
        pricing_pipeline=DEFAULT_PRICING_PIPELINE,
        duplicate_billing_policy=(
            DuplicateBillingPolicy.REPEATED_SERVICE_PER_PATIENT_DAY
        ),
    )
