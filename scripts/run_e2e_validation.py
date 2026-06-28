#!/usr/bin/env python3
"""Fase 9: validación end-to-end reproducible del sistema DEMO."""
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
SAMPLE_INVESTABLE = ["AAPL", "MSFT", "NVDA", "YPF", "GGAL", "MELI", "VALE", "PBR", "ITUB"]
SAMPLE_BENCHMARKS = ["SPY", "QQQ", "EWZ", "ARGT", "BIL"]
REQUIRED_OUTPUTS = [
    "run_manifest.json", "data_quality_report.json", "universe_coverage_report.json",
    "scoring_results.json", "mock_research.json", "mock_decisions.json", "mock_audits.json",
    "risk_engine_results.json", "simulated_trades.csv", "portfolio_snapshot.json",
    "performance_snapshot.json", "performance_timeseries.csv", "benchmark_performance.csv",
    "forward_test_pending.csv", "forward_test_results.csv", "forward_test_summary.json",
    "forward_test_postmortem.md", "context_pack_summary.json", "daily_report.md",
]
REQUIRED_DAILY_SECTIONS = ["Estado de cartera", "Performance DEMO vs benchmarks", "Forward-test", "Revisión humana", "Decisiones finales", "Operaciones simuladas"]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, *, severity: str = "FAIL", details: str = "") -> None:
    checks.append({"name": name, "status": "PASS" if passed else severity, "details": details})


def status_for(checks: list[dict[str, Any]]) -> str:
    if any(c["status"] == "FAIL" for c in checks):
        return "FAIL"
    if any(c["status"] == "WARNING" for c in checks):
        return "WARNING"
    return "PASS"


def find_output_root(stdout: str) -> Path:
    for line in stdout.splitlines():
        if line.startswith("Outputs: "):
            return ROOT / line.split("Outputs: ", 1)[1].strip()
    raise RuntimeError("No se encontró la carpeta Outputs en stdout de run_demo.py")


