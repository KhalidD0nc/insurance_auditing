from pathlib import Path
import unittest

from insurance_auditing.contract_parser import load_hospital_2_contract
from insurance_auditing.models import (
    HospitalDataset,
    InvoiceRecord,
    LineItem,
    ServiceMappingOverride,
)
from insurance_auditing.pricing import ContractAuditor
from insurance_auditing.service_matching import (
    ServiceMatcher,
    normalise_service_description,
)


ROOT = Path(__file__).resolve().parents[1]


def _invoice(invoice_id: str = "INV-TEST") -> InvoiceRecord:
    return InvoiceRecord(
        2,
        invoice_id,
        "H2",
        "INS-H2-2024-1183",
        "2024-01-08",
        "PAT-TEST",
        "F-MAIN",
        "GOLD",
        "2024-01-01",
        "2024-01-08",
        0,
    )


def _line(
    line_id: str,
    description: str,
    quantity: int,
    basis: str,
    price: int,
    *,
    service_date: str = "2024-01-08",
    invoice_id: str = "INV-TEST",
) -> LineItem:
    return LineItem(
        2,
        line_id,
        invoice_id,
        1,
        service_date,
        description,
        quantity,
        basis,
        price,
        quantity * price,
    )


class Hospital2MatchingAndPricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_hospital_2_contract(
            ROOT / "contracts" / "hospital_2" / "master_services_agreement.md"
        )

    def test_conservative_matcher_rejects_a_textual_tie(self) -> None:
        matcher = ServiceMatcher(
            self.contract,
            minimum_margin=0.15,
            allow_price_tiebreaker=False,
        )
        match = matcher.match("STD ISOL RM OCC", "per_day", 33_275)
        self.assertIsNone(match.service_name)
        self.assertEqual(match.margin, 0)

    def test_conservative_matcher_accepts_an_unambiguous_abbreviation(self) -> None:
        matcher = ServiceMatcher(
            self.contract,
            minimum_margin=0.15,
            allow_price_tiebreaker=False,
            allow_expanded_abbreviations=True,
        )
        match = matcher.match(
            "ADV ENDOSC PROC",
            "per_procedure",
            1,
        )
        self.assertEqual(
            match.service_name,
            "Advanced Gastrointestinal Endoscopic Procedure",
        )
        self.assertEqual(match.source, "deterministic")

    def test_unit_basis_breaks_only_a_true_semantic_tie(self) -> None:
        matcher = ServiceMatcher(
            self.contract,
            minimum_margin=0.15,
            allow_price_tiebreaker=False,
            allow_unit_basis_tiebreaker=True,
        )
        match = matcher.match(
            "PAED INF THER",
            "per_unit_dispensed",
            1,
        )
        self.assertEqual(
            match.service_name,
            "Intensive Paediatric Infusion Therapy",
        )
        self.assertEqual(match.source, "unit_basis_tiebreaker")
        self.assertEqual(match.confidence, 0.90)

        decoy = matcher.match(
            "AMB OPHTH VENT SUPP",
            "per_item",
            18_050,
        )
        self.assertIsNone(decoy.service_name)

    def test_validated_mapping_override_records_provenance(self) -> None:
        description = "STD ISOL RM OCC"
        key = normalise_service_description(description)
        matcher = ServiceMatcher(
            self.contract,
            {
                key: ServiceMappingOverride(
                    "Standard Orthopaedic Isolation Room Occupancy", 0.96
                )
            },
            minimum_margin=0.15,
            allow_price_tiebreaker=False,
        )
        match = matcher.match(description, "per_day", 33_275)
        self.assertEqual(
            match.service_name, "Standard Orthopaedic Isolation Room Occupancy"
        )
        self.assertEqual(match.source, "llm_mapping")
        self.assertEqual(match.key, key)
        self.assertEqual(match.confidence, 0.96)

    def test_hospital_2_weekend_rate_uses_half_up_cents(self) -> None:
        item = _line(
            "L-1",
            "Emergency Renal Infusion Therapy",
            1,
            "per_hour",
            5_925,
            service_date="2024-01-07",
        )
        dataset = HospitalDataset(2, (_invoice(),), (item,))
        detail = ContractAuditor(self.contract).audit_detailed(dataset).line_details[
            "INV-TEST"
        ][0]
        self.assertEqual(detail.expected_unit_price_cents, 6_636)
        self.assertIn("premium_omitted", detail.categories)
        self.assertEqual(detail.contract_clause_id, "4.4")

    def test_wrong_unit_basis_is_a_separate_deterministic_finding(self) -> None:
        service = "Advanced Infectious Isolation Room Occupancy"
        item = _line("L-1", service, 1, "per_hour", 164_825)
        dataset = HospitalDataset(2, (_invoice(),), (item,))
        detail = ContractAuditor(self.contract).audit_detailed(dataset).line_details[
            "INV-TEST"
        ][0]
        self.assertEqual(detail.matched_service, service)
        self.assertIn("wrong_unit_basis", detail.categories)

    def test_applies_hospital_2_bundle_rates_to_both_services(self) -> None:
        items = (
            _line(
                "L-1",
                "Bedside Obstetric Physiotherapy Session",
                1,
                "per_hour",
                6_950,
            ),
            _line(
                "L-2",
                "Postoperative Orthopaedic Isolation Room Occupancy",
                1,
                "per_night",
                120_475,
            ),
        )
        result = ContractAuditor(self.contract).audit_detailed(
            HospitalDataset(2, (_invoice(),), items)
        )
        details = {item.line_id: item for item in result.line_details["INV-TEST"]}
        self.assertEqual(details["L-1"].expected_unit_price_cents, 5_200)
        self.assertEqual(details["L-2"].expected_unit_price_cents, 90_350)
        self.assertIn("bundle_not_applied", details["L-1"].categories)

    def test_applies_threshold_premium_and_daily_cap(self) -> None:
        premium = _line(
            "L-1",
            "Focused Palliative Recovery Room Occupancy",
            13,
            "per_night",
            52_775,
        )
        capped = _line(
            "L-2",
            "Advanced Infectious Isolation Room Occupancy",
            25,
            "per_day",
            164_825,
        )
        result = ContractAuditor(self.contract).audit_detailed(
            HospitalDataset(2, (_invoice(),), (premium, capped))
        )
        details = {item.line_id: item for item in result.line_details["INV-TEST"]}
        self.assertEqual(details["L-1"].expected_unit_price_cents, 68_608)
        self.assertIn("premium_omitted", details["L-1"].categories)
        self.assertEqual(details["L-2"].expected_quantity, 24)
        self.assertIn("daily_cap_exceeded", details["L-2"].categories)

    def test_applies_discount_only_after_prior_utilisation_exceeds_threshold(self) -> None:
        service = "Advanced Gastrointestinal Endoscopic Procedure"
        base_rate = self.contract.services[service].rate_cents
        items = (
            _line("L-1", service, 121, "per_procedure", base_rate),
            _line("L-2", service, 1, "per_procedure", base_rate),
        )
        result = ContractAuditor(self.contract).audit_detailed(
            HospitalDataset(2, (_invoice(),), items)
        )
        details = {item.line_id: item for item in result.line_details["INV-TEST"]}
        self.assertEqual(details["L-1"].expected_unit_price_cents, base_rate)
        self.assertEqual(details["L-2"].expected_unit_price_cents, 332_420)
        self.assertEqual(details["L-2"].applied_discount_threshold, 120)

    def test_applies_bidirectional_exclusion_window(self) -> None:
        excluded = "Intermittent Psychiatric Laboratory Panel"
        trigger = "Emergency Pulmonary Ventilation Support"
        items = (
            _line(
                "L-1",
                excluded,
                1,
                self.contract.services[excluded].unit_basis,
                self.contract.services[excluded].rate_cents,
                service_date="2024-01-01",
            ),
            _line(
                "L-2",
                trigger,
                1,
                self.contract.services[trigger].unit_basis,
                self.contract.services[trigger].rate_cents,
                service_date="2024-01-08",
            ),
        )
        result = ContractAuditor(self.contract).audit_detailed(
            HospitalDataset(2, (_invoice(),), items)
        )
        detail = next(
            item
            for item in result.line_details["INV-TEST"]
            if item.matched_service == excluded
        )
        self.assertEqual(detail.expected_line_total_cents, 0)
        self.assertIn("exclusion_window_violation", detail.categories)


if __name__ == "__main__":
    unittest.main()
