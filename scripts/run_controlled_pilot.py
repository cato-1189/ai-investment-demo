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
DATA_POLICY_DEFAULTS = {
    "require_manual_csv_for_controlled_pilot": False,
    "allow_online_provider_probe": False,
    "minimum_preflight_data_coverage_pct": 0.60,
    "partial_coverage_below_threshold_status": "WARNING",
    "required_benchmarks_policy": "warning",
}


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


def sniff_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8").splitlines()
    header = sample[0] if sample else ""
    return ";" if header.count(";") >= header.count(",") else ","


def parse_positive_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def read_manual_csv_quality(path: Path, wanted: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": rel(path),
        "exists": path.exists(),
        "delimiter": None,
        "columns": [],
        "missing_columns": sorted(MIN_MANUAL_COLUMNS),
        "covered_tickers": [],
        "missing_tickers": list(wanted),
        "parse_errors": [],
    }
    if not path.exists():
        return result
    delimiter = sniff_delimiter(path)
    result["delimiter"] = delimiter
    covered: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = set(reader.fieldnames or [])
        result["columns"] = sorted(columns)
        result["missing_columns"] = sorted(MIN_MANUAL_COLUMNS - columns)
        if result["missing_columns"]:
            return result
        for idx, row in enumerate(reader, start=2):
            ticker = str(row.get("ticker", "")).strip().upper()
            if ticker not in set(wanted):
                continue
            close = parse_positive_float(row.get("close"))
            volume = parse_positive_float(row.get("volume"))
            if close is None or volume is None:
                result["parse_errors"].append({"line": idx, "ticker": ticker, "close": row.get("close"), "volume": row.get("volume"), "error": "close/volume deben ser numéricos positivos"})
                continue
            covered.add(ticker)
    result["covered_tickers"] = sorted(covered)
    result["missing_tickers"] = sorted(set(wanted) - covered)
    return result