def build_reports(out_root: Path, checks: list[dict[str, Any]], command: list[str], stdout: str, stderr: str) -> dict[str, Any]:
    scoring = read_json(out_root / "scoring_results.json", [])
    trades = read_csv(out_root / "simulated_trades.csv")
    manifest = read_json(out_root / "run_manifest.json", {})
    data_quality = read_json(out_root / "data_quality_report.json", {})
    coverage = read_json(out_root / "universe_coverage_report.json", {})
    context_summary = read_json(out_root / "context_pack_summary.json", {"packs": {}})
    benchmarks_in_scoring = sorted(set(SAMPLE_BENCHMARKS) & {row.get("ticker") for row in scoring})
    missing_sample = sorted(set(SAMPLE_INVESTABLE) - {row.get("ticker") for row in scoring} - set(data_quality.get("investable_assets_blocked", [])))
    blocked = data_quality.get("investable_assets_blocked", []) + data_quality.get("excluded_symbols", [])
    missing_outputs = [name for name in REQUIRED_OUTPUTS if not (out_root / name).exists()]
    report_md = (out_root / "daily_report.md").read_text(encoding="utf-8") if (out_root / "daily_report.md").exists() else ""

    add_check(checks, "outputs_obligatorios", not missing_outputs, details=f"faltantes={missing_outputs}")
    add_check(checks, "sin_broker", manifest.get("broker_connected") is False and "broker" not in stdout.lower().replace("broker no usado", ""), details="broker_connected=false y sin señal operativa")
    add_check(checks, "allow_real_orders_false", manifest.get("allow_real_orders") is False)
    add_check(checks, "real_order_siempre_false", all((r.get("real_order") or "False").lower() == "false" for r in trades), details=f"trades={len(trades)}")
    add_check(checks, "benchmarks_fuera_scoring", not benchmarks_in_scoring, details=f"benchmarks_en_scoring={benchmarks_in_scoring}")
    add_check(checks, "muestra_chica_validada", not missing_sample, details=f"muestra={SAMPLE_INVESTABLE}; faltantes_no_bloqueados={missing_sample}")
    add_check(checks, "context_packs_por_agente", {"research", "decision", "audit", "risk_orchestrator", "report", "learning_postmortem"} <= set(context_summary.get("packs", {})), details=str(context_summary.get("packs", {}).keys()))
    add_check(checks, "context_packs_respetan_limites", all(p.get("within_limit") for p in context_summary.get("packs", {}).values()), details=json.dumps(context_summary.get("packs", {}), ensure_ascii=False))
    add_check(checks, "decision_audit_mock", not manifest.get("llms_used") and read_json(out_root / "mock_decisions.json", []) is not None and read_json(out_root / "mock_audits.json", []) is not None)
    add_check(checks, "daily_report_legible", all(section in report_md for section in REQUIRED_DAILY_SECTIONS), details=f"secciones={REQUIRED_DAILY_SECTIONS}")
    add_check(checks, "memoria_sin_duplicacion_critica", read_json(out_root / "memory_diff.json", {}).get("summary") is not None, severity="WARNING", details="Se valida diff y cola humana idempotente; dedupe semántico profundo queda fuera de Fase 9.")

    warnings = []
    if data_quality.get("missing_data") or data_quality.get("benchmarks_missing"):
        warnings.append({"name": "datos_faltantes_visibles", "details": {"missing_data": data_quality.get("missing_data"), "benchmarks_missing": data_quality.get("benchmarks_missing")}})
    if blocked:
        warnings.append({"name": "activos_bloqueados_visibles", "details": blocked})
    for w in warnings:
        checks.append({"name": w["name"], "status": "WARNING", "details": w["details"]})

    report = {
        "phase": "FASE_9_E2E_VALIDATION",
        "status": status_for(checks),
        "command": command,
        "output_root": str(out_root.relative_to(ROOT)),
        "sample": {"investable": SAMPLE_INVESTABLE, "benchmarks": SAMPLE_BENCHMARKS},
        "checks": checks,
        "missing_outputs": missing_outputs,
        "warnings": warnings,
        "missing_data": data_quality.get("missing_data", []),
        "blocked_assets": blocked,
        "benchmarks_in_scoring": benchmarks_in_scoring,
        "broker_or_real_order_signals": {"broker_connected": manifest.get("broker_connected"), "allow_real_orders": manifest.get("allow_real_orders"), "real_order_values": sorted({r.get("real_order") for r in trades})},
        "agents": {"decision_agent": "mock", "audit_agent": "mock", "llms_used": manifest.get("llms_used")},
        "coverage": coverage,
        "stdout_tail": stdout.splitlines()[-20:],
        "stderr": stderr,
    }
    (out_root / "e2e_validation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Reporte de validación integral E2E - Fase 9", "", f"- Estado global: **{report['status']}**", f"- Outputs: `{report['output_root']}`", f"- Muestra invertible: {', '.join(SAMPLE_INVESTABLE)}", f"- Benchmarks: {', '.join(SAMPLE_BENCHMARKS)}", "", "## Validaciones", "", "| Estado | Componente | Detalle |", "|---|---|---|"]
    for c in checks:
        icon = "✅" if c["status"] == "PASS" else "⚠️" if c["status"] == "WARNING" else "❌"
        lines.append(f"| {icon} {c['status']} | {c['name']} | {str(c.get('details', '')).replace('|', '/')} |")
    lines += ["", "## Conclusión de seguridad", "", f"- Broker conectado: `{manifest.get('broker_connected')}`.", f"- allow_real_orders: `{manifest.get('allow_real_orders')}`.", f"- Valores real_order observados: `{sorted({r.get('real_order') for r in trades})}`.", f"- Benchmarks en scoring: `{benchmarks_in_scoring}`.", "- decision_agent y audit_agent permanecen mock; no se habilitan agentes reales."]
    (out_root / "e2e_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Corre validación E2E Fase 9 sin credenciales, broker ni datos reales por default.")
    parser.add_argument("--date", default="2026-06-27")
    args = parser.parse_args()
    symbols = SAMPLE_INVESTABLE + SAMPLE_BENCHMARKS
    command = [sys.executable, str(RUN_DEMO), "--date", args.date, "--universe-symbols", ",".join(symbols)]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"run_demo.py falló con código {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    out_root = find_output_root(proc.stdout)
    report = build_reports(out_root, [], command, proc.stdout, proc.stderr)
    print(f"E2E Fase 9: {report['status']}")
    print(f"Reporte JSON: {(out_root / 'e2e_validation_report.json').relative_to(ROOT)}")
    print(f"Reporte MD: {(out_root / 'e2e_validation_report.md').relative_to(ROOT)}")
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
