from __future__ import annotations

from collections import defaultdict
from datetime import date

from .models import (
    AuditFinding,
    ContractIdentity,
    HospitalDataset,
    InvoiceRecord,
    LineItem,
    AssembledInvoice,
)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class StructuralAuditor:
    """High-precision checks that do not depend on service-name matching."""

    def __init__(self, contract: ContractIdentity) -> None:
        self.contract = contract

    def resolve(self, dataset: HospitalDataset) -> dict[str, AssembledInvoice]:
        invoices_by_id: dict[str, list[InvoiceRecord]] = defaultdict(list)
        lines_by_id: dict[str, list[LineItem]] = defaultdict(list)
        for invoice in dataset.invoices:
            invoices_by_id[invoice.invoice_id].append(invoice)
        for item in dataset.line_items:
            lines_by_id[item.invoice_id].append(item)

        resolved: dict[str, AssembledInvoice] = {}
        for invoice_id, candidates in invoices_by_id.items():
            all_items = lines_by_id.get(invoice_id, [])
            invoice = self._choose_canonical_invoice(candidates, all_items)
            selected_items = self._items_for_occurrence(invoice, all_items, candidates)
            resolved[invoice_id] = AssembledInvoice(
                invoice=invoice,
                line_items=tuple(selected_items),
                occurrence_count=len(candidates),
            )
        return resolved

    def audit(self, dataset: HospitalDataset) -> dict[str, AuditFinding]:
        resolved = self.resolve(dataset)
        invoices_by_id: dict[str, list[InvoiceRecord]] = defaultdict(list)
        for invoice in dataset.invoices:
            invoices_by_id[invoice.invoice_id].append(invoice)

        findings: dict[str, AuditFinding] = {}
        for invoice_id, resolution in resolved.items():
            candidates = invoices_by_id[invoice_id]
            invoice = resolution.invoice
            items = resolution.line_items
            line_items_total = sum(item.line_total_cents for item in items)
            categories: set[str] = set()

            if resolution.occurrence_count > 1:
                categories.add("duplicate_invoice_id")
            if any(candidate.hospital_id != self.contract.hospital_id for candidate in candidates):
                categories.add("hospital_id_mismatch")
            if any(
                candidate.contract_number != self.contract.contract_number
                for candidate in candidates
            ):
                categories.add("contract_number_mismatch")
            if invoice.invoice_total_cents != line_items_total:
                categories.add("invoice_total_mismatch")

            invoice_date = _parse_date(invoice.invoice_date)
            if invoice_date is None:
                categories.add("malformed_invoice_date")

            for item in items:
                if item.quantity * item.unit_price_cents != item.line_total_cents:
                    categories.add("line_total_arithmetic")

                service_date = _parse_date(item.service_date)
                if service_date is None:
                    categories.add("malformed_service_date")
                    continue
                outside_term = not (
                    self.contract.effective_from <= service_date <= self.contract.effective_to
                )
                if outside_term:
                    categories.add("service_date_out_of_window")
                elif invoice_date is not None and service_date > invoice_date:
                    categories.add("service_date_after_invoice_date")

            findings[invoice_id] = AuditFinding(
                invoice_id=invoice_id,
                categories=tuple(sorted(categories)),
                billed_total_cents=invoice.invoice_total_cents,
                expected_total_cents=line_items_total,
                canonical_invoice_row=invoice.row_number,
            )

        return findings

    @classmethod
    def _choose_canonical_invoice(
        cls,
        candidates: list[InvoiceRecord],
        items: list[LineItem],
    ) -> InvoiceRecord:
        """Select the last occurrence when an identifier has been reused.

        The development labels treat the later bill as the invoice under review and
        the earlier occurrence as evidence that its identifier was already used.
        Row order is retained during ingestion so this decision is deterministic.
        """

        del items  # The item partition is applied after occurrence selection.
        return max(candidates, key=lambda invoice: invoice.row_number)

    @staticmethod
    def _items_for_occurrence(
        invoice: InvoiceRecord,
        items: list[LineItem],
        candidates: list[InvoiceRecord],
    ) -> list[LineItem]:
        if len(candidates) == 1:
            return list(items)

        def distance(candidate: InvoiceRecord, service_date: date) -> tuple[int, int, int]:
            admission = _parse_date(candidate.admission_date)
            discharge = _parse_date(candidate.discharge_date)
            invoice_date = _parse_date(candidate.invoice_date)
            if admission is None or discharge is None:
                episode_distance = 10**9
            elif service_date < admission:
                episode_distance = (admission - service_date).days
            elif service_date > discharge:
                episode_distance = (service_date - discharge).days
            else:
                episode_distance = 0
            invoice_distance = (
                abs((invoice_date - service_date).days) if invoice_date is not None else 10**9
            )
            return episode_distance, invoice_distance, -candidate.row_number

        selected: list[LineItem] = []
        for item in items:
            service_date = _parse_date(item.service_date)
            if service_date is None:
                owner = max(candidates, key=lambda candidate: candidate.row_number)
            else:
                owner = min(candidates, key=lambda candidate: distance(candidate, service_date))
            if owner.row_number == invoice.row_number:
                selected.append(item)
        return selected
