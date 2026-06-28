import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_DEMO = ROOT / "scripts" / "run_demo.py"
RUN_PILOT = ROOT / "scripts" / "run_real_data_pilot.py"


def output_root(stdout: str) -> Path:
    for line in stdout.splitlines():
        if line.startswith("Outputs: "):
            return ROOT / line.split("Outputs: ", 1)[1]
    raise AssertionError(stdout)


def test_fixture_default_still_uses_fixture_and_no_broker():
    proc = subprocess.run([sys.executable, str(RUN_DEMO), "--date", "2026-06-27", "--universe-symbols", "AAPL,MSFT,SPY"], cwd=ROOT, text=True, capture_output=True, check=True)
    out = output_root(proc.stdout)
    manifest = json.loads((out / "run_manifest.json").read_text())
    dq = json.loads((out / "data_quality_report.json").read_text())
    assert manifest["broker_connected"] is False
    assert manifest["allow_real_orders"] is False
    assert manifest["external_apis_used"] is False
    assert dq["source"] == "local_fixture"


def test_real_pilot_requires_explicit_activation():
    proc = subprocess.run([sys.executable, str(RUN_PILOT), "--date", "2026-06-27"], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode != 0
    assert "--activate-real-data-pilot" in proc.stderr or "--activate-real-data-pilot" in proc.stdout


def test_real_pilot_report_security_and_visibility():
    proc = subprocess.run([sys.executable, str(RUN_PILOT), "--date", "2026-06-27", "--activate-real-data-pilot"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert proc.returncode in (0, 1), proc.stderr
    report_path = None
    for line in proc.stdout.splitlines():
        if line.startswith("Reporte JSON: "):
            report_path = ROOT / line.split("Reporte JSON: ", 1)[1]
    assert report_path and report_path.exists(), proc.stdout
    report = json.loads(report_path.read_text())
    assert report["provider"] in {"stooq_csv", "multi_provider"}
    assert report["requested_tickers"]
    assert "tickers_without_data" in report
    assert report["safety"]["broker_connected"] is False
    assert report["safety"]["allow_real_orders"] is False
    assert set(report["safety"]["real_order_values"]) <= {"false"}
    assert not report["benchmarks_in_scoring"]
    assert set(report["requested_benchmarks"]).isdisjoint(set(report["assets_sent_to_scoring"]))
    assert (report_path.parent / "real_data_pilot_report.md").exists()


def test_real_pilot_report_excludes_estimated_ratios_from_effective_coverage(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_real_data_pilot as pilot  # noqa: E402

    out = ROOT / "outputs" / "test_real_pilot_report_excludes_estimated_ratios"
    (out / "snapshots").mkdir(parents=True, exist_ok=True)
    (out / "scoring_results.json").write_text("[]\n", encoding="utf-8")
    (out / "data_quality_report.json").write_text(json.dumps({"investable_assets_blocked": [], "benchmarks_available": [], "provider_errors": [], "missing_data": []}), encoding="utf-8")
    (out / "run_manifest.json").write_text(json.dumps({"broker_connected": False, "allow_real_orders": False, "llms_used": False}), encoding="utf-8")
    (out / "simulated_trades.csv").write_text("ticker,real_order\nAAPL,false\n", encoding="utf-8")
    (out / "snapshots" / "raw_market_data.json").write_text(json.dumps({"provider": "manual_csv", "minimum_real_data_coverage_pct": 0.0}), encoding="utf-8")
    normalized = [{
        "ticker": "AAPL",
        "provider": "manual_csv",
        "price_data": {"price_close": {"value": 200, "is_missing": False, "is_estimated": False}},
        "ratios_data": {"pe_ttm": {"value": 20, "is_missing": False, "is_estimated": True, "provider": "fixture"}},
        "fundamentals_data": {},
        "metadata_data": {},
    }]
    (out / "snapshots" / "normalized_market_data.json").write_text(json.dumps(normalized), encoding="utf-8")

    report = pilot.build_pilot_report(out, ["pilot"], f"Outputs: {out.relative_to(ROOT)}\n", "")

    assert "AAPL" in report["price_available"]
    assert "AAPL" not in report["ratios_available"]
    assert "AAPL" in report["ratios_estimated_available"]
