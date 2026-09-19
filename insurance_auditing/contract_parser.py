from __future__ import annotations

import hashlib
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
    ScheduledRate,
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


def _table_rows_after_heading(markdown: str, heading: str) -> list[list[str]]:
    in_section = False
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        if line == heading:
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
        raise ValueError(f"section {heading!r} contains no Markdown table")
    return rows[1:]


def _table_rows_until_heading(
    markdown: str,
    heading: str,
    next_heading: str,
) -> list[list[str]]:
    try:
        section = markdown.split(heading, 1)[1].split(next_heading, 1)[0]
    except IndexError as exc:
        raise ValueError(
            f"table boundaries are missing: {heading!r} to {next_heading!r}"
        ) from exc
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        raise ValueError(f"section {heading!r} contains no Markdown table")
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


def _article_text(markdown: str, article: str) -> str:
    match = re.search(
        rf"^## Article {re.escape(article)}\b.*?(?=^## Article |\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"Article {article} is missing")
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


_HOSPITAL_2_BASIS_PATTERN = "|".join(
    re.escape(value)
    for value in sorted(_UNIT_BASIS, key=len, reverse=True)
)
_HOSPITAL_2_SERVICE = re.compile(
    rf"^(?P<clause>\d+\.\d+) In respect of (?P<service>.*?), "
    rf"the Provider shall invoice the Payer at the rate of "
    rf"(?P<rate>GBP [\d,]+\.\d{{2}}) "
    rf"(?P<basis>{_HOSPITAL_2_BASIS_PATTERN})\.(?P<body>.*)$",
    flags=re.MULTILINE,
)


def _require_hospital_2_count(name: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise ValueError(
            f"Hospital 2 must contain {expected} {name}, found {actual}"
        )


def load_hospital_2_contract(path: Path | str) -> ContractRules:
    """Parse Hospital 2's prose-only agreement into executable rules.

    The agreement deliberately distributes rates across prose clauses. Every
    accepted rule is therefore tied to a numbered clause and the parser rejects
    partial or structurally unexpected extraction.
    """

    contract_path = Path(path)
    markdown = contract_path.read_text(encoding="utf-8")
    identity = contract_for_hospital(2)
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
            f"Hospital 2 contract identity mismatch: expected {expected_identity!r}, "
            f"found {parsed_identity!r}"
        )
    if _metadata_value(markdown, "Currency") != "GBP":
        raise ValueError("Hospital 2 uses an unsupported currency")
    if _metadata_value(markdown, "Rounding convention") != "half_up_cent":
        raise ValueError("Hospital 2 uses an unsupported rounding convention")

    scope = _article_text(markdown, "I")
    if "Main Campus (F-MAIN)" not in scope or "No facility differential" not in scope:
        raise ValueError("Hospital 2 facility scope is missing or unsupported")
    if "irrespective of the Patient's plan tier" not in scope:
        raise ValueError("Hospital 2 plan-tier scope is missing or unsupported")

    conventions = _article_text(markdown, "III")
    order_markers = (
        "substitution of a bundled rate",
        "the facility multiplier",
        "the plan-tier multiplier",
        "any premium or uplift",
        "any cumulative volume discount",
    )
    positions = [conventions.find(marker) for marker in order_markers]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("Hospital 2 adjustment order is missing or unsupported")
    if "Rounding is applied after each individual step" not in conventions:
        raise ValueError("Hospital 2 per-step rounding rule is missing or unsupported")
    if "exclusion window is measured in either direction" not in conventions:
        raise ValueError("Hospital 2 exclusion-window convention is unsupported")

    matches = list(_HOSPITAL_2_SERVICE.finditer(markdown))
    _require_hospital_2_count("service clauses", len(matches), 76)
    services: dict[str, ServiceRule] = {}
    clause_bodies: dict[str, tuple[str, str]] = {}
    for match in matches:
        clause_id = match.group("clause")
        service = match.group("service")
        basis = match.group("basis")
        if service in services:
            raise ValueError(f"duplicate Hospital 2 service: {service}")
        services[service] = ServiceRule(
            name=service,
            unit_basis=_UNIT_BASIS[basis],
            rate_cents=_money_to_cents(match.group("rate")),
            clause_id=clause_id,
        )
        clause_bodies[service] = (clause_id, match.group("body"))

    cap_pattern = re.compile(
        r"shall not bill more than .*?\((\d+)\) [^.]+? of this Service"
    )
    premium_pattern = re.compile(
        r"aggregate quantity of this Service.*?exceeds .*?\((\d+)\) [^,]+, "
        r"the rate.*?increased by .*?\((\d+)%\)"
    )
    weekend_pattern = re.compile(
        r"does not fall on a Business Day, the rate.*?increased by .*?\((\d+)%\)"
    )
    discount_pattern = re.compile(
        r"cumulative utilisation of this Service exceeds .*?\((\d+)\) [^,]+,"
        r".*?discount of .*?\((\d+)%\)"
    )
    exclusion_pattern = re.compile(
        r"This Service is not billable where (.*?) has been delivered to the same "
        r"Patient within .*?\((\d+)\) days"
    )
    bundle_pattern = re.compile(
        rf"Where this Service and (.*?) are both delivered.*?this Service at "
        rf"(GBP [\d,]+\.\d{{2}}) ({_HOSPITAL_2_BASIS_PATTERN}) and .*? at "
        rf"(GBP [\d,]+\.\d{{2}}) ({_HOSPITAL_2_BASIS_PATTERN}), in substitution"
    )

    premiums: dict[str, ThresholdPremium] = {}
    weekend_uplifts: dict[str, Decimal] = {}
    discounts: dict[str, list[VolumeDiscount]] = defaultdict(list)
    exclusions: list[ExclusionRule] = []
    bundle_mentions: dict[
        tuple[str, str], list[tuple[str, dict[str, int]]]
    ] = defaultdict(list)
    cap_count = 0
    discount_count = 0

    for service, (clause_id, body) in clause_bodies.items():
        cap_matches = list(cap_pattern.finditer(body))
        if len(cap_matches) > 1:
            raise ValueError(f"multiple Hospital 2 caps in clause {clause_id}")
        if cap_matches:
            cap_count += 1
            services[service] = replace(
                services[service], daily_cap=int(cap_matches[0].group(1))
            )

        premium_matches = list(premium_pattern.finditer(body))
        if len(premium_matches) > 1:
            raise ValueError(f"multiple Hospital 2 premiums in clause {clause_id}")
        if premium_matches:
            threshold, percent = premium_matches[0].groups()
            premiums[service] = ThresholdPremium(
                service,
                int(threshold),
                Decimal(100 + int(percent)) / Decimal(100),
                clause_id,
            )

        weekend_matches = list(weekend_pattern.finditer(body))
        if len(weekend_matches) > 1:
            raise ValueError(f"multiple Hospital 2 weekend uplifts in clause {clause_id}")
        if weekend_matches:
            weekend_uplifts[service] = (
                Decimal(100 + int(weekend_matches[0].group(1))) / Decimal(100)
            )

        for discount_match in discount_pattern.finditer(body):
            threshold, percent = discount_match.groups()
            discounts[service].append(
                VolumeDiscount(
                    service,
                    int(threshold),
                    Decimal(100 - int(percent)) / Decimal(100),
                    clause_id,
                )
            )
            discount_count += 1

        exclusion_matches = list(exclusion_pattern.finditer(body))
        if len(exclusion_matches) > 1:
            raise ValueError(f"multiple Hospital 2 exclusions in clause {clause_id}")
        if exclusion_matches:
            trigger, window = exclusion_matches[0].groups()
            exclusions.append(ExclusionRule(service, int(window), trigger, clause_id))

        bundle_matches = list(bundle_pattern.finditer(body))
        if len(bundle_matches) > 1:
            raise ValueError(f"multiple Hospital 2 bundles in clause {clause_id}")
        if bundle_matches:
            partner, own_rate, own_basis, partner_rate, partner_basis = (
                bundle_matches[0].groups()
            )
            if partner not in services:
                raise ValueError(
                    f"Hospital 2 bundle in clause {clause_id} references "
                    f"unknown service: {partner}"
                )
            if _UNIT_BASIS[own_basis] != services[service].unit_basis:
                raise ValueError(f"Hospital 2 bundle basis mismatch in clause {clause_id}")
            if _UNIT_BASIS[partner_basis] != services[partner].unit_basis:
                raise ValueError(f"Hospital 2 partner basis mismatch in clause {clause_id}")
            pair = tuple(sorted((service, partner)))
            bundle_mentions[pair].append(
                (
                    clause_id,
                    {
                        service: _money_to_cents(own_rate),
                        partner: _money_to_cents(partner_rate),
                    },
                )
            )

    _require_hospital_2_count("daily caps", cap_count, 8)
    _require_hospital_2_count("threshold premiums", len(premiums), 9)
    _require_hospital_2_count("weekend uplifts", len(weekend_uplifts), 8)
    # The source contains twelve actual discount thresholds. Article III also
    # describes discount ordering, but it is not itself a threshold rule.
    _require_hospital_2_count("volume-discount thresholds", discount_count, 12)
    _require_hospital_2_count(
        "bundle mentions", sum(map(len, bundle_mentions.values())), 6
    )
    _require_hospital_2_count("bundle pairs", len(bundle_mentions), 3)
    _require_hospital_2_count("exclusions", len(exclusions), 6)

    bundles: list[BundleRule] = []
    for pair, mentions in sorted(bundle_mentions.items()):
        if len(mentions) != 2 or mentions[0][1] != mentions[1][1]:
            raise ValueError(
                f"Hospital 2 bundle must be symmetric and consistent: {pair!r}"
            )
        rates = mentions[0][1]
        bundles.append(
            BundleRule(
                pair[0],
                pair[1],
                rates[pair[0]],
                rates[pair[1]],
                tuple(sorted(clause_id for clause_id, _ in mentions)),
            )
        )

    immutable_discounts = {
        service: tuple(sorted(rules, key=lambda rule: rule.threshold))
        for service, rules in discounts.items()
    }
    immutable_exclusions = tuple(exclusions)
    immutable_bundles = tuple(bundles)
    _validate_rule_references(
        services,
        premiums,
        weekend_uplifts,
        immutable_discounts,
        immutable_bundles,
        immutable_exclusions,
    )

    return ContractRules(
        identity=identity,
        services=services,
        threshold_premiums=premiums,
        non_business_day_uplifts=weekend_uplifts,
        volume_discounts=immutable_discounts,
        bundles=immutable_bundles,
        exclusions=immutable_exclusions,
        pricing_pipeline=DEFAULT_PRICING_PIPELINE,
        duplicate_billing_policy=DuplicateBillingPolicy.MATCHING_LINE_ACROSS_INVOICES,
        source_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    )


def load_hospital_3_contract(path: Path | str) -> ContractRules:
    """Merge Hospital 3's base agreement, rate appendix, and 2025 amendment."""

    contract_directory = Path(path)
    base = (contract_directory / "base_agreement.md").read_text(encoding="utf-8")
    appendix = (contract_directory / "appendix_b_rate_schedule.md").read_text(
        encoding="utf-8"
    )
    amendment = (contract_directory / "amendment_no_1.md").read_text(
        encoding="utf-8"
    )
    identity = contract_for_hospital(3)
    expected_identity = (
        identity.contract_number,
        identity.effective_from,
        identity.effective_to,
    )
    for name, markdown in (
        ("base agreement", base),
        ("Appendix B", appendix),
        ("Amendment No. 1", amendment),
    ):
        parsed_identity = (
            _metadata_value(markdown, "Contract number"),
            _contract_date(_metadata_value(markdown, "Effective from")),
            _contract_date(_metadata_value(markdown, "Effective to")),
        )
        if parsed_identity != expected_identity:
            raise ValueError(
                f"Hospital 3 {name} identity mismatch: expected "
                f"{expected_identity!r}, found {parsed_identity!r}"
            )
        if _metadata_value(markdown, "Currency") != "GBP":
            raise ValueError(f"Hospital 3 {name} uses an unsupported currency")
        if _metadata_value(markdown, "Rounding convention") != "half_up_cent":
            raise ValueError(
                f"Hospital 3 {name} uses an unsupported rounding convention"
            )

    scope = _section_text(base, 1)
    if "Main Campus (F-MAIN)" not in scope or "No facility differential" not in scope:
        raise ValueError("Hospital 3 facility scope is missing or unsupported")
    if "all plan tiers are reimbursed identically" not in scope:
        raise ValueError("Hospital 3 plan-tier scope is missing or unsupported")

    conventions = _section_text(base, 3)
    order_markers = (
        "substitution of a bundled rate",
        "the facility multiplier",
        "the plan-tier multiplier",
        "any premium or uplift",
        "any cumulative volume discount",
    )
    positions = [conventions.find(marker) for marker in order_markers]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("Hospital 3 adjustment order is missing or unsupported")
    if "Rounding is applied after each individual step" not in conventions:
        raise ValueError("Hospital 3 per-step rounding rule is missing or unsupported")

    rate_rows = _table_rows_after_heading(appendix, "## B.1 Rates")
    if len(rate_rows) != 118:
        raise ValueError(
            f"Hospital 3 Appendix B must contain 118 services, found {len(rate_rows)}"
        )
    services: dict[str, ServiceRule] = {}
    for service, basis, rate, cap in rate_rows:
        if service in services:
            raise ValueError(f"duplicate Hospital 3 service: {service}")
        if basis not in _UNIT_BASIS:
            raise ValueError(f"unknown Hospital 3 unit basis for {service}: {basis!r}")
        services[service] = ServiceRule(
            name=service,
            unit_basis=_UNIT_BASIS[basis],
            rate_cents=_money_to_cents(rate),
            daily_cap=None if cap == "—" else _quantity(cap),
            clause_id="B.1",
        )

    amendment_date = _contract_date("1 January 2025")
    if "takes effect on 1 January 2025" not in amendment:
        raise ValueError("Hospital 3 amendment effective date is missing")
    if "applies **by Service Date**" not in amendment:
        raise ValueError("Hospital 3 amendment must apply by Service Date")

    substituted_rows = _table_rows_after_heading(
        amendment, "## A1.2 Substituted Rates"
    )
    if len(substituted_rows) != 7:
        raise ValueError("Hospital 3 amendment must contain 7 substituted rates")
    for service, basis, old_rate, new_rate in substituted_rows:
        if service not in services:
            raise ValueError(f"Hospital 3 amendment references unknown service: {service}")
        existing = services[service]
        if _UNIT_BASIS.get(basis) != existing.unit_basis:
            raise ValueError(f"Hospital 3 amendment basis mismatch for {service}")
        if _money_to_cents(old_rate) != existing.rate_cents:
            raise ValueError(f"Hospital 3 amendment prior rate mismatch for {service}")
        services[service] = replace(
            existing,
            scheduled_rates=(
                ScheduledRate(amendment_date, _money_to_cents(new_rate), "A1.2"),
            ),
        )

    additional_rows = _table_rows_after_heading(
        amendment, "## A1.3 Additional Services"
    )
    if len(additional_rows) != 2:
        raise ValueError("Hospital 3 amendment must contain 2 additional services")
    for service, basis, rate in additional_rows:
        if service in services:
            raise ValueError(f"duplicate Hospital 3 amended service: {service}")
        if basis not in _UNIT_BASIS:
            raise ValueError(f"unknown Hospital 3 unit basis for {service}: {basis!r}")
        services[service] = ServiceRule(
            name=service,
            unit_basis=_UNIT_BASIS[basis],
            rate_cents=_money_to_cents(rate),
            clause_id="A1.3",
            effective_from=amendment_date,
        )

    premium_rows = _table_rows(base, 4)
    if len(premium_rows) != 14:
        raise ValueError("Hospital 3 must contain 14 threshold premiums")
    premiums = {
        service: ThresholdPremium(
            service,
            _quantity(threshold),
            _uplift_multiplier(uplift),
            "4",
        )
        for service, threshold, uplift in premium_rows
    }

    weekend_rows = _table_rows(base, 5)
    if len(weekend_rows) != 12:
        raise ValueError("Hospital 3 must contain 12 weekend uplifts")
    weekend_uplifts = {
        service: _uplift_multiplier(uplift)
        for service, uplift in weekend_rows
    }

    discount_rows = _table_rows(base, 6)
    if len(discount_rows) != 19:
        raise ValueError("Hospital 3 must contain 19 volume-discount thresholds")
    discounts: dict[str, list[VolumeDiscount]] = defaultdict(list)
    for service, threshold, discount in discount_rows:
        discounts[service].append(
            VolumeDiscount(
                service,
                _quantity(threshold),
                _discount_multiplier(discount),
                "6",
            )
        )
    immutable_discounts = {
        service: tuple(sorted(rules, key=lambda rule: rule.threshold))
        for service, rules in discounts.items()
    }

    cap_rows = _table_rows(base, 7)
    if len(cap_rows) != 12:
        raise ValueError("Hospital 3 must contain 12 daily caps")
    for service, cap in cap_rows:
        if service not in services:
            raise ValueError(f"Hospital 3 cap references unknown service: {service}")
        parsed_cap = _quantity(cap)
        if services[service].daily_cap != parsed_cap:
            raise ValueError(f"Hospital 3 cap mismatch for {service}")

    bundle_rows = _table_rows(base, 8)
    if len(bundle_rows) != 5:
        raise ValueError("Hospital 3 must contain 5 bundle pairs")
    bundles = tuple(
        BundleRule(
            service_a,
            service_b,
            _money_to_cents(rate_a),
            _money_to_cents(rate_b),
            ("8",),
        )
        for service_a, service_b, rate_a, rate_b in bundle_rows
    )

    exclusion_rows = _table_rows(base, 9)
    if len(exclusion_rows) != 10:
        raise ValueError("Hospital 3 must contain 10 exclusion windows")
    exclusions = tuple(
        ExclusionRule(excluded, _quantity(window), trigger, "9")
        for excluded, window, trigger in exclusion_rows
    )

    _validate_rule_references(
        services,
        premiums,
        weekend_uplifts,
        immutable_discounts,
        bundles,
        exclusions,
    )
    source = "\n".join((base, appendix, amendment))
    return ContractRules(
        identity=identity,
        services=services,
        threshold_premiums=premiums,
        non_business_day_uplifts=weekend_uplifts,
        volume_discounts=immutable_discounts,
        bundles=bundles,
        exclusions=exclusions,
        pricing_pipeline=DEFAULT_PRICING_PIPELINE,
        duplicate_billing_policy=DuplicateBillingPolicy.REPEATED_SERVICE_PER_PATIENT_DAY,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


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


def load_hospital_5_contract(path: Path | str) -> ContractRules:
    """Parse Hospital 5's network, tier, and conditional reimbursement rules."""

    contract_path = Path(path)
    markdown = contract_path.read_text(encoding="utf-8")
    identity = contract_for_hospital(5)
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
            f"Hospital 5 contract identity mismatch: expected {expected_identity!r}, "
            f"found {parsed_identity!r}"
        )
    if _metadata_value(markdown, "Currency") != "GBP":
        raise ValueError("Hospital 5 uses an unsupported currency")
    if _metadata_value(markdown, "Rounding convention") != "half_up_cent":
        raise ValueError("Hospital 5 uses an unsupported rounding convention")

    calculation = _section_text(markdown, 3)
    order_markers = (
        "substitution of a bundled rate",
        "the facility multiplier",
        "the plan-tier multiplier",
        "any premium or uplift",
        "any cumulative volume discount",
    )
    positions = [calculation.find(marker) for marker in order_markers]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("Hospital 5 adjustment order is missing or unsupported")
    if "Rounding is applied after each individual step" not in calculation:
        raise ValueError("Hospital 5 per-step rounding rule is missing or unsupported")

    service_rows = _table_rows_until_heading(
        markdown,
        "## 4. Table 1 — Base Rates",
        "### Table 2 — Facility Multipliers",
    )
    if len(service_rows) != 84:
        raise ValueError(
            f"Hospital 5 must contain 84 base-rate rows, found {len(service_rows)}"
        )
    services: dict[str, ServiceRule] = {}
    for service, basis, rate, cap in service_rows:
        if service in services:
            raise ValueError(f"duplicate Hospital 5 base-rate service: {service}")
        if basis not in _UNIT_BASIS:
            raise ValueError(f"unknown Hospital 5 unit basis for {service}: {basis!r}")
        services[service] = ServiceRule(
            name=service,
            unit_basis=_UNIT_BASIS[basis],
            rate_cents=_money_to_cents(rate),
            daily_cap=None if cap == "—" else _quantity(cap),
            clause_id="4",
        )
    if sum(rule.daily_cap is not None for rule in services.values()) != 9:
        raise ValueError("Hospital 5 must contain 9 daily caps")

    facility_rows = _table_rows_until_heading(
        markdown,
        "### Table 2 — Facility Multipliers",
        "### Table 3 — Plan-Tier Multipliers",
    )
    if len(facility_rows) != 84:
        raise ValueError("Hospital 5 must contain 84 facility-multiplier rows")
    facility_multipliers: dict[str, dict[str, Decimal]] = {}
    for service, main, north, coast in facility_rows:
        if service in facility_multipliers:
            raise ValueError(f"duplicate Hospital 5 facility row: {service}")
        facility_multipliers[service] = {
            "F-MAIN": Decimal(main),
            "F-NORTH": Decimal(north),
            "F-COAST": Decimal(coast),
        }

    tier_rows = _table_rows_until_heading(
        markdown,
        "### Table 3 — Plan-Tier Multipliers",
        "## 5. Threshold Premiums",
    )
    if len(tier_rows) != 84:
        raise ValueError("Hospital 5 must contain 84 plan-tier-multiplier rows")
    plan_tier_multipliers: dict[str, dict[str, Decimal]] = {}
    for service, bronze, silver, gold in tier_rows:
        if service in plan_tier_multipliers:
            raise ValueError(f"duplicate Hospital 5 plan-tier row: {service}")
        plan_tier_multipliers[service] = {
            "BRONZE": Decimal(bronze),
            "SILVER": Decimal(silver),
            "GOLD": Decimal(gold),
        }
    if set(facility_multipliers) != set(services):
        raise ValueError("Hospital 5 facility table does not match the base-rate table")
    if set(plan_tier_multipliers) != set(services):
        raise ValueError("Hospital 5 plan-tier table does not match the base-rate table")

    premium_rows = _table_rows(markdown, 5)
    if len(premium_rows) != 10:
        raise ValueError("Hospital 5 must contain 10 threshold premiums")
    premiums: dict[str, ThresholdPremium] = {}
    for service, threshold, uplift in premium_rows:
        if service in premiums:
            raise ValueError(f"duplicate Hospital 5 threshold premium: {service}")
        premiums[service] = ThresholdPremium(
            service,
            _quantity(threshold),
            _uplift_multiplier(uplift),
            "5",
        )

    weekend_rows = _table_rows(markdown, 6)
    if len(weekend_rows) != 9:
        raise ValueError("Hospital 5 must contain 9 non-business-day uplifts")
    weekend_uplifts: dict[str, Decimal] = {}
    for service, uplift in weekend_rows:
        if service in weekend_uplifts:
            raise ValueError(f"duplicate Hospital 5 weekend uplift: {service}")
        weekend_uplifts[service] = _uplift_multiplier(uplift)
    if set(premiums) & set(weekend_uplifts):
        raise ValueError("Hospital 5 premium and weekend uplift services overlap")

    bundle_rows = _table_rows(markdown, 7)
    if len(bundle_rows) != 3:
        raise ValueError("Hospital 5 must contain 3 bundle pairs")
    bundles_list: list[BundleRule] = []
    bundled_services: set[str] = set()
    for service_a, rate_a, service_b, rate_b in bundle_rows:
        repeated = {service_a, service_b} & bundled_services
        if repeated:
            raise ValueError(
                "Hospital 5 service appears in more than one bundle: "
                f"{sorted(repeated)}"
            )
        bundled_services.update((service_a, service_b))
        bundles_list.append(
            BundleRule(
                service_a,
                service_b,
                _money_to_cents(rate_a),
                _money_to_cents(rate_b),
                ("7",),
            )
        )
    bundles = tuple(bundles_list)

    discount_rows = _table_rows(markdown, 8)
    if len(discount_rows) != 15:
        raise ValueError("Hospital 5 must contain 15 volume-discount thresholds")
    discounts: dict[str, list[VolumeDiscount]] = defaultdict(list)
    seen_discount_thresholds: set[tuple[str, int]] = set()
    for service, threshold, discount in discount_rows:
        parsed_threshold = _quantity(threshold)
        key = (service, parsed_threshold)
        if key in seen_discount_thresholds:
            raise ValueError(f"duplicate Hospital 5 volume discount: {key!r}")
        seen_discount_thresholds.add(key)
        discounts[service].append(
            VolumeDiscount(
                service,
                parsed_threshold,
                _discount_multiplier(discount),
                "8",
            )
        )
    immutable_discounts = {
        service: tuple(sorted(rules, key=lambda rule: rule.threshold))
        for service, rules in discounts.items()
    }

    exclusion_rows = _table_rows(markdown, 9)
    if len(exclusion_rows) != 7:
        raise ValueError("Hospital 5 must contain 7 exclusion windows")
    exclusions_list: list[ExclusionRule] = []
    seen_exclusions: set[tuple[str, int, str]] = set()
    for excluded, window, trigger in exclusion_rows:
        key = (excluded, _quantity(window), trigger)
        if key in seen_exclusions:
            raise ValueError(f"duplicate Hospital 5 exclusion rule: {key!r}")
        seen_exclusions.add(key)
        exclusions_list.append(ExclusionRule(*key, "9"))
    exclusions = tuple(exclusions_list)

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
        facility_multipliers=facility_multipliers,
        plan_tier_multipliers=plan_tier_multipliers,
        pricing_pipeline=DEFAULT_PRICING_PIPELINE,
        duplicate_billing_policy=(
            DuplicateBillingPolicy.REPEATED_SERVICE_PER_PATIENT_DAY
        ),
        source_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    )
