from benchmark.report import write_report


def test_capped_report_labels_ceiling_cost_as_estimate(tmp_path):
    record = {
        "architecture": "single", "correct": True, "speedup": 2,
        "cost_usd": 0.5, "cost_basis": "provider_reported",
        "budget_cost_basis": "token_usage_at_price_ceiling",
    }
    report = write_report(tmp_path, [record])
    assert "not provider invoices" in report
    assert "unequal completed repetitions" in report
    assert "budget.json" in report


def test_ordinary_report_does_not_claim_budget_ledger(tmp_path):
    report = write_report(tmp_path, [{"architecture": "single", "correct": False}])
    assert "budget.json" not in report
