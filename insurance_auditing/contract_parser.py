from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .contracts import contract_for_hospital
from .models import (
    BundleRule,
    ContractRules,
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
        raise ValueError(f"contract rules reference unknown services: {sorted(unknown_references)}")

    return ContractRules(
        identity=contract_for_hospital(1),
        services=services,
        threshold_premiums=premiums,
        non_business_day_uplifts=weekend_uplifts,
        volume_discounts={service: tuple(rules) for service, rules in discounts.items()},
        bundles=bundles,
        exclusions=exclusions,
    )
