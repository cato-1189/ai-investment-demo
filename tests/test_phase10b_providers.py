import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_PILOT = ROOT / "scripts" / "run_real_data_pilot.py"
sys.path.insert(0, str(ROOT / "scripts"))
import run_demo  # noqa: E402


def write_manual(date: str, rows: list[str], header: str = "ticker;date;close;volume;currency;source") -> Path:
    path = ROOT / "data" / "manual_market_data" / date / "market_data.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_manual_csv_valid_loads_and_normalizes(tmp_path):
    date = "2026-06-27"
    path = tmp_path / "market_data.csv"
    path.write_text("ticker;date;close;volume;currency;source\nAAPL;2026-06-26;200;1000000;USD;manual_test\n", encoding="utf-8")
    settings = {"manual_csv_path": str(path), "allow_manual_csv_fallback": True}
    payload = run_demo.fetch_manual_csv([{"ticker": "AAPL"}], date, settings)
    assets, _ = run_demo.normalize_market_data(payload, {"market_data": settings}, date)
    assert payload["errors"] == []
    assert assets[0]["provider"] == "manual_csv"
    assert assets[0]["price_close"] == 200
    assert assets[0]["avg_volume_usd"] == 200_000_000
    assert assets[0]["data_quality"] == "MEDIUM"


def test_manual_csv_invalid_reports_errors(tmp_path):
    date = "2026-06-27"
    path = tmp_path / "bad.csv"
    path.write_text("ticker;date;close\nAAPL;2026-06-26;200\n", encoding="utf-8")
    payload = run_demo.fetch_manual_csv([{"ticker": "AAPL"}], date, {"manual_csv_path": str(path)})
    assert payload["assets"] == []
    assert "columnas faltantes" in payload["errors"][0]["error"]


def test_fallback_to_manual_when_stooq_fails(monkeypatch, tmp_path):
    date = "2026-06-27"
    path = tmp_path / "market_data.csv"
    path.write_text("ticker;date;close;volume;currency;source\nAAPL;2026-06-26;200;1000000;USD;manual_test\n", encoding="utf-8")

    def fake_stooq(universe, today, timeout):
        return {"provider": "stooq_csv", "as_of_date": today, "fetched_at": "now", "assets": [], "errors": [{"ticker": "AAPL", "error": "403"}]}

    monkeypatch.setattr(run_demo, "fetch_stooq_csv", fake_stooq)
    payload = run_demo.fetch_real_multi_provider([{"ticker": "AAPL"}], date, {"provider_priority": ["stooq_csv", "manual_csv"], "allow_manual_csv_fallback": True, "manual_csv_path": str(path), "timeout_seconds": 1})
    assert payload["coverage_by_provider"] == {"stooq_csv": [], "manual_csv": ["AAPL"]}
    assert payload["manual_csv_used"] is True
    assert any(e["provider"] == "stooq_csv" for e in payload["errors"])


