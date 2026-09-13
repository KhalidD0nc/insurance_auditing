from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    row_number: int
    invoice_id: str
    hospital_id: str
    contract_number: str
    invoice_date: str
    patient_id: str
    facility_code: str
    plan_tier: str
    admission_date: str
    discharge_date: str
    invoice_total_cents: int


@dataclass(frozen=True, slots=True)
class LineItem:
    row_number: int
    line_id: str
    invoice_id: str
    line_no: int
    service_date: str
    description: str
    quantity: int
    unit_basis_as_billed: str
    unit_price_cents: int
    line_total_cents: int


@dataclass(frozen=True, slots=True)
class HospitalDataset:
    hospital_number: int
    invoices: tuple[InvoiceRecord, ...]
    line_items: tuple[LineItem, ...]


@dataclass(frozen=True, slots=True)
class AuditFinding:
    invoice_id: str
    categories: tuple[str, ...]
    billed_total_cents: int
    expected_total_cents: int
    canonical_invoice_row: int

    @property
    def flagged(self) -> bool:
        return bool(self.categories)


@dataclass(frozen=True, slots=True)
class AssembledInvoice:
    invoice: InvoiceRecord
    line_items: tuple[LineItem, ...]
    occurrence_count: int


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    hospital_number: int
    hospital_id: str
    contract_number: str
    effective_from: date
    effective_to: date


@dataclass(frozen=True, slots=True)
class ServiceRule:
    name: str
    unit_basis: str
    rate_cents: int
    daily_cap: int | None = None


@dataclass(frozen=True, slots=True)
class ThresholdPremium:
    service: str
    threshold: int
    multiplier: Decimal


@dataclass(frozen=True, slots=True)
class VolumeDiscount:
    service: str
    threshold: int
    multiplier: Decimal


@dataclass(frozen=True, slots=True)
class BundleRule:
    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int


@dataclass(frozen=True, slots=True)
class ExclusionRule:
    excluded_service: str
    window_days: int
    trigger_service: str


@dataclass(frozen=True, slots=True)
class ContractRules:
    identity: ContractIdentity
    services: dict[str, ServiceRule]
    threshold_premiums: dict[str, ThresholdPremium]
    non_business_day_uplifts: dict[str, Decimal]
    volume_discounts: dict[str, tuple[VolumeDiscount, ...]]
    bundles: tuple[BundleRule, ...]
    exclusions: tuple[ExclusionRule, ...]


@dataclass(frozen=True, slots=True)
class ServiceMatch:
    service_name: str | None
    score: float
    margin: float
