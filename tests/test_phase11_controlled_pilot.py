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
    assert report["data_readiness_status"] == "FAIL"
    assert any(check["name"] == "data_manual_csv_required_present" for check in report["errors"])


def test_preflight_warning_for_missing_benchmark():
    cfg = config_copy()
    csv_path = write_manual(Path("/tmp/phase11b_benchmark_warning.csv"))
    cfg["market_data"] = {**cfg["market_data"], "manual_csv_path": str(csv_path)}
    cfg["benchmark_universe"] = [b for b in cfg["benchmark_universe"] if b["ticker"] != "BIL"]
    report = controlled.build_preflight(cfg, "2026-06-27")
    assert report["status"] == "WARNING"
    warning = next(check for check in report["warnings"] if check["name"] == "benchmarks_present")
    assert "BIL" in warning["missing"]


def test_preflight_warning_with_partial_coverage_allowed(tmp_path):
    cfg = config_copy()
    csv_path = write_manual(tmp_path / "market_data.csv", tickers=controlled.INVESTABLE[:3])
    cfg["market_data"] = {**cfg["market_data"], "manual_csv_path": str(csv_path)}
    cfg["controlled_pilot_data_policy"] = {**cfg["controlled_pilot_data_policy"], "partial_coverage_below_threshold_status": "WARNING"}
    report = controlled.build_preflight(cfg, "2026-06-27")
    assert report["data_readiness_status"] == "WARNING"
    assert report["data_readiness"]["investable_coverage"]["covered_tickers"] == controlled.INVESTABLE[:3]


def test_preflight_fail_with_zero_coverage(tmp_path):
    cfg = config_copy()
    csv_path = write_manual(tmp_path / "market_data.csv", tickers=["ZZZZ"])
    cfg["market_data"] = {**cfg["market_data"], "manual_csv_path": str(csv_path)}
    report = controlled.build_preflight(cfg, "2026-06-27")
    assert report["data_readiness_status"] == "FAIL"
    assert any(check["name"] == "data_investable_coverage" for check in report["errors"])


def test_provider_probe_records_403_or_timeout(monkeypatch):
    cfg = config_copy()

    def fake_probe(config, date, tickers):
        return {"provider": "stooq_csv", "tested_tickers": tickers, "covered_tickers": [], "missing_tickers": tickers, "provider_probe_errors": [{"provider": "stooq_csv", "error": "Tunnel connection failed: 403 Forbidden"}, {"provider": "stooq_csv", "error": "timed out"}]}

    monkeypatch.setattr(controlled, "provider_probe", fake_probe)
    report = controlled.build_preflight(cfg, "2026-06-27", probe_data_provider=True)
    errors = report["data_readiness"]["provider_probe_errors"]
    assert "403 Forbidden" in json.dumps(errors)
    assert "timed out" in json.dumps(errors)


def test_missing_benchmarks_fail_when_required(tmp_path):
    cfg = config_copy()
    csv_path = write_manual(tmp_path / "market_data.csv")
    cfg["market_data"] = {**cfg["market_data"], "manual_csv_path": str(csv_path)}
    cfg["controlled_pilot_data_policy"] = {**cfg["controlled_pilot_data_policy"], "required_benchmarks_policy": "required"}
    cfg["benchmark_universe"] = [b for b in cfg["benchmark_universe"] if b["ticker"] != "BIL"]
    report = controlled.build_preflight(cfg, "2026-06-27")
    assert report["status"] == "FAIL"
    assert any(check["name"] == "data_benchmarks_coverage" for check in report["errors"])


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


def test_control_report_warning_pilot_cannot_mask_fail_coverage(monkeypatch, tmp_path):
    report_dir = ROOT / "outputs" / "test_control_report_warning_pilot_cannot_mask_fail_coverage"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "real_data_pilot_report.json"
    report_path.write_text(json.dumps({"status": "WARNING", "price_available": controlled.INVESTABLE[:1], "fundamentals_available": controlled.INVESTABLE, "ratios_available": controlled.INVESTABLE, "metadata_available": controlled.INVESTABLE, "real_data_coverage_pct": 1 / len(controlled.INVESTABLE), "warnings": [{"name": "existing_warning"}], "errors": []}), encoding="utf-8")
    preflight = {"status": "WARNING", "errors": [], "warnings": [{"message": "warning previo"}], "safety_confirmation": {"broker_connected": False, "allow_real_orders": False, "real_orders_possible": False}, "data_readiness_status": "WARNING"}
    cfg = config_copy()
    cfg["market_data"] = {**cfg["market_data"], "minimum_price_coverage_pct": 0.9, "minimum_fundamentals_coverage_pct": 0.0, "minimum_ratios_coverage_pct": 0.0, "minimum_metadata_coverage_pct": None}
    cfg["controlled_pilot_data_policy"] = {**cfg["controlled_pilot_data_policy"], "partial_coverage_below_threshold_status": "FAIL"}
    monkeypatch.setattr(run_demo, "load_config", lambda: cfg)
    proc = subprocess.CompletedProcess(["pilot"], 0, f"Reporte JSON: {report_path.relative_to(ROOT)}\n", "")

    report = controlled.build_control_report(preflight, proc, True, controlled.sha256_file(run_demo.CONFIG_PATH))

    assert report["pilot_status"] == "WARNING"
    assert report["status"] == "FAIL"
    assert report["data_coverage"]["effective_coverage_failures"]