def data_policy(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("controlled_pilot_data_policy", {}) or {}
    policy = {**DATA_POLICY_DEFAULTS, **raw}
    if policy.get("required_benchmarks_policy") not in {"required", "warning", "ignore"}:
        policy["required_benchmarks_policy"] = "warning"
    if policy.get("partial_coverage_below_threshold_status") not in {"WARNING", "FAIL"}:
        policy["partial_coverage_below_threshold_status"] = "WARNING"
    return policy


def build_probe_universe(tickers: list[str]) -> list[dict[str, str]]:
    return [{"ticker": ticker} for ticker in tickers]


def provider_probe(config: dict[str, Any], date: str, tickers: list[str]) -> dict[str, Any]:
    settings = {**run_demo.market_data_settings(config), "provider_priority": ["stooq_csv"], "allow_manual_csv_fallback": False}
    payload = run_demo.fetch_real_multi_provider(build_probe_universe(tickers), date, settings)
    normalized, _ = run_demo.normalize_market_data(payload, {"market_data": settings}, date)
    covered = sorted(a["ticker"] for a in normalized if a.get("ticker") in set(tickers) and not a.get("missing_fields") and a.get("data_quality") in {"MEDIUM", "HIGH"})
    return {"provider": "stooq_csv", "tested_tickers": tickers, "covered_tickers": covered, "missing_tickers": sorted(set(tickers) - set(covered)), "provider_probe_errors": payload.get("errors", [])}


def build_data_readiness(config: dict[str, Any], date: str, *, require_manual_csv: bool = False, probe_data_provider: bool = False) -> dict[str, Any]:
    policy = data_policy(config)
    required_manual = bool(require_manual_csv or policy["require_manual_csv_for_controlled_pilot"] or run_demo.market_data_settings(config).get("provider") == "manual_csv")
    threshold = float(policy["minimum_preflight_data_coverage_pct"])
    csv_path = manual_csv_path(config, date)
    csv_quality = read_manual_csv_quality(csv_path, INVESTABLE)
    coverage_source = None
    covered = set(csv_quality["covered_tickers"])
    provider_result = {"provider": None, "tested_tickers": [], "covered_tickers": [], "missing_tickers": [], "provider_probe_errors": []}
    if covered:
        coverage_source = "manual_csv"
    should_probe = bool(probe_data_provider or policy["allow_online_provider_probe"])
    if should_probe and not required_manual:
        try:
            provider_result = provider_probe(config, date, INVESTABLE[: min(3, len(INVESTABLE))])
            covered.update(provider_result["covered_tickers"])
            if provider_result["covered_tickers"]:
                coverage_source = "stooq_csv_probe"
        except Exception as exc:  # keep provider errors visible, do not hide them
            provider_result = {"provider": "stooq_csv", "tested_tickers": INVESTABLE[: min(3, len(INVESTABLE))], "covered_tickers": [], "missing_tickers": INVESTABLE[: min(3, len(INVESTABLE))], "provider_probe_errors": [{"provider": "stooq_csv", "error": str(exc)}]}
    coverage_pct = round(len(covered) / len(INVESTABLE), 4) if INVESTABLE else 0.0
    bench_present = sorted(set(BENCHMARKS) & {b.get("ticker") for b in config.get("benchmark_universe", [])})
    bench_missing = sorted(set(BENCHMARKS) - set(bench_present))
    checks: list[dict[str, Any]] = []
    if required_manual and not csv_quality["exists"]:
        add_check(checks, "data_manual_csv_required_present", "FAIL", "CSV manual requerido ausente.", path=csv_quality["path"])
    if csv_quality["exists"]:
        add_check(checks, "data_manual_csv_min_columns", "PASS" if not csv_quality["missing_columns"] else "FAIL", "CSV manual con columnas mínimas.", missing_columns=csv_quality["missing_columns"])
        add_check(checks, "data_manual_csv_prices_volumes_parse", "PASS" if not csv_quality["parse_errors"] else "FAIL", "Precios y volúmenes del CSV manual parsean como positivos.", parse_errors=csv_quality["parse_errors"][:10])
    if len(covered) == 0:
        add_check(checks, "data_investable_coverage", "FAIL", "No hay datos válidos para ningún activo invertible.", coverage_pct=coverage_pct)
    elif coverage_pct < threshold:
        add_check(checks, "data_investable_coverage", policy["partial_coverage_below_threshold_status"], "Cobertura parcial por debajo del umbral configurado.", coverage_pct=coverage_pct, minimum_preflight_data_coverage_pct=threshold)
    else:
        add_check(checks, "data_investable_coverage", "PASS", "Cobertura mínima de activos invertibles disponible.", coverage_pct=coverage_pct, minimum_preflight_data_coverage_pct=threshold)
    bench_status = "PASS"
    if bench_missing and policy["required_benchmarks_policy"] == "required":
        bench_status = "FAIL"
    elif bench_missing and policy["required_benchmarks_policy"] == "warning":
        bench_status = "WARNING"
    add_check(checks, "data_benchmarks_coverage", bench_status, "Benchmarks requeridos presentes según política configurada.", policy=policy["required_benchmarks_policy"], present=bench_present, missing=bench_missing)
    status = preflight_status(checks)
    return {
        "status": status,
        "policy": policy,
        "human_explanation": human_data_readiness_explanation(status, coverage_pct, threshold, csv_quality, provider_result, bench_missing),
        "manual_csv": csv_quality,
        "investable_coverage": {"coverage_pct": coverage_pct, "minimum_preflight_data_coverage_pct": threshold, "covered_tickers": sorted(covered), "missing_tickers": sorted(set(INVESTABLE) - covered), "coverage_source": coverage_source},
        "benchmark_coverage": {"policy": policy["required_benchmarks_policy"], "present": bench_present, "missing": bench_missing},
        "provider_probe": provider_result,
        "provider_used_or_probed": coverage_source or ("stooq_csv" if should_probe else None),
        "provider_probe_errors": provider_result.get("provider_probe_errors", []),
        "checks": checks,
    }


def human_data_readiness_explanation(status: str, coverage_pct: float, threshold: float, csv_quality: dict[str, Any], provider_result: dict[str, Any], bench_missing: list[str]) -> str:
    parts = [f"Data readiness {status}: cobertura invertible {coverage_pct:.0%} contra umbral {threshold:.0%}."]
    parts.append("CSV manual detectado." if csv_quality.get("exists") else "CSV manual no detectado.")
    if provider_result.get("provider_probe_errors"):
        parts.append("El probe online registró errores visibles; revisar provider_probe_errors.")
    if bench_missing:
        parts.append(f"Benchmarks faltantes: {', '.join(bench_missing)}.")
    return " ".join(parts)


def add_check(checks: list[dict[str, Any]], name: str, status: str, message: str, **details: Any) -> None:
    checks.append({"name": name, "status": status, "message": message, **details})


def preflight_status(checks: list[dict[str, Any]]) -> str:
    statuses = {c["status"] for c in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    return "PASS"


def build_preflight(config: dict[str, Any], date: str, *, require_manual_csv: bool = False, allow_real_llm: bool = False, probe_data_provider: bool = False) -> dict[str, Any]:
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
    provider_ok = bool(settings.get("provider")) and settings.get("provider") in {"fixture", "stooq_csv", "manual_csv", "yfinance"}
    add_check(checks, "data_provider_configured", "PASS" if provider_ok else "FAIL", "Proveedor de datos configurado y soportado.", provider=settings.get("provider"), provider_priority=settings.get("provider_priority"))

    data_readiness = build_data_readiness(config, date, require_manual_csv=require_manual_csv, probe_data_provider=probe_data_provider)
    checks.extend(data_readiness["checks"])

    bench_present = sorted(set(BENCHMARKS) & {b.get("ticker") for b in config.get("benchmark_universe", [])})
    bench_missing = sorted(set(BENCHMARKS) - set(bench_present))
    benchmark_policy = data_readiness["benchmark_coverage"]["policy"]
    benchmark_status = "PASS" if not bench_missing or benchmark_policy == "ignore" else ("FAIL" if benchmark_policy == "required" else "WARNING")
    add_check(checks, "benchmarks_present", benchmark_status, "Benchmarks presentes según política; nunca entran al scoring invertible.", expected=BENCHMARKS, present=bench_present, missing=bench_missing, policy=benchmark_policy)

    universe_ok = 0 < len(INVESTABLE) <= MAX_CONTROLLED_INVESTABLES
    add_check(checks, "small_universe_defined", "PASS" if universe_ok else "FAIL", "Universo controlado pequeño definido.", investable_tickers=INVESTABLE, max_allowed=MAX_CONTROLLED_INVESTABLES)
    add_check(checks, "config_demo_not_auto_modified", "PASS", "La preflight solo lee config/config_demo.yaml; no la modifica automáticamente.", config_path=rel(run_demo.CONFIG_PATH), sha256=sha256_file(run_demo.CONFIG_PATH))
    add_check(checks, "benchmarks_outside_scoring", "PASS" if not run_demo.universe_builder_settings(config).get("allow_benchmarks_in_scoring", False) else "FAIL", "Benchmarks no deben entrar al scoring.", allow_benchmarks_in_scoring=run_demo.universe_builder_settings(config).get("allow_benchmarks_in_scoring", False))

    status = preflight_status(checks)
    return {
        "phase": "FASE_11B_CONTROLLED_PILOT_PREFLIGHT",
        "date": date,
        "generated_at_utc": utc_now().isoformat(),
        "status": status,
        "checks": checks,
        "errors": [c for c in checks if c["status"] == "FAIL"],
        "warnings": [c for c in checks if c["status"] == "WARNING"],
        "safety_confirmation": {"mode": config.get("system", {}).get("mode"), "broker_connected": False, "allow_real_orders": config.get("system", {}).get("allow_real_orders"), "decision_agent_real": "decision_agent" in agents, "audit_agent_real": "audit_agent" in agents, "llm_enabled": bool(llm.get("enabled")), "real_orders_possible": False},
        "data_readiness_status": data_readiness["status"],
        "data_readiness": data_readiness,
        "controlled_universe": {"investable": INVESTABLE, "benchmarks": BENCHMARKS},
    }


def write_preflight_md(path: Path, report: dict[str, Any]) -> None:
    lines = ["# Preflight controlled pilot - Fase 11B", "", f"- Estado: **{report['status']}**", f"- Data readiness: **{report.get('data_readiness_status')}**", f"- Fecha: `{report['date']}`", "", "## Checks"]
    for c in report["checks"]:
        lines.append(f"- **{c['status']}** `{c['name']}`: {c['message']}")
    dr = report.get("data_readiness", {})
    lines += ["", "## Data readiness", f"- Explicación: {dr.get('human_explanation')}", f"- Cobertura invertible: `{dr.get('investable_coverage')}`", f"- Cobertura benchmarks: `{dr.get('benchmark_coverage')}`", f"- CSV manual: `{dr.get('manual_csv')}`", f"- Proveedor usado/probado: `{dr.get('provider_used_or_probed')}`", f"- provider_probe_errors: `{dr.get('provider_probe_errors')}`"]
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
    # Fase 12A: validar cobertura financiera efectiva leyendo el reporte real.
    effective_coverage_failures = []
    if pilot_report:
        cfg = run_demo.load_config()
        settings = run_demo.market_data_settings(cfg)
        coverage_checks = [
            ("price", "price_available", "minimum_price_coverage_pct"),
            ("fundamentals", "fundamentals_available", "minimum_fundamentals_coverage_pct"),
            ("ratios", "ratios_available", "minimum_ratios_coverage_pct"),
            ("metadata", "metadata_available", "minimum_metadata_coverage_pct"),
        ]
        warning_or_fail = data_policy(cfg).get("partial_coverage_below_threshold_status", "WARNING")
        for label, field, threshold_key in coverage_checks:
            threshold = settings.get(threshold_key)
            if threshold is None:
                continue
            observed = len(pilot_report.get(field, []) or []) / len(INVESTABLE) if INVESTABLE else 0.0
            if observed < float(threshold):
                msg = f"Cobertura {label} efectiva {observed:.0%} menor al mínimo {float(threshold):.0%}."
                if warning_or_fail == "FAIL":
                    effective_coverage_failures.append({"category": label, "observed": observed, "minimum": float(threshold), "message": msg})
                    errors.append(msg)
                else:
                    warnings.append(msg)

    if not started:
        status = "BLOCKED"
    elif effective_coverage_failures or errors:
        status = "FAIL"
    else:
        status = (pilot_report or {}).get("status", "PASS")
    outputs = []
    if pilot_report_path:
        outputs.extend([rel(pilot_report_path), rel(pilot_report_path.with_suffix(".md"))])
    return {
        "phase": "FASE_11B_CONTROLLED_PILOT_RUN_CONTROL",
        "status": status,
        "preflight_status": preflight["status"],
        "pilot_started": started,
        "pilot_returncode": pilot_proc.returncode if pilot_proc else None,
        "pilot_status": (pilot_report or {}).get("status") if pilot_report else None,
        "data_readiness": preflight.get("data_readiness"),
        "data_coverage": {"preflight_data_readiness_status": preflight.get("data_readiness_status"), "real_data_coverage_pct": (pilot_report or {}).get("real_data_coverage_pct"), "price_available": (pilot_report or {}).get("price_available"), "fundamentals_available": (pilot_report or {}).get("fundamentals_available"), "fundamentals_estimated_available": (pilot_report or {}).get("fundamentals_estimated_available"), "ratios_available": (pilot_report or {}).get("ratios_available"), "ratios_estimated_available": (pilot_report or {}).get("ratios_estimated_available"), "metadata_available": (pilot_report or {}).get("metadata_available"), "ready_for_scoring": (pilot_report or {}).get("ready_for_scoring"), "coverage_by_provider": (pilot_report or {}).get("coverage_by_provider"), "tickers_without_data": (pilot_report or {}).get("tickers_without_data"), "benchmarks_missing": (pilot_report or {}).get("benchmarks_missing"), "effective_coverage_failures": effective_coverage_failures},
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
    lines = ["# Run control report - Fase 11B", "", f"- Estado: **{report['status']}**", f"- Preflight: `{report['preflight_status']}`", f"- Piloto ejecutado: `{report['pilot_started']}`", f"- Estado piloto: `{report['pilot_status']}`", "", "## Data readiness", f"- Estado data readiness: `{report['data_coverage']['preflight_data_readiness_status']}`", f"- Detalle preflight: `{report.get('data_readiness')}`", "", "## Cobertura piloto", f"- Cobertura real: `{report['data_coverage']['real_data_coverage_pct']}`", f"- Tickers sin datos: `{report['data_coverage']['tickers_without_data']}`", f"- Benchmarks faltantes: `{report['data_coverage']['benchmarks_missing']}`", "", "## Seguridad", f"- Broker conectado: `{report['safety_confirmation']['broker_connected']}`", f"- allow_real_orders: `{report['safety_confirmation']['allow_real_orders']}`", f"- Config modificada automáticamente: `{report['safety_confirmation']['config_demo_yaml_modified']}`", "", "## Warnings y errores", f"- Warnings: `{report['warnings']}`", f"- Errores: `{report['errors']}`", "", "## Próxima acción", report["recommendation"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fase 11: controlled pilot con preflight operativo antes de ejecutar el piloto real DEMO/paper trading.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--allow-warning-run", action="store_true", help="Permite ejecutar si preflight queda WARNING.")
    parser.add_argument("--require-manual-csv", action="store_true", help="Exige CSV manual válido para esta corrida controlada.")
    parser.add_argument("--allow-real-llm", action="store_true", help="Autoriza explícitamente LLM real si config lo habilita; no habilita decision/audit reales.")
    parser.add_argument("--probe-data-provider", action="store_true", help="Ejecuta probe liviano del proveedor online solo sobre la muestra chica invertible.")
    args = parser.parse_args()

    run_id = f"{args.date}_controlled_phase11_{utc_now().strftime('%H%M%S%f')}"
    out_root = ROOT / "outputs" / "controlled_runs" / args.date / run_id
    out_root.mkdir(parents=True, exist_ok=True)
    config_hash_before = sha256_file(run_demo.CONFIG_PATH)
    config = run_demo.load_config()
    preflight = build_preflight(config, args.date, require_manual_csv=args.require_manual_csv, allow_real_llm=args.allow_real_llm, probe_data_provider=args.probe_data_provider)
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
