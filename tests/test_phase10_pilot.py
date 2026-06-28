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
    assert report["provider"] == "stooq_csv"
    assert report["requested_tickers"]
    assert "tickers_without_data" in report
    assert report["safety"]["broker_connected"] is False
    assert report["safety"]["allow_real_orders"] is False
    assert set(report["safety"]["real_order_values"]) <= {"false"}
    assert not report["benchmarks_in_scoring"]
    assert set(report["requested_benchmarks"]).isdisjoint(set(report["assets_sent_to_scoring"]))
    assert (report_path.parent / "real_data_pilot_report.md").exists()
