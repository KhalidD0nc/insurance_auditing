from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .audit import StructuralAuditor, _parse_date
from .models import (
    AuditFinding,
    ContractRules,
    DetailedAuditResult,
    DuplicateBillingPolicy,
    HospitalDataset,
    InvoiceRecord,
    LineAuditDetail,
    LineItem,
    PricingStage,
    RateCalculationStep,
    ServiceMatch,
    ServiceMappingOverride,
)
from .service_matching import ServiceMatcher


def _round_cents(amount: int, multiplier: Decimal) -> int:
    return int((Decimal(amount) * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(slots=True)
class _LineContext:
    item: LineItem
    invoice: InvoiceRecord
    match: ServiceMatch
    base_rate_cents: int | None = None
    expected_rate_cents: int | None = None
    expected_quantity: int | None = None
    excluded: bool = False
    duplicate_category: str | None = None
    bundle_applies: bool = False
    premium_multiplier: Decimal | None = None
    discount_multiplier: Decimal | None = None
    aggregate_daily_quantity: int | None = None
    cumulative_quantity_before: int | None = None
    applied_discount_threshold: int | None = None
    exclusion_window_days: int | None = None
    bundle_partner_line_ids: set[str] = field(default_factory=set)
    exclusion_trigger_line_ids: set[str] = field(default_factory=set)
    daily_cap_related_line_ids: set[str] = field(default_factory=set)
    duplicate_of_line_id: str | None = None
    include_in_output: bool = True


class ContractAuditor:
    def __init__(
        self,
        contract: ContractRules,
        service_mappings: Mapping[str, ServiceMappingOverride] | None = None,
        *,
        conservative_matching: bool = False,
    ) -> None:
        self.contract = contract
        self.matcher = ServiceMatcher(
            contract,
            service_mappings,
            minimum_margin=0.15 if conservative_matching else 0.0,
            allow_price_tiebreaker=not conservative_matching,
            allow_unit_basis_tiebreaker=conservative_matching,
            allow_expanded_abbreviations=conservative_matching,
        )
        self._validate_pricing_pipeline()

    def _validate_pricing_pipeline(self) -> None:
        configured = self.contract.pricing_pipeline
        expected = set(PricingStage)
        if len(configured) != len(expected) or set(configured) != expected:
            raise ValueError(
                "pricing pipeline must contain every pricing stage exactly once"
            )

    def audit(self, dataset: HospitalDataset) -> dict[str, AuditFinding]:
        return self.audit_detailed(dataset).findings

    def audit_detailed(self, dataset: HospitalDataset) -> DetailedAuditResult:
        structural = StructuralAuditor(self.contract.identity).audit(dataset)
        resolver = StructuralAuditor(self.contract.identity)
        resolved = resolver.resolve(dataset)
        invoices_by_id: dict[str, list[InvoiceRecord]] = defaultdict(list)
        lines_by_id: dict[str, list[LineItem]] = defaultdict(list)
        for invoice in dataset.invoices:
            invoices_by_id[invoice.invoice_id].append(invoice)
        for item in dataset.line_items:
            lines_by_id[item.invoice_id].append(item)

        contexts: list[_LineContext] = []
        for invoice_id, candidates in invoices_by_id.items():
            canonical_row = resolved[invoice_id].invoice.row_number
            for invoice in candidates:
                occurrence_items = resolver._items_for_occurrence(
                    invoice, lines_by_id[invoice_id], candidates
                )
                contexts.extend(
                    _LineContext(
                        item=item,
                        invoice=invoice,
                        match=self.matcher.match(
                            item.description,
                            item.unit_basis_as_billed,
                            item.unit_price_cents,
                        ),
                        include_in_output=invoice.row_number == canonical_row,
                    )
                    for item in occurrence_items
                )
        self._prepare_pricing(contexts)

        categories = {
            invoice_id: set(finding.categories) for invoice_id, finding in structural.items()
        }
        expected_totals: dict[str, int] = defaultdict(int)
        line_details: dict[str, list[LineAuditDetail]] = defaultdict(list)
        for context in contexts:
            if not context.include_in_output:
                continue
            invoice_categories = categories[context.item.invoice_id]
            detail = self._line_audit_detail(context)
            invoice_categories.update(detail.categories)
            expected_totals[context.item.invoice_id] += detail.expected_line_total_cents
            line_details[context.item.invoice_id].append(detail)

        findings = {
            invoice_id: AuditFinding(
                invoice_id=invoice_id,
                categories=tuple(sorted(categories[invoice_id])),
                billed_total_cents=finding.billed_total_cents,
                expected_total_cents=expected_totals[invoice_id],
                canonical_invoice_row=finding.canonical_invoice_row,
            )
            for invoice_id, finding in structural.items()
        }
        return DetailedAuditResult(
            findings=findings,
            line_details={
                invoice_id: tuple(sorted(details, key=lambda detail: detail.line_id))
                for invoice_id, details in line_details.items()
            },
        )

    def _line_audit_detail(self, context: _LineContext) -> LineAuditDetail:
        item = context.item
        categories = self._line_structural_categories(context)
        service_name = context.match.service_name
        contract_basis: str | None = None
        daily_cap: int | None = None
        premium_threshold: int | None = None
        expected_quantity = item.quantity
        expected_rate = item.unit_price_cents
        expected_line_total = item.quantity * item.unit_price_cents
        rate_steps: tuple[RateCalculationStep, ...] = ()

        if service_name is None:
            categories.add("unknown_service")
        else:
            service = self.contract.services[service_name]
            contract_basis = service.unit_basis
            daily_cap = service.daily_cap
            premium = self.contract.threshold_premiums.get(service_name)
            premium_threshold = premium.threshold if premium else None
            if item.unit_basis_as_billed != service.unit_basis:
                categories.add("wrong_unit_basis")
            if context.duplicate_category is not None:
                categories.add(context.duplicate_category)
            if context.excluded:
                categories.add("exclusion_window_violation")
            if (
                context.expected_quantity is not None
                and context.expected_quantity < item.quantity
            ):
                categories.add("daily_cap_exceeded")

            assert context.expected_rate_cents is not None
            expected_rate = context.expected_rate_cents
            expected_quantity = context.expected_quantity or 0
            expected_line_total = (
                0
                if context.excluded or context.duplicate_category
                else expected_rate * expected_quantity
            )
            if item.unit_price_cents != expected_rate:
                categories.add(self._price_error_category(context))
            rate_steps = self._rate_calculation(context)

        return LineAuditDetail(
            invoice_id=item.invoice_id,
            line_id=item.line_id,
            line_no=item.line_no,
            service_date=item.service_date,
            description=item.description,
            matched_service=service_name,
            match_score=context.match.score,
            match_margin=context.match.margin,
            billed_unit_basis=item.unit_basis_as_billed,
            contract_unit_basis=contract_basis,
            billed_quantity=item.quantity,
            expected_quantity=expected_quantity,
            aggregate_daily_quantity=context.aggregate_daily_quantity,
            premium_threshold=premium_threshold,
            cumulative_quantity_before=context.cumulative_quantity_before,
            applied_discount_threshold=context.applied_discount_threshold,
            daily_cap=daily_cap,
            exclusion_window_days=context.exclusion_window_days,
            billed_unit_price_cents=item.unit_price_cents,
            expected_unit_price_cents=expected_rate,
            billed_line_total_cents=item.line_total_cents,
            calculated_billed_line_total_cents=item.quantity * item.unit_price_cents,
            expected_line_total_cents=expected_line_total,
            rate_calculation=rate_steps,
            categories=tuple(sorted(categories)),
            bundle_partner_line_ids=tuple(sorted(context.bundle_partner_line_ids)),
            exclusion_trigger_line_ids=tuple(
                sorted(context.exclusion_trigger_line_ids)
            ),
            daily_cap_related_line_ids=tuple(
                sorted(context.daily_cap_related_line_ids)
            ),
            duplicate_of_line_id=context.duplicate_of_line_id,
            match_source=context.match.source,
            match_key=context.match.key,
            match_confidence=context.match.confidence,
            contract_clause_id=(
                self.contract.services[service_name].clause_id
                if service_name is not None
                else None
            ),
        )

    def _line_structural_categories(self, context: _LineContext) -> set[str]:
        item = context.item
        categories: set[str] = set()
        if item.quantity * item.unit_price_cents != item.line_total_cents:
            categories.add("line_total_arithmetic")
        service_date = _parse_date(item.service_date)
        if service_date is None:
            categories.add("malformed_service_date")
            return categories
        if not (
            self.contract.identity.effective_from
            <= service_date
            <= self.contract.identity.effective_to
        ):
            categories.add("service_date_out_of_window")
            return categories
        invoice_date = _parse_date(context.invoice.invoice_date)
        if invoice_date is not None and service_date > invoice_date:
            categories.add("service_date_after_invoice_date")
        return categories

    def _rate_calculation(
        self,
        context: _LineContext,
    ) -> tuple[RateCalculationStep, ...]:
        assert context.match.service_name is not None
        service = self.contract.services[context.match.service_name]
        rate = service.rate_cents
        steps = [RateCalculationStep("base", None, None, rate)]
        if context.bundle_applies:
            assert context.base_rate_cents is not None
            steps.append(RateCalculationStep("bundle", rate, None, context.base_rate_cents))
            rate = context.base_rate_cents
        if context.premium_multiplier is not None:
            adjusted = _round_cents(rate, context.premium_multiplier)
            steps.append(
                RateCalculationStep(
                    "premium",
                    rate,
                    str(context.premium_multiplier),
                    adjusted,
                )
            )
            rate = adjusted
        if context.discount_multiplier is not None:
            adjusted = _round_cents(rate, context.discount_multiplier)
            steps.append(
                RateCalculationStep(
                    "discount",
                    rate,
                    str(context.discount_multiplier),
                    adjusted,
                )
            )
        return tuple(steps)

    def _prepare_pricing(self, contexts: list[_LineContext]) -> None:
        valid = [context for context in contexts if context.match.service_name is not None]
        for context in valid:
            service = self.contract.services[context.match.service_name]
            context.base_rate_cents = service.rate_cents
            context.expected_quantity = context.item.quantity

        grouped: dict[tuple[str, date, str], list[_LineContext]] = defaultdict(list)
        for context in valid:
            service_date = _parse_date(context.item.service_date)
            if service_date is not None:
                grouped[(context.invoice.patient_id, service_date, context.match.service_name)].append(context)

        stage_handlers = {
            PricingStage.BUNDLE: lambda: self._apply_bundles(grouped),
            PricingStage.PREMIUM: lambda: self._apply_premiums(grouped),
            PricingStage.DISCOUNT: lambda: self._apply_discounts(valid),
            PricingStage.DAILY_CAP: lambda: self._apply_caps(grouped),
            PricingStage.EXCLUSION: lambda: self._apply_exclusions(grouped),
            PricingStage.DUPLICATE: lambda: self._apply_duplicates(grouped),
        }
        for stage in self.contract.pricing_pipeline:
            stage_handlers[stage]()

        for context in valid:
            rate = context.base_rate_cents
            assert rate is not None
            if context.premium_multiplier is not None:
                rate = _round_cents(rate, context.premium_multiplier)
            if context.discount_multiplier is not None:
                rate = _round_cents(rate, context.discount_multiplier)
            context.expected_rate_cents = rate

    def _apply_bundles(self, grouped: dict[tuple[str, date, str], list[_LineContext]]) -> None:
        for bundle in self.contract.bundles:
            patient_dates = {(patient, day) for patient, day, _ in grouped}
            for patient, day in patient_dates:
                a = grouped.get((patient, day, bundle.service_a), [])
                b = grouped.get((patient, day, bundle.service_b), [])
                if a and b:
                    for context in a:
                        context.base_rate_cents = bundle.rate_a_cents
                        context.bundle_applies = True
                        context.bundle_partner_line_ids.update(
                            partner.item.line_id for partner in b
                        )
                    for context in b:
                        context.base_rate_cents = bundle.rate_b_cents
                        context.bundle_applies = True
                        context.bundle_partner_line_ids.update(
                            partner.item.line_id for partner in a
                        )

    def _apply_premiums(self, grouped: dict[tuple[str, date, str], list[_LineContext]]) -> None:
        for (_, service_date, service_name), group in grouped.items():
            aggregate_quantity = sum(context.item.quantity for context in group)
            for context in group:
                context.aggregate_daily_quantity = aggregate_quantity
            premium = self.contract.threshold_premiums.get(service_name)
            if premium and aggregate_quantity > premium.threshold:
                for context in group:
                    context.premium_multiplier = premium.multiplier
            weekend = self.contract.non_business_day_uplifts.get(service_name)
            if weekend and service_date.weekday() >= 5:
                for context in group:
                    context.premium_multiplier = weekend

    def _apply_discounts(self, contexts: list[_LineContext]) -> None:
        by_service: dict[str, list[_LineContext]] = defaultdict(list)
        for context in contexts:
            if _parse_date(context.item.service_date) is not None:
                by_service[context.match.service_name].append(context)
        for service_name, group in by_service.items():
            rules = self.contract.volume_discounts.get(service_name, ())
            if not rules:
                continue
            cumulative = 0
            group.sort(key=lambda context: (_parse_date(context.item.service_date), context.item.line_id))
            for context in group:
                context.cumulative_quantity_before = cumulative
                applicable = [rule for rule in rules if cumulative > rule.threshold]
                if applicable:
                    context.discount_multiplier = applicable[-1].multiplier
                    context.applied_discount_threshold = applicable[-1].threshold
                cumulative += context.item.quantity

    def _apply_caps(self, grouped: dict[tuple[str, date, str], list[_LineContext]]) -> None:
        for (_, _, service_name), group in grouped.items():
            cap = self.contract.services[service_name].daily_cap
            if cap is None:
                continue
            remaining = cap
            group_line_ids = {context.item.line_id for context in group}
            for context in sorted(group, key=lambda context: context.item.line_id):
                context.daily_cap_related_line_ids.update(
                    group_line_ids - {context.item.line_id}
                )
                context.expected_quantity = min(context.item.quantity, max(remaining, 0))
                remaining -= context.item.quantity

    def _apply_exclusions(self, grouped: dict[tuple[str, date, str], list[_LineContext]]) -> None:
        by_patient_service: dict[tuple[str, str], list[tuple[date, _LineContext]]] = defaultdict(list)
        for (patient, service_date, service_name), group in grouped.items():
            by_patient_service[(patient, service_name)].extend((service_date, context) for context in group)
        for rule in self.contract.exclusions:
            patients = {patient for patient, _ in by_patient_service}
            for patient in patients:
                excluded = by_patient_service.get((patient, rule.excluded_service), [])
                triggers = by_patient_service.get((patient, rule.trigger_service), [])
                for excluded_date, context in excluded:
                    matching_triggers = [
                        trigger_context
                        for trigger_date, trigger_context in triggers
                        if abs((excluded_date - trigger_date).days) <= rule.window_days
                    ]
                    if matching_triggers:
                        context.excluded = True
                        context.exclusion_window_days = rule.window_days
                        context.exclusion_trigger_line_ids.update(
                            trigger.item.line_id for trigger in matching_triggers
                        )

    def _apply_duplicates(
        self,
        grouped: dict[tuple[str, date, str], list[_LineContext]],
    ) -> None:
        if (
            self.contract.duplicate_billing_policy
            == DuplicateBillingPolicy.REPEATED_SERVICE_PER_PATIENT_DAY
        ):
            self._apply_repeated_service_duplicates(grouped)
            return
        if (
            self.contract.duplicate_billing_policy
            == DuplicateBillingPolicy.MATCHING_LINE_ACROSS_INVOICES
        ):
            self._apply_matching_cross_invoice_duplicates(grouped)
            return
        raise ValueError(
            "unsupported duplicate billing policy: "
            f"{self.contract.duplicate_billing_policy!r}"
        )

    @staticmethod
    def _apply_repeated_service_duplicates(
        grouped: dict[tuple[str, date, str], list[_LineContext]],
    ) -> None:
        for group in grouped.values():
            ordered = sorted(group, key=lambda item: item.item.line_id)
            original_line_id = ordered[0].item.line_id
            for context in ordered[1:]:
                context.duplicate_category = "duplicate_service"
                context.duplicate_of_line_id = original_line_id

    @staticmethod
    def _apply_matching_cross_invoice_duplicates(
        grouped: dict[tuple[str, date, str], list[_LineContext]],
    ) -> None:
        for group in grouped.values():
            by_signature: dict[tuple[int, str], list[_LineContext]] = defaultdict(list)
            for context in group:
                by_signature[(context.item.quantity, context.item.unit_basis_as_billed)].append(context)
            for duplicates in by_signature.values():
                invoice_ids = {context.item.invoice_id for context in duplicates}
                if len(invoice_ids) <= 1:
                    continue
                ordered = sorted(duplicates, key=lambda item: item.item.line_id)
                original_line_id = ordered[0].item.line_id
                for context in ordered[1:]:
                    context.duplicate_category = "cross_invoice_duplicate"
                    context.duplicate_of_line_id = original_line_id

    def _price_error_category(self, context: _LineContext) -> str:
        assert context.base_rate_cents is not None
        assert context.match.service_name is not None
        billed = context.item.unit_price_cents
        standalone = self.contract.services[context.match.service_name].rate_cents
        if context.bundle_applies and billed == self._apply_adjustments(
            standalone, context.premium_multiplier, context.discount_multiplier
        ):
            return "bundle_not_applied"
        if context.premium_multiplier is not None and billed == context.base_rate_cents:
            return "premium_omitted"
        if context.premium_multiplier is None:
            possible_premiums: set[Decimal] = set()
            threshold = self.contract.threshold_premiums.get(context.match.service_name)
            if threshold:
                possible_premiums.add(threshold.multiplier)
            weekend = self.contract.non_business_day_uplifts.get(context.match.service_name)
            if weekend:
                possible_premiums.add(weekend)
            if any(
                billed
                == self._apply_adjustments(
                    context.base_rate_cents, multiplier, context.discount_multiplier
                )
                for multiplier in possible_premiums
            ):
                return "premium_incorrectly_applied"
        if context.discount_multiplier is not None:
            before_discount = context.base_rate_cents
            if context.premium_multiplier is not None:
                before_discount = _round_cents(before_discount, context.premium_multiplier)
            if billed == before_discount:
                return "volume_discount_omitted"
        if context.discount_multiplier is None:
            if any(
                billed
                == self._apply_adjustments(
                    context.base_rate_cents,
                    context.premium_multiplier,
                    rule.multiplier,
                )
                for rule in self.contract.volume_discounts.get(context.match.service_name, ())
            ):
                return "volume_discount_incorrectly_applied"
        return "unit_price_mismatch"

    @staticmethod
    def _apply_adjustments(
        base_rate_cents: int,
        premium: Decimal | None,
        discount: Decimal | None,
    ) -> int:
        rate = base_rate_cents
        if premium is not None:
            rate = _round_cents(rate, premium)
        if discount is not None:
            rate = _round_cents(rate, discount)
        return rate


# Compatibility for callers that adopted the development-only class name before
# the audit engine was made contract-agnostic.
Hospital1Auditor = ContractAuditor
