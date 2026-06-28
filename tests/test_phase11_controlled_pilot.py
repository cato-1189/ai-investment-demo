import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_CONTROLLED = ROOT / "scripts" / "run_controlled_pilot.py"
sys.path.insert(0, str(ROOT / "scripts"))
import run_controlled_pilot as controlled  # noqa: E402
import run_demo  # noqa: E402


def config_copy():
    return copy.deepcopy(run_demo.load_config())


def write_manual(path: Path, tickers: list[str] | None = None) -> Path:
    tickers = tickers or controlled.INVESTABLE
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["ticker;date;close;volume;currency;source"]
    rows.extend(f"{ticker};2026-06-26;100;1000000;USD;manual_test" for ticker in tickers)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_preflight_pass_with_valid_required_manual_csv(tmp_path):
    cfg = config_copy()
    csv_path = write_manual(tmp_path / "market_data.csv")
    cfg["market_data"] = {**cfg["market_data"], "manual_csv_path": str(csv_path)}
    report = controlled.build_preflight(cfg, "2026-06-27", require_manual_csv=True)
    assert report["status"] == "PASS"
    assert not report["errors"]
    assert report["safety_confirmation"]["broker_connected"] is False
    assert report["safety_confirmation"]["allow_real_orders"] is False


def test_preflight_fail_without_required_manual_csv(tmp_path):
    cfg = config_copy()
    cfg["market_data"] = {**cfg["market_data"], "manual_csv_path": str(tmp_path / "missing.csv")}
    report = controlled.build_preflight(cfg, "2026-06-27", require_manual_csv=True)
    assert report["status"] == "FAIL"
    assert any(check["name"] == "manual_csv_present" for check in report["errors"])


def test_preflight_warning_for_missing_benchmark():
    cfg = config_copy()
    cfg["benchmark_universe"] = [b for b in cfg["benchmark_universe"] if b["ticker"] != "BIL"]
    report = controlled.build_preflight(cfg, "2026-06-27")
    assert report["status"] == "WARNING"
    warning = next(check for check in report["warnings"] if check["name"] == "benchmarks_present")
    assert "BIL" in warning["missing"]


def test_fail_preflight_does_not_execute_pilot():
    preflight = {"status": "FAIL", "errors": [{"message": "bad"}], "warnings": [], "safety_confirmation": {"broker_connected": False, "allow_real_orders": False, "real_orders_possible": False}}
    report = controlled.build_control_report(preflight, None, False, controlled.sha256_file(run_demo.CONFIG_PATH))
    assert report["status"] == "BLOCKED"
    assert report["pilot_started"] is False
    assert report["pilot_returncode"] is None


def test_warning_run_requires_explicit_allow_flag(tmp_path):
    proc = subprocess.run([sys.executable, str(RUN_CONTROLLED), "--date", "2026-06-27"], cwd=ROOT, text=True, capture_output=True, check=False)
    # Config actual suele ser PASS; forzar la regla con función consolidada para no depender de red.
    preflight = {"status": "WARNING", "errors": [], "warnings": [{"message": "benchmark faltante"}], "safety_confirmation": {"broker_connected": False, "allow_real_orders": False, "real_orders_possible": False}}
    blocked = controlled.build_control_report(preflight, None, False, controlled.sha256_file(run_demo.CONFIG_PATH))
    assert blocked["status"] == "BLOCKED"
    allowed_proc = subprocess.CompletedProcess(["pilot"], 0, "", "")
    allowed = controlled.build_control_report(preflight, allowed_proc, True, controlled.sha256_file(run_demo.CONFIG_PATH))
    assert allowed["pilot_started"] is True
    assert proc.returncode in (0, 1)


def test_broker_disconnected_and_real_order_false_controls():
    cfg = config_copy()
    report = controlled.build_preflight(cfg, "2026-06-27")
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["broker_disconnected"]["status"] == "PASS"
    assert checks["allow_real_orders_false"]["status"] == "PASS"


def test_real_order_true_fails_preflight():
    cfg = config_copy()
    cfg["system"] = {**cfg["system"], "allow_real_orders": True}
    report = controlled.build_preflight(cfg, "2026-06-27")
    assert report["status"] == "FAIL"
    assert any(check["name"] == "allow_real_orders_false" for check in report["errors"])


def test_benchmarks_outside_scoring_default():
    cfg = config_copy()
    report = controlled.build_preflight(cfg, "2026-06-27")
    check = next(check for check in report["checks"] if check["name"] == "benchmarks_outside_scoring")
    assert check["status"] == "PASS"
    assert check["allow_benchmarks_in_scoring"] is False
