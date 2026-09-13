from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from .models import HospitalDataset, InvoiceRecord, LineItem


INVOICE_COLUMNS = {
    "invoice_id",
    "hospital_id",
    "contract_number",
    "invoice_date",
    "patient_id",
    "facility_code",
    "plan_tier",
    "admission_date",
    "discharge_date",
    "invoice_total_cents",
}

LINE_ITEM_COLUMNS = {
    "line_id",
    "invoice_id",
    "line_no",
    "service_date",
    "description",
    "quantity",
    "unit_basis_as_billed",
    "unit_price_cents",
    "line_total_cents",
}


def _normalised_rows(path: Path, required_columns: set[str]) -> Iterable[tuple[int, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        normalised_headers = {header.strip() for header in reader.fieldnames}
        missing = required_columns - normalised_headers
        if missing:
            raise ValueError(f"CSV {path} is missing columns: {sorted(missing)}")

        for row_number, raw in enumerate(reader, start=2):
            row = {
                key.strip(): value.strip()
                for key, value in raw.items()
                if key is not None and value is not None
            }
            yield row_number, row


def _as_int(value: str, *, path: Path, row_number: int, column: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"invalid integer in {path}, row {row_number}, column {column}: {value!r}"
        ) from exc


def load_hospital(data_root: Path | str, hospital_number: int) -> HospitalDataset:
    root = Path(data_root)
    invoice_path = root / "invoices" / f"hospital_{hospital_number}_invoices.csv"
    line_item_path = root / "invoices" / f"hospital_{hospital_number}_line_items.csv"

    invoices = tuple(
        InvoiceRecord(
            row_number=row_number,
            invoice_id=row["invoice_id"],
            hospital_id=row["hospital_id"],
            contract_number=row["contract_number"],
            invoice_date=row["invoice_date"],
            patient_id=row["patient_id"],
            facility_code=row["facility_code"],
            plan_tier=row["plan_tier"],
            admission_date=row["admission_date"],
            discharge_date=row["discharge_date"],
            invoice_total_cents=_as_int(
                row["invoice_total_cents"],
                path=invoice_path,
                row_number=row_number,
                column="invoice_total_cents",
            ),
        )
        for row_number, row in _normalised_rows(invoice_path, INVOICE_COLUMNS)
    )

    line_items = tuple(
        LineItem(
            row_number=row_number,
            line_id=row["line_id"],
            invoice_id=row["invoice_id"],
            line_no=_as_int(row["line_no"], path=line_item_path, row_number=row_number, column="line_no"),
            service_date=row["service_date"],
            description=row["description"],
            quantity=_as_int(row["quantity"], path=line_item_path, row_number=row_number, column="quantity"),
            unit_basis_as_billed=row["unit_basis_as_billed"],
            unit_price_cents=_as_int(
                row["unit_price_cents"],
                path=line_item_path,
                row_number=row_number,
                column="unit_price_cents",
            ),
            line_total_cents=_as_int(
                row["line_total_cents"],
                path=line_item_path,
                row_number=row_number,
                column="line_total_cents",
            ),
        )
        for row_number, row in _normalised_rows(line_item_path, LINE_ITEM_COLUMNS)
    )

    invoice_ids = {invoice.invoice_id for invoice in invoices}
    orphan_ids = sorted({item.invoice_id for item in line_items} - invoice_ids)
    if orphan_ids:
        preview = ", ".join(orphan_ids[:5])
        raise ValueError(f"line items refer to missing invoices: {preview}")

    return HospitalDataset(hospital_number, invoices, line_items)
