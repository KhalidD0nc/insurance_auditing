from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


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
class RateCalculationStep:
    stage: str
    input_rate_cents: int | None
    multiplier: str | None
    output_rate_cents: int


@dataclass(frozen=True, slots=True)
class LineAuditDetail:
    invoice_id: str
    line_id: str
    line_no: int
    service_date: str
    description: str
    matched_service: str | None
    match_score: float
    match_margin: float
    billed_unit_basis: str
    contract_unit_basis: str | None
    billed_quantity: int
    expected_quantity: int
    aggregate_daily_quantity: int | None
    premium_threshold: int | None
    cumulative_quantity_before: int | None
    applied_discount_threshold: int | None
    daily_cap: int | None
    exclusion_window_days: int | None
    billed_unit_price_cents: int
    expected_unit_price_cents: int
    billed_line_total_cents: int
    calculated_billed_line_total_cents: int
    expected_line_total_cents: int
    rate_calculation: tuple[RateCalculationStep, ...]
    categories: tuple[str, ...]
    bundle_partner_line_ids: tuple[str, ...]
    exclusion_trigger_line_ids: tuple[str, ...]
    daily_cap_related_line_ids: tuple[str, ...]
    duplicate_of_line_id: str | None
    match_source: str = "deterministic"
    match_key: str | None = None
    match_confidence: float | None = None
    contract_clause_id: str | None = None


@dataclass(frozen=True, slots=True)
class DetailedAuditResult:
    findings: dict[str, AuditFinding]
    line_details: dict[str, tuple[LineAuditDetail, ...]]


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
class ScheduledRate:
    effective_from: date
    rate_cents: int
    clause_id: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceRule:
    name: str
    unit_basis: str
    rate_cents: int
    daily_cap: int | None = None
    clause_id: str | None = None
    effective_from: date | None = None
    scheduled_rates: tuple[ScheduledRate, ...] = ()


@dataclass(frozen=True, slots=True)
class ThresholdPremium:
    service: str
    threshold: int
    multiplier: Decimal
    clause_id: str | None = None


@dataclass(frozen=True, slots=True)
class VolumeDiscount:
    service: str
    threshold: int
    multiplier: Decimal
    clause_id: str | None = None


@dataclass(frozen=True, slots=True)
class BundleRule:
    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int
    clause_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExclusionRule:
    excluded_service: str
    window_days: int
    trigger_service: str
    clause_id: str | None = None


class PricingStage(str, Enum):
    BUNDLE = "bundle"
    FACILITY = "facility"
    PLAN_TIER = "plan_tier"
    PREMIUM = "premium"
    DISCOUNT = "discount"
    DAILY_CAP = "daily_cap"
    EXCLUSION = "exclusion"
    DUPLICATE = "duplicate"


class DuplicateBillingPolicy(str, Enum):
    MATCHING_LINE_ACROSS_INVOICES = "matching_line_across_invoices"
    REPEATED_SERVICE_PER_PATIENT_DAY = "repeated_service_per_patient_day"


DEFAULT_PRICING_PIPELINE = (
    PricingStage.BUNDLE,
    PricingStage.FACILITY,
    PricingStage.PLAN_TIER,
    PricingStage.PREMIUM,
    PricingStage.DISCOUNT,
    PricingStage.DAILY_CAP,
    PricingStage.EXCLUSION,
    PricingStage.DUPLICATE,
)


@dataclass(frozen=True, slots=True)
class ContractRules:
    identity: ContractIdentity
    services: dict[str, ServiceRule]
    threshold_premiums: dict[str, ThresholdPremium]
    non_business_day_uplifts: dict[str, Decimal]
    volume_discounts: dict[str, tuple[VolumeDiscount, ...]]
    bundles: tuple[BundleRule, ...]
    exclusions: tuple[ExclusionRule, ...]
    facility_multipliers: dict[str, dict[str, Decimal]] = field(
        default_factory=dict
    )
    plan_tier_multipliers: dict[str, dict[str, Decimal]] = field(
        default_factory=dict
    )
    pricing_pipeline: tuple[PricingStage, ...] = DEFAULT_PRICING_PIPELINE
    duplicate_billing_policy: DuplicateBillingPolicy = (
        DuplicateBillingPolicy.MATCHING_LINE_ACROSS_INVOICES
    )
    source_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceMatch:
    service_name: str | None
    score: float
    margin: float
    source: str = "deterministic"
    key: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ServiceMappingOverride:
    service_name: str
    confidence: float
