#!/usr/bin/env python3
"""Fase 11: preflight operativo y ejecución controlada del piloto DEMO/paper trading."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_demo  # noqa: E402
from run_real_data_pilot import BENCHMARKS, INVESTABLE  # noqa: E402

RUN_REAL_DATA_PILOT = ROOT / "scripts" / "run_real_data_pilot.py"
MIN_MANUAL_COLUMNS = {"ticker", "date", "close", "volume", "currency", "source"}
MAX_CONTROLLED_INVESTABLES = 10


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def manual_csv_path(config: dict[str, Any], date: str) -> Path:
    settings = run_demo.market_data_settings(config)
    return run_demo.manual_csv_path(settings, date)


def read_manual_header_and_tickers(path: Path) -> tuple[set[str], set[str], str | None]:
    if not path.exists():
        return set(), set(), None
    sample = path.read_text(encoding="utf-8").splitlines()
    header = sample[0] if sample else ""
    delimiter = ";" if header.count(";") >= header.count(",") else ","
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = set(reader.fieldnames or [])
        tickers = {str(row.get("ticker", "")).strip().upper() for row in reader if row.get("ticker")}
    return columns, tickers, delimiter


def add_check(checks: list[dict[str, Any]], name: str, status: str, message: str, **details: Any) -> None:
    checks.append({"name": name, "status": status, "message": message, **details})


def preflight_status(checks: list[dict[str, Any]]) -> str:
    statuses = {c["status"] for c in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    return "PASS"


def build_preflight(config: dict[str, Any], date: str, *, require_manual_csv: bool = False, allow_real_llm: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    settings = run_demo.market_data_settings(config)
    llm = run_demo.llm_settings(config)
    agents = set(llm.get("real_agents", []))
    config_text = run_demo.CONFIG_PATH.read_text(encoding="utf-8")

    add_check(checks, "demo_paper_trading_mode", "PASS" if config.get("system", {}).get("mode") == "DEMO_PAPER_TRADING" else "FAIL", "El modo debe ser DEMO_PAPER_TRADING.", observed=config.get("system", {}).get("mode"))
    add_check(checks, "broker_disconnected", "PASS" if "broker_connected: true" not in config_text.lower() and "broker_provider" not in config_text.lower() else "FAIL", "No hay broker configurado ni conectado.")
    add_check(checks, "allow_real_orders_false", "PASS" if config.get("system", {}).get("allow_real_orders") is False else "FAIL", "system.allow_real_orders debe permanecer false.", observed=config.get("system", {}).get("allow_real_orders"))
    add_check(checks, "decision_audit_real_disabled", "PASS" if not ({"decision_agent", "audit_agent"} & agents) else "FAIL", "decision_agent y audit_agent reales deben estar deshabilitados.", real_agents=sorted(agents))
    llm_ok = not llm.get("enabled") or allow_real_llm
    add_check(checks, "llm_real_disabled", "PASS" if llm_ok else "FAIL", "LLM real deshabilitado salvo autorización explícita.", llm_enabled=bool(llm.get("enabled")), allow_real_llm=allow_real_llm)
    provider_ok = bool(settings.get("provider")) and settings.get("provider") in {"fixture", "stooq_csv", "manual_csv", "yfinance", "multi_provider"}
    add_check(checks, "data_provider_configured", "PASS" if provider_ok else "FAIL", "Proveedor de datos configurado y soportado.", provider=settings.get("provider"), provider_priority=settings.get("provider_priority"))

    manual_required = require_manual_csv or settings.get("provider") == "manual_csv"
    csv_path = manual_csv_path(config, date)
    if manual_required:
        if not csv_path.exists():
            add_check(checks, "manual_csv_present", "FAIL", "CSV manual requerido pero ausente.", path=rel(csv_path))
        else:
            add_check(checks, "manual_csv_present", "PASS", "CSV manual requerido presente.", path=rel(csv_path))
            columns, tickers, delimiter = read_manual_header_and_tickers(csv_path)
            missing_cols = sorted(MIN_MANUAL_COLUMNS - columns)
            add_check(checks, "manual_csv_min_columns", "PASS" if not missing_cols else "FAIL", "Columnas mínimas del CSV manual.", columns=sorted(columns), missing_columns=missing_cols, delimiter=delimiter)
            missing_tickers = sorted(set(INVESTABLE) - tickers)
            add_check(checks, "manual_csv_expected_tickers", "PASS" if not missing_tickers else "FAIL", "Tickers invertibles esperados presentes en CSV manual requerido.", expected=INVESTABLE, missing_tickers=missing_tickers)
    else:
        add_check(checks, "manual_csv_optional", "PASS", "CSV manual no requerido para esta preflight; puede usarse como fallback si existe.", path=rel(csv_path), exists=csv_path.exists())

    bench_present = sorted(set(BENCHMARKS) & {b.get("ticker") for b in config.get("benchmark_universe", [])})
    bench_missing = sorted(set(BENCHMARKS) - set(bench_present))
    min_fund = float(settings.get("minimum_fundamentals_coverage_pct", 0.0))
    min_rat = float(settings.get("minimum_ratios_coverage_pct", 0.0))
    add_check(checks, "financial_coverage_policy", "PASS", "Política mínima de cobertura financiera cargada; se valida en corrida contra datos normalizados.", minimum_fundamentals_coverage_pct=min_fund, minimum_ratios_coverage_pct=min_rat, required_fields_for_scoring=settings.get("required_fields_for_scoring", []), fail_if_required_financial_fields_missing=settings.get("fail_if_required_financial_fields_missing", True))

    add_check(checks, "benchmarks_present", "PASS" if not bench_missing else "WARNING", "Benchmarks presentes o advertidos; no bloquean scoring invertible.", expected=BENCHMARKS, present=bench_present, missing=bench_missing)

    universe_ok = 0 < len(INVESTABLE) <= MAX_CONTROLLED_INVESTABLES
    add_check(checks, "small_universe_defined", "PASS" if universe_ok else "FAIL", "Universo controlado pequeño definido.", investable_tickers=INVESTABLE, max_allowed=MAX_CONTROLLED_INVESTABLES)
    add_check(checks, "config_demo_not_auto_modified", "PASS", "La preflight solo lee config/config_demo.yaml; no la modifica automáticamente.", config_path=rel(run_demo.CONFIG_PATH), sha256=sha256_file(run_demo.CONFIG_PATH))
    add_check(checks, "benchmarks_outside_scoring", "PASS" if not run_demo.universe_builder_settings(config).get("allow_benchmarks_in_scoring", False) else "FAIL", "Benchmarks no deben entrar al scoring.", allow_benchmarks_in_scoring=run_demo.universe_builder_settings(config).get("allow_benchmarks_in_scoring", False))

    status = preflight_status(checks)
    return {
        "phase": "FASE_11_CONTROLLED_PILOT_PREFLIGHT",
        "date": date,
        "generated_at_utc": utc_now().isoformat(),
        "status": status,
        "checks": checks,
        "errors": [c for c in checks if c["status"] == "FAIL"],
        "warnings": [c for c in checks if c["status"] == "WARNING"],
        "safety_confirmation": {"mode": config.get("system", {}).get("mode"), "broker_connected": False, "allow_real_orders": config.get("system", {}).get("allow_real_orders"), "decision_agent_real": "decision_agent" in agents, "audit_agent_real": "audit_agent" in agents, "llm_enabled": bool(llm.get("enabled")), "real_orders_possible": False},
        "controlled_universe": {"investable": INVESTABLE, "benchmarks": BENCHMARKS},
    }


def write_preflight_md(path: Path, report: dict[str, Any]) -> None:
    lines = ["# Preflight controlled pilot - Fase 11", "", f"- Estado: **{report['status']}**", f"- Fecha: `{report['date']}`", "", "## Checks"]
    for c in report["checks"]:
        lines.append(f"- **{c['status']}** `{c['name']}`: {c['message']}")
    lines += ["", "## Seguridad", f"- Broker conectado: `{report['safety_confirmation']['broker_connected']}`", f"- allow_real_orders: `{report['safety_confirmation']['allow_real_orders']}`", f"- Órdenes reales posibles: `{report['safety_confirmation']['real_orders_possible']}`", "- decision_agent y audit_agent reales deshabilitados."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_report_path(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        if line.startswith("Reporte JSON: "):
            return ROOT / line.split("Reporte JSON: ", 1)[1].strip()
    return None


def build_control_report(preflight: dict[str, Any], pilot_proc: subprocess.CompletedProcess[str] | None, started: bool, config_hash_before: str | None) -> dict[str, Any]:
    pilot_report_path = find_report_path(pilot_proc.stdout) if pilot_proc else None
    pilot_report = json.loads(pilot_report_path.read_text(encoding="utf-8")) if pilot_report_path and pilot_report_path.exists() else None
    config_hash_after = sha256_file(run_demo.CONFIG_PATH)
    errors = [c["message"] for c in preflight.get("errors", [])]
    warnings = [c["message"] for c in preflight.get("warnings", [])]
    if pilot_proc and pilot_proc.returncode not in (0,):
        if pilot_report and pilot_report.get("status") == "WARNING":
            warnings.append("El piloto terminó con WARNING.")
        else:
            errors.append(f"Piloto terminó con código {pilot_proc.returncode}.")
    status = "BLOCKED" if not started else ("FAIL" if errors and not (pilot_report and pilot_report.get("status") == "WARNING" and not [e for e in errors if 'código' in e]) else (pilot_report or {}).get("status", "PASS"))
    outputs = []
    if pilot_report_path:
        outputs.extend([rel(pilot_report_path), rel(pilot_report_path.with_suffix(".md"))])
    return {
        "phase": "FASE_11_CONTROLLED_PILOT_RUN_CONTROL",
        "status": status,
        "preflight_status": preflight["status"],
        "pilot_started": started,
        "pilot_returncode": pilot_proc.returncode if pilot_proc else None,
        "pilot_status": (pilot_report or {}).get("status") if pilot_report else None,
        "data_coverage": {"real_data_coverage_pct": (pilot_report or {}).get("real_data_coverage_pct"), "coverage_by_provider": (pilot_report or {}).get("coverage_by_provider"), "tickers_without_data": (pilot_report or {}).get("tickers_without_data"), "benchmarks_missing": (pilot_report or {}).get("benchmarks_missing")},
        "errors": errors,
        "warnings": warnings,
        "outputs_generated": outputs,
        "safety_confirmation": {**preflight["safety_confirmation"], "config_demo_yaml_modified": config_hash_before != config_hash_after, "config_hash_before": config_hash_before, "config_hash_after": config_hash_after},
        "recommendation": recommendation(preflight["status"], started, pilot_report),
        "stdout_tail": pilot_proc.stdout.splitlines()[-30:] if pilot_proc else [],
        "stderr": pilot_proc.stderr if pilot_proc else "",
    }


def recommendation(preflight_status_value: str, started: bool, pilot_report: dict[str, Any] | None) -> str:
    if preflight_status_value == "FAIL":
        return "No ejecutar. Corregir checks FAIL y repetir preflight."
    if not started:
        return "Preflight con WARNING: revisar advertencias o reintentar con --allow-warning-run si el humano acepta el riesgo DEMO."
    if pilot_report and pilot_report.get("status") == "PASS":
        return "Corrida controlada apta para revisión humana; no automatizar todavía."
    return "Revisar warnings/cobertura antes de avanzar; mantener Fase 12 pendiente."


def write_control_md(path: Path, report: dict[str, Any]) -> None:
    lines = ["# Run control report - Fase 11", "", f"- Estado: **{report['status']}**", f"- Preflight: `{report['preflight_status']}`", f"- Piloto ejecutado: `{report['pilot_started']}`", f"- Estado piloto: `{report['pilot_status']}`", "", "## Cobertura", f"- Cobertura real: `{report['data_coverage']['real_data_coverage_pct']}`", f"- Tickers sin datos: `{report['data_coverage']['tickers_without_data']}`", f"- Benchmarks faltantes: `{report['data_coverage']['benchmarks_missing']}`", "", "## Seguridad", f"- Broker conectado: `{report['safety_confirmation']['broker_connected']}`", f"- allow_real_orders: `{report['safety_confirmation']['allow_real_orders']}`", f"- Config modificada automáticamente: `{report['safety_confirmation']['config_demo_yaml_modified']}`", "", "## Warnings y errores", f"- Warnings: `{report['warnings']}`", f"- Errores: `{report['errors']}`", "", "## Próxima acción", report["recommendation"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 11: controlled pilot con preflight operativo antes de ejecutar el piloto real DEMO/paper trading.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--allow-warning-run", action="store_true", help="Permite ejecutar si preflight queda WARNING.")
    parser.add_argument("--require-manual-csv", action="store_true", help="Exige CSV manual válido para esta corrida controlada.")
    parser.add_argument("--allow-real-llm", action="store_true", help="Autoriza explícitamente LLM real si config lo habilita; no habilita decision/audit reales.")
    args = parser.parse_args()

    run_id = f"{args.date}_controlled_phase11_{utc_now().strftime('%H%M%S%f')}"
    out_root = ROOT / "outputs" / "controlled_runs" / args.date / run_id
    out_root.mkdir(parents=True, exist_ok=True)
    config_hash_before = sha256_file(run_demo.CONFIG_PATH)
    config = run_demo.load_config()
    preflight = build_preflight(config, args.date, require_manual_csv=args.require_manual_csv, allow_real_llm=args.allow_real_llm)
    write_json(out_root / "preflight_report.json", preflight)
    write_preflight_md(out_root / "preflight_report.md", preflight)

    started = False
    proc: subprocess.CompletedProcess[str] | None = None
    if preflight["status"] == "PASS" or (preflight["status"] == "WARNING" and args.allow_warning_run):
        started = True
        cmd = [sys.executable, str(RUN_REAL_DATA_PILOT), "--date", args.date, "--activate-real-data-pilot"]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    control = build_control_report(preflight, proc, started, config_hash_before)
    write_json(out_root / "run_control_report.json", control)
    write_control_md(out_root / "run_control_report.md", control)
    print(f"Preflight: {preflight['status']}")
    print(f"Controlled pilot started: {started}")
    print(f"Preflight report JSON: {rel(out_root / 'preflight_report.json')}")
    print(f"Run control report JSON: {rel(out_root / 'run_control_report.json')}")
    if preflight["status"] == "FAIL" or (preflight["status"] == "WARNING" and not args.allow_warning_run):
        return 1
    return proc.returncode if proc else 0


if __name__ == "__main__":
    raise SystemExit(main())
