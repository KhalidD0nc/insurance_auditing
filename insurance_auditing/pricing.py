from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .audit import StructuralAuditor, _parse_date
from .models import AuditFinding, ContractRules, HospitalDataset, InvoiceRecord, LineItem, ServiceMatch
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
    cross_invoice_duplicate: bool = False
    bundle_applies: bool = False
    premium_multiplier: Decimal | None = None
    discount_multiplier: Decimal | None = None
    include_in_output: bool = True


class Hospital1Auditor:
    def __init__(self, contract: ContractRules) -> None:
        self.contract = contract
        self.matcher = ServiceMatcher(contract)

    def audit(self, dataset: HospitalDataset) -> dict[str, AuditFinding]:
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
        for context in contexts:
            if not context.include_in_output:
                continue
            invoice_categories = categories[context.item.invoice_id]
            if context.match.service_name is None:
                invoice_categories.add("unknown_service")
                expected_totals[context.item.invoice_id] += (
                    context.item.quantity * context.item.unit_price_cents
                )
                continue

            service = self.contract.services[context.match.service_name]
            if context.item.unit_basis_as_billed != service.unit_basis:
                invoice_categories.add("wrong_unit_basis")
            if context.cross_invoice_duplicate:
                invoice_categories.add("cross_invoice_duplicate")
            if context.excluded:
                invoice_categories.add("exclusion_window_violation")
            if context.expected_quantity is not None and context.expected_quantity < context.item.quantity:
                invoice_categories.add("daily_cap_exceeded")

            assert context.expected_rate_cents is not None
            expected_quantity = context.expected_quantity or 0
            expected_line_total = 0 if context.excluded or context.cross_invoice_duplicate else (
                context.expected_rate_cents * expected_quantity
            )
            expected_totals[context.item.invoice_id] += expected_line_total

            if context.item.unit_price_cents != context.expected_rate_cents:
                invoice_categories.add(self._price_error_category(context))

        return {
            invoice_id: AuditFinding(
                invoice_id=invoice_id,
                categories=tuple(sorted(categories[invoice_id])),
                billed_total_cents=finding.billed_total_cents,
                expected_total_cents=expected_totals[invoice_id],
                canonical_invoice_row=finding.canonical_invoice_row,
            )
            for invoice_id, finding in structural.items()
        }

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

        self._apply_bundles(grouped)
        self._apply_premiums(grouped)
        self._apply_discounts(valid)
        self._apply_caps(grouped)
        self._apply_exclusions(grouped)
        self._apply_cross_invoice_duplicates(grouped)

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
                    for context in b:
                        context.base_rate_cents = bundle.rate_b_cents
                        context.bundle_applies = True

    def _apply_premiums(self, grouped: dict[tuple[str, date, str], list[_LineContext]]) -> None:
        for (_, service_date, service_name), group in grouped.items():
            premium = self.contract.threshold_premiums.get(service_name)
            if premium and sum(context.item.quantity for context in group) > premium.threshold:
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
                applicable = [rule for rule in rules if cumulative > rule.threshold]
                if applicable:
                    context.discount_multiplier = applicable[-1].multiplier
                cumulative += context.item.quantity

    def _apply_caps(self, grouped: dict[tuple[str, date, str], list[_LineContext]]) -> None:
        for (_, _, service_name), group in grouped.items():
            cap = self.contract.services[service_name].daily_cap
            if cap is None:
                continue
            remaining = cap
            for context in sorted(group, key=lambda context: context.item.line_id):
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
                    if any(abs((excluded_date - trigger_date).days) <= rule.window_days for trigger_date, _ in triggers):
                        context.excluded = True

    @staticmethod
    def _apply_cross_invoice_duplicates(
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
                for context in sorted(duplicates, key=lambda item: item.item.line_id)[1:]:
                    context.cross_invoice_duplicate = True

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