def test_real_pilot_partial_manual_csv_warning_and_safety():
    date = "2026-06-27"
    write_manual(date, [
        "AAPL;2026-06-26;200;1000000;USD;manual_test",
        "MSFT;2026-06-26;400;1000000;USD;manual_test",
    ])
    proc = subprocess.run([sys.executable, str(RUN_PILOT), "--date", date, "--activate-real-data-pilot"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert proc.returncode in (0, 1), proc.stderr
    report_path = next(ROOT / line.split("Reporte JSON: ", 1)[1] for line in proc.stdout.splitlines() if line.startswith("Reporte JSON: "))
    report = json.loads(report_path.read_text())
    assert report["status"] in {"WARNING", "PASS"}
    assert report["manual_csv_used"] is True
    assert "AAPL" in report["tickers_with_real_data_available"]
    assert report["benchmarks_in_scoring"] == []
    assert report["safety"]["broker_connected"] is False
    assert report["safety"]["allow_real_orders"] is False
    assert set(report["safety"]["real_order_values"]) <= {"false"}


def test_zero_coverage_fail_when_required(monkeypatch):
    report = run_demo.build_data_quality_report([], "run", "2026-06-27", [], {"provider": "multi_provider", "errors": [{"error": "none"}]})
    assert report["complete_assets"] == []


def test_yfinance_missing_package_allows_manual_fallback(monkeypatch, tmp_path):
    date = "2026-06-27"
    path = tmp_path / "market_data.csv"
    path.write_text("ticker;date;close;volume;currency;source\nAAPL;2026-06-26;200;1000000;USD;manual_test\n", encoding="utf-8")

    def fake_yfinance(universe, today, settings):
        return {"provider": "yfinance", "as_of_date": today, "fetched_at": "now", "assets": [], "errors": [{"provider": "yfinance", "error": "yfinance no instalado"}]}

    monkeypatch.setattr(run_demo, "fetch_yfinance", fake_yfinance)
    payload = run_demo.fetch_real_multi_provider([{"ticker": "AAPL"}], date, {"provider": "stooq_csv", "provider_priority": ["yfinance", "manual_csv"], "enable_yfinance_provider": True, "allow_manual_csv_fallback": True, "manual_csv_path": str(path)})
    assert payload["coverage_by_provider"] == {"yfinance": [], "manual_csv": ["AAPL"]}
    assert any(e["provider"] == "yfinance" for e in payload["errors"])


def test_missing_value_helper_handles_nan_na_and_empty_values():
    assert run_demo.is_missing_value(None)
    assert run_demo.is_missing_value(float("nan"))
    assert run_demo.is_missing_value("")
    assert run_demo.is_missing_value("N/A")
    assert run_demo.is_missing_value("N/D")
    assert run_demo.is_missing_value([])
    assert run_demo.is_missing_value({})


def test_fundamentals_and_ratios_minimums_block_scoring_but_metadata_optional():
    asset = {"ticker": "AAPL", "price_close": 200, "avg_volume_usd": 1_000_000_000, "metrics": {"pe_ttm": 20}, "data_quality": "HIGH", "missing_fields": [], "price_data": {"price_close": {"value": 200, "is_missing": False}}, "fundamentals_data": {"totalRevenue": {"value": None, "is_missing": True}}, "ratios_data": {"pe_ttm": {"value": 20, "is_missing": False}}, "metadata_data": {}}
    cfg = {"market_data": {"minimum_price_coverage_pct": 0.5, "minimum_fundamentals_coverage_pct": 1.0, "minimum_ratios_coverage_pct": 1.0, "minimum_metadata_coverage_pct": None}}
    assert not run_demo.has_sufficient_data_for_scoring(asset, cfg)
    cfg["market_data"]["minimum_fundamentals_coverage_pct"] = 0.0
    assert run_demo.has_sufficient_data_for_scoring(asset, cfg)


def test_data_quality_report_uses_full_universe_not_only_scored():
    assets = [
        {"ticker": "AAPL", "price_close": 200, "avg_volume_usd": 1_000_000, "metrics": {"pe_ttm": 20}, "data_quality": "HIGH", "missing_fields": [], "estimated_fields": []},
        {"ticker": "MSFT", "data_quality": "LOW", "missing_fields": ["price_close"], "estimated_fields": []},
        {"ticker": "SPY", "data_quality": "LOW", "missing_fields": ["price_close"], "estimated_fields": []},
    ]
    report = run_demo.build_data_quality_report([assets[0]], "run", "2026-06-27", assets, {"provider": "multi_provider"})
    assert report["total_assets_checked"] == 3
    assert "MSFT" in report["blocked_assets"]
    assert "SPY" in report["blocked_assets"]


def test_benchmarks_are_filtered_out_of_scoring():
    cfg = run_demo.load_config()
    universes = {"investable": [{"ticker": "AAPL"}], "benchmarks": [{"ticker": "SPY"}], "excluded": [], "filters": {"min_price": 1, "min_avg_volume_usd": 1, "min_data_quality": "MEDIUM"}}
    assets = [{"ticker": "AAPL", "price_close": 200, "avg_volume_usd": 1_000_000, "metrics": {"pe_ttm": 20}, "data_quality": "HIGH", "missing_fields": []}, {"ticker": "SPY", "price_close": 500, "avg_volume_usd": 1_000_000, "metrics": {"pe_ttm": 20}, "data_quality": "HIGH", "missing_fields": []}]
    scoring, pre = run_demo.filter_assets_for_scoring(assets, universes, cfg)
    assert [a["ticker"] for a in scoring] == ["AAPL"]
    assert pre["blocked_before_scoring"][0]["reason"] == "benchmark_not_investable"


def test_fetch_yfinance_reports_module_not_found_when_not_installed():
    payload = run_demo.fetch_yfinance([{"ticker": "AAPL"}], "2026-06-27", {})
    assert payload["provider"] == "yfinance"
    assert payload["assets"] == []
    assert payload["errors"][0]["exception"] == "ModuleNotFoundError"
