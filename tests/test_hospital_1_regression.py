from pathlib import Path
import unittest

from insurance_auditing.audit import StructuralAuditor
from insurance_auditing.contract_parser import load_hospital_1_contract
from insurance_auditing.contracts import contract_for_hospital
from insurance_auditing.evaluation import (
    evaluate_categories,
    evaluate_flags,
    load_development_labels,
    load_labels,
)
from insurance_auditing.io import load_hospital
from insurance_auditing.pricing import ContractAuditor


class Hospital1RegressionTests(unittest.TestCase):
    def test_structural_rules_are_high_precision_on_development_set(self) -> None:
        root = Path(__file__).resolve().parents[1]
        findings = StructuralAuditor(contract_for_hospital(1)).audit(load_hospital(root, 1))
        metrics = evaluate_flags(findings, load_labels(root / "labels" / "hospital_1_labels.csv"))

        self.assertEqual(metrics.false_positives, 0)
        self.assertGreaterEqual(metrics.true_positives, 30)
        self.assertGreaterEqual(metrics.precision, 0.99)

    def test_full_contract_audit_on_development_set(self) -> None:
        root = Path(__file__).resolve().parents[1]
        findings = ContractAuditor(
            load_hospital_1_contract(
                root / "contracts" / "hospital_1" / "provider_services_agreement.md"
            )
        ).audit(load_hospital(root, 1))
        labels = load_development_labels(root / "labels" / "hospital_1_labels.csv")
        metrics = evaluate_flags(
            findings,
            {invoice_id: label.is_erroneous for invoice_id, label in labels.items()},
        )

        self.assertEqual(metrics.true_positives, 58)
        self.assertEqual(metrics.false_positives, 0)
        self.assertEqual(metrics.false_negatives, 0)
        exact_totals = sum(
            findings[invoice_id].expected_total_cents == label.expected_total_cents
            for invoice_id, label in labels.items()
        )
        self.assertGreaterEqual(exact_totals, 909)
        category_metrics = evaluate_categories(findings, labels)
        self.assertTrue(all(metrics.f1 == 1.0 for metrics in category_metrics.values()))


if __name__ == "__main__":
    unittest.main()
