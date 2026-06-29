#!/usr/bin/env python3
"""Fase 10: piloto controlado con datos reales al cierre, siempre paper trading."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN_DEMO = ROOT / "scripts" / "run_demo.py"
INVESTABLE = ["AAPL", "MSFT", "NVDA", "YPF", "GGAL", "MELI", "VALE", "PBR", "ITUB"]
BENCHMARKS = ["SPY", "QQQ", "EWZ", "ARGT", "BIL"]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def find_output_root(stdout: str) -> Path:
    for line in stdout.splitlines():
        if line.startswith("Outputs: "):
            return ROOT / line.split("Outputs: ", 1)[1].strip()
    raise RuntimeError("No se encontró Outputs en stdout de run_demo.py")


def build_pilot_report(out_root: Path, command: list[str], stdout: str, stderr: str) -> dict[str, Any]:
    scoring = read_json(out_root / "scoring_results.json", [])
    dq = read_json(out_root / "data_quality_report.json", {})
    manifest = read_json(out_root / "run_manifest.json", {})
    trades = read_csv(out_root / "simulated_trades.csv")
    raw = read_json(out_root / "snapshots" / "raw_market_data.json", {})
    normalized = read_json(out_root / "snapshots" / "normalized_market_data.json", [])
    scored = {row.get("ticker") for row in scoring}
    normalized_by_ticker = {row.get("ticker"): row for row in normalized}
    def category_available(row, category, *, include_estimated: bool = False):
        data = row.get(category) or {}
        if not data:
            return False
        for field in data.values():
            if not isinstance(field, dict):
                continue
            if field.get("is_missing"):
                continue
            if field.get("is_estimated") and not include_estimated:
                continue
            return True
        return False

    price_available = sorted(t for t in INVESTABLE if t in normalized_by_ticker and category_available(normalized_by_ticker[t], "price_data"))
    fundamentals_available = sorted(t for t in INVESTABLE if t in normalized_by_ticker and category_available(normalized_by_ticker[t], "fundamentals_data"))
    fundamentals_estimated_available = sorted(t for t in INVESTABLE if t in normalized_by_ticker and category_available(normalized_by_ticker[t], "fundamentals_data", include_estimated=True) and t not in fundamentals_available)
    ratios_available = sorted(t for t in INVESTABLE if t in normalized_by_ticker and category_available(normalized_by_ticker[t], "ratios_data"))
    ratios_estimated_available = sorted(t for t in INVESTABLE if t in normalized_by_ticker and category_available(normalized_by_ticker[t], "ratios_data", include_estimated=True) and t not in ratios_available)
    metadata_available = sorted(t for t in INVESTABLE if t in normalized_by_ticker and category_available(normalized_by_ticker[t], "metadata_data"))
    ready_for_scoring = sorted(t for t in INVESTABLE if t in scored)
    available = price_available
    provider_by_ticker = {t: normalized_by_ticker[t].get("provider") for t in available if t in normalized_by_ticker}
    coverage_by_provider = {}
    for ticker, provider in provider_by_ticker.items():
        coverage_by_provider.setdefault(provider or "unknown", []).append(ticker)
    coverage_by_provider = {k: sorted(v) for k, v in sorted(coverage_by_provider.items())}
    coverage_pct = round(len(available) / len(INVESTABLE), 4) if INVESTABLE else 0.0
    minimum_coverage_pct = float(raw.get("minimum_real_data_coverage_pct") or 0.60)
    blocked = sorted(set(dq.get("investable_assets_blocked", [])))
    missing = sorted(t for t in INVESTABLE if t not in available)
    bench_avail = sorted(set(dq.get("benchmarks_available", [])) & set(BENCHMARKS))
    bench_missing = sorted(set(BENCHMARKS) - set(bench_avail))
    benchmarks_in_scoring = sorted(set(BENCHMARKS) & scored)
    real_values = sorted({str(r.get("real_order")).lower() for r in trades})
    warnings = []
    if coverage_pct < minimum_coverage_pct and available:
        warnings.append({"name": "minimum_real_data_coverage_not_met", "coverage_pct": coverage_pct, "minimum_real_data_coverage_pct": minimum_coverage_pct})
    if missing:
        warnings.append({"name": "investable_data_missing_or_insufficient", "tickers": missing})
    if bench_missing:
        warnings.append({"name": "benchmark_data_missing", "tickers": bench_missing})
    if blocked:
        warnings.append({"name": "assets_blocked_before_scoring", "tickers": blocked})
    errors = []
    if not available and raw.get("fail_if_no_real_prices", True):
        errors.append("no hay datos reales para ningún activo invertible")
    if manifest.get("broker_connected") is not False:
        errors.append("broker_connected debe ser false")
    if manifest.get("allow_real_orders") is not False:
        errors.append("allow_real_orders debe ser false")
    if any(v not in {"false", "none"} for v in real_values):
        errors.append(f"real_order no es siempre false: {real_values}")
    if benchmarks_in_scoring:
        errors.append(f"benchmarks entraron al scoring: {benchmarks_in_scoring}")
    if manifest.get("llms_used"):
        errors.append("LLM real habilitado; Fase 10 requiere decision/audit mock y research mock por default")
    flow_outputs = ["universe_coverage_report.json", "data_quality_report.json", "scoring_results.json", "mock_research.json", "mock_decisions.json", "mock_audits.json", "risk_engine_results.json", "simulated_trades.csv", "portfolio_snapshot.json", "performance_snapshot.json", "forward_test_summary.json", "forward_test_postmortem.md", "proposed_methodology_changes.json", "context_pack_summary.json", "daily_report.md"]
    missing_outputs = [name for name in flow_outputs if not (out_root / name).exists()]
    if missing_outputs:
        errors.append(f"outputs faltantes: {missing_outputs}")
    provider_health = read_json(out_root / "provider_health_report.json", {})
    report = {
        "phase": "FASE_10_REAL_DATA_PILOT",
        "status": "FAIL" if errors else "WARNING" if warnings else "PASS",
        "command": command,
        "output_root": str(out_root.relative_to(ROOT)),
        "provider": raw.get("provider"),
        "provider_priority": raw.get("provider_priority", []),
        "manual_csv_used": bool(raw.get("manual_csv_used")),
        "coverage_by_provider": coverage_by_provider,
        "provider_by_ticker": provider_by_ticker,
        "real_data_coverage_pct": coverage_pct,
        "minimum_real_data_coverage_pct": minimum_coverage_pct,
        "requested_tickers": INVESTABLE,
        "requested_benchmarks": BENCHMARKS,
        "tickers_with_real_data_available": available,
        "price_available": price_available,
        "fundamentals_available": fundamentals_available,
        "fundamentals_estimated_available": fundamentals_estimated_available,
        "ratios_available": ratios_available,
        "ratios_estimated_available": ratios_estimated_available,
        "metadata_available": metadata_available,
        "ready_for_scoring": ready_for_scoring,
        "tickers_without_data": missing,
        "tickers_blocked": blocked,
        "benchmarks_available": bench_avail,
        "benchmarks_missing": bench_missing,
        "assets_sent_to_scoring": sorted(scored),
        "assets_excluded_from_scoring": sorted((set(INVESTABLE) | set(BENCHMARKS)) - scored),
        "completed_full_flow": not missing_outputs,
        "warnings": warnings,
        "errors": errors,
        "safety": {"broker_connected": manifest.get("broker_connected"), "allow_real_orders": manifest.get("allow_real_orders"), "real_order_values": real_values, "decision_agent": "mock", "audit_agent": "mock", "llms_used": manifest.get("llms_used")},
        "benchmarks_in_scoring": benchmarks_in_scoring,
        "provider_health": provider_health,
        "freshness": dq.get("freshness", {}),
        "stale_data": dq.get("stale_data", []),
        "provider_errors": dq.get("provider_errors", []),
        "missing_data_detail": dq.get("missing_data", []),
        "stdout_tail": stdout.splitlines()[-20:],
        "stderr": stderr,
    }
    (out_root / "real_data_pilot_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Reporte piloto con datos reales - Fase 10", "", f"- Estado: **{report['status']}**", f"- Proveedor: `{report['provider']}`", f"- Prioridad de proveedores: `{report['provider_priority']}`", f"- CSV manual usado: `{report['manual_csv_used']}`", f"- Outputs: `{report['output_root']}`", "", "## Cobertura", f"- Cobertura total invertible: {coverage_pct:.0%} (mínimo {minimum_coverage_pct:.0%})", f"- Cobertura por proveedor: `{coverage_by_provider}`", f"- Tickers solicitados: {', '.join(INVESTABLE)}", f"- Con datos reales disponibles: {', '.join(available) or 'ninguno'}", f"- Sin datos o insuficientes: {', '.join(missing) or 'ninguno'}", f"- Bloqueados: {', '.join(blocked) or 'ninguno'}", f"- Benchmarks disponibles: {', '.join(bench_avail) or 'ninguno'}", f"- Benchmarks faltantes: {', '.join(bench_missing) or 'ninguno'}", "", "## Scoring", f"- Enviados a scoring: {', '.join(sorted(scored)) or 'ninguno'}", f"- Excluidos del scoring: {', '.join(report['assets_excluded_from_scoring']) or 'ninguno'}", f"- Benchmarks en scoring: {benchmarks_in_scoring}", "", "## Seguridad", f"- Broker conectado: `{manifest.get('broker_connected')}`", f"- allow_real_orders: `{manifest.get('allow_real_orders')}`", f"- real_order observado: `{real_values}`", "- decision_agent y audit_agent permanecen mock.", "", "## Provider health y freshness", f"- Provider health: `{provider_health.get('overall_status')}`", f"- Datos stale: `{dq.get('stale_data', [])}`", "", "## Warnings y errores", f"- Warnings: `{warnings}`", f"- Errores: `{errors}`"]
    (out_root / "real_data_pilot_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta el piloto Fase 10 con datos reales solo si se activa explícitamente.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--activate-real-data-pilot", action="store_true", help="Confirmación explícita requerida para usar stooq_csv.")
    args = parser.parse_args()
    if not args.activate_real_data_pilot:
        raise SystemExit("Debe pasar --activate-real-data-pilot para ejecutar el piloto real; la demo fixture sigue siendo default.")
    symbols = INVESTABLE + BENCHMARKS
    command = [sys.executable, str(RUN_DEMO), "--date", args.date, "--universe-symbols", ",".join(symbols), "--real-data-pilot"]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"run_demo.py falló ({proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    out_root = find_output_root(proc.stdout)
    report = build_pilot_report(out_root, command, proc.stdout, proc.stderr)
    print(f"Piloto real Fase 10: {report['status']}")
    print(f"Reporte JSON: {(out_root / 'real_data_pilot_report.json').relative_to(ROOT)}")
    print(f"Reporte MD: {(out_root / 'real_data_pilot_report.md').relative_to(ROOT)}")
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
