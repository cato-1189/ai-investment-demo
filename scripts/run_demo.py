#!/usr/bin/env python3
"""Fase 1: DEMO end-to-end sin LLM, APIs externas, datos reales ni broker."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any



ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config_demo.yaml"
FIXTURE_PATH = ROOT / "data" / "fixtures" / "demo_assets.json"
TEMPLATE_DIR = ROOT / "memory_templates"

PROMPT_KEYS = [
    "data_scoring_agent",
    "research_agent",
    "decision_agent",
    "audit_agent",
    "orchestrator_risk_agent",
    "report_agent",
    "learning_agent",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if any(char in value for char in [".", "e", "E"]):
            return float(value)
        return int(value)
    except ValueError:
        return value


def strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:idx].rstrip()
    return line.rstrip()


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        raw = strip_inline_comment(lines[idx])
        idx += 1
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"Lista YAML inesperada cerca de: {stripped}")
            parent.append(parse_scalar(stripped[2:]))
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            raise ValueError(f"Línea YAML inválida: {stripped}")
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = parse_scalar(value)
            continue
        # Decide container from next meaningful line.
        lookahead = idx
        next_container: Any = {}
        while lookahead < len(lines):
            probe = strip_inline_comment(lines[lookahead])
            if probe.strip():
                probe_indent = len(probe) - len(probe.lstrip(" "))
                if probe_indent > indent and probe.strip().startswith("- "):
                    next_container = []
                break
            lookahead += 1
        parent[key] = next_container
        stack.append((indent, next_container))
    return root


def load_config() -> dict[str, Any]:
    return parse_simple_yaml(CONFIG_PATH.read_text(encoding="utf-8"))


def validate_demo_safety(config: dict[str, Any]) -> list[str]:
    system = config.get("system", {})
    errors = []
    if system.get("mode") != "DEMO_PAPER_TRADING":
        errors.append("system.mode debe ser DEMO_PAPER_TRADING")
    if system.get("allow_real_orders") is not False:
        errors.append("system.allow_real_orders debe ser false")
    # Fase 1 no permite integraciones operativas. La config actual puede mencionar
    # allow_real_orders como control de seguridad, pero debe quedar en false.
    serialized = json.dumps(config, sort_keys=True).lower()
    risky_terms = [term for term in ["live_trading", "broker_provider", "broker_account"] if term in serialized]
    if risky_terms:
        errors.append(f"config contiene términos de integración operativa no permitidos: {risky_terms}")
    return errors


def load_prompts(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    prompts = {}
    for key in PROMPT_KEYS:
        agent_cfg = config["agents"][key]
        prompt_path = ROOT / agent_cfg["prompt_file"]
        content = prompt_path.read_text(encoding="utf-8")
        prompts[key] = {
            "prompt_file": str(prompt_path.relative_to(ROOT)),
            "chars": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "loaded": True,
        }
    return prompts


def ensure_memory(config: dict[str, Any], run_id: str, today: str) -> dict[str, str]:
    created_or_existing = {}
    memory_files = config["context_management"]["memory_files"]
    for key, rel in memory_files.items():
        path = ROOT / rel
        if not path.exists():
            template = TEMPLATE_DIR / Path(rel).name
            path.parent.mkdir(parents=True, exist_ok=True)
            if template.exists():
                shutil.copyfile(template, path)
            elif path.suffix == ".jsonl":
                path.write_text("", encoding="utf-8")
            elif path.suffix == ".csv":
                path.write_text("date,run_id,notes\n", encoding="utf-8")
            elif path.suffix == ".json":
                write_json(path, {})
            else:
                path.write_text(f"# {key}\n\nCreado por {run_id} el {today}.\n", encoding="utf-8")
        created_or_existing[key] = rel
    return created_or_existing


def score_asset(asset: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    metrics = asset["metrics"]
    valuation = max(0, min(100, 100 - metrics["pe_ttm"] * 4 + metrics["fcf_yield"] * 250))
    business_quality = max(0, min(100, 50 + metrics["roe"] * 200))
    financial_strength = max(0, min(100, 100 - metrics["net_debt_ebitda"] * 18))
    growth = max(0, min(100, 50 + metrics["revenue_growth"] * 250))
    momentum_reversal = max(0, min(100, 55 + abs(metrics["drawdown_52w"]) * 40 + metrics["momentum_6m"] * 40))
    risk = max(0, min(100, 100 - max(0, metrics["net_debt_ebitda"] - 2) * 25))
    data_quality_map = {"HIGH": 100, "MEDIUM": 70, "LOW": 30}
    data_quality = data_quality_map.get(asset["data_quality"], 0)
    scores = {
        "valuation": round(valuation, 2),
        "business_quality": round(business_quality, 2),
        "financial_strength": round(financial_strength, 2),
        "growth": round(growth, 2),
        "momentum_reversal": round(momentum_reversal, 2),
        "risk": round(risk, 2),
        "data_quality": round(data_quality, 2),
    }
    total = sum(scores[key] * weights[key] for key in weights)
    alerts = []
    if asset["data_quality"] == "LOW":
        alerts.append("DATA_QUALITY_LOW")
    if asset["avg_volume_usd"] < 1_000_000:
        alerts.append("LOW_LIQUIDITY")
    if metrics["net_debt_ebitda"] > 4:
        alerts.append("EXTREME_DEBT")
    if metrics["fcf_yield"] < 0:
        alerts.append("NEGATIVE_FCF")
    if metrics["pe_ttm"] < 6 and (metrics["revenue_growth"] < 0 or metrics["fcf_yield"] < 0):
        alerts.append("POSSIBLE_VALUE_TRAP")
    return {**asset, "scores": scores, "total_score": round(total, 2), "alerts": alerts}


def mock_decision(asset: dict[str, Any], config: dict[str, Any], today: str) -> dict[str, Any]:
    max_trade = config["portfolio_rules"]["max_single_trade_weight"]
    if asset["data_quality"] == "LOW":
        action, weight, ratio = "NEED_MORE_DATA", 0.0, None
    elif asset["total_score"] >= config["candidate_filters"]["min_total_score"] + 10:
        action, weight, ratio = "BUY_DEMO", max_trade * 1.5, 2.6
    elif asset["total_score"] >= config["candidate_filters"]["min_total_score"]:
        action, weight, ratio = "WATCHLIST", 0.0, 1.8
    else:
        action, weight, ratio = "DO_NOT_BUY", 0.0, 1.2
    return {
        "as_of_date": today,
        "ticker": asset["ticker"],
        "company": asset["company"],
        "decision": action,
        "conviction": "MEDIUM" if action == "BUY_DEMO" else "LOW",
        "suggested_weight": round(weight, 4),
        "max_acceptable_weight": config["portfolio_rules"]["max_weight_per_position"],
        "time_horizon_months": 12,
        "base_case_upside": 0.30 if action == "BUY_DEMO" else None,
        "bear_case_downside": -0.12 if action == "BUY_DEMO" else None,
        "upside_downside_ratio": ratio,
        "main_thesis": "Decisión mock Fase 1 basada solo en score fixture, sin LLM ni datos reales.",
        "main_catalyst": "Catalizador mock para validar flujo end-to-end.",
        "reason_for_decision": f"Score determinístico {asset['total_score']} y calidad {asset['data_quality']}.",
        "key_risks": asset["alerts"],
        "invalidation_triggers": ["Datos reales contradicen fixture", "Auditoría o reglas bloquean"],
        "monitoring_conditions": ["Revisar al implementar datos reales en una fase posterior"],
        "data_quality": asset["data_quality"],
        "research_quality": "LOW",
        "requires_audit": True,
    }


def mock_audit(asset: dict[str, Any], decision: dict[str, Any], config: dict[str, Any], today: str) -> dict[str, Any]:
    result = "APPROVED"
    objections = ["Auditoría mock: no usa LLM ni datos reales en Fase 1."]
    value_trap = "LOW"
    if "POSSIBLE_VALUE_TRAP" in asset["alerts"]:
        result, value_trap = "BLOCKED", "HIGH"
    elif decision["decision"] == "NEED_MORE_DATA" or asset["data_quality"] == "LOW":
        result = "NEED_MORE_DATA"
    elif decision["suggested_weight"] > config["portfolio_rules"]["max_single_trade_weight"]:
        result = "APPROVED_WITH_REDUCED_WEIGHT"
        objections.append("Peso mock excede max_single_trade_weight; debe reducirse.")
    return {
        "as_of_date": today,
        "ticker": asset["ticker"],
        "company": asset["company"],
        "decision_agent_recommendation": decision["decision"],
        "audit_result": result,
        "objection_severity": "HIGH" if result in {"BLOCKED", "NEED_MORE_DATA"} else "MEDIUM",
        "original_weight": decision["suggested_weight"],
        "auditor_max_weight": min(decision["suggested_weight"], config["portfolio_rules"]["max_single_trade_weight"]),
        "main_objections": objections,
        "missing_evidence": ["Datos reales", "Research cualitativo real", "Validación de proveedor externo"],
        "contradictory_evidence": [],
        "value_trap_risk": value_trap,
        "permanent_capital_loss_risk": "MEDIUM",
        "catalyst_quality": "LOW",
        "conditions_to_approve": ["Integrar datos y LLM reales en fases futuras"],
        "conditions_to_block": ["Reglas duras incumplidas", "Auditoría bloquea o pide más datos"],
        "final_audit_comment": "Auditoría mock estructurada para validar motor de riesgo.",
    }


def apply_risk(asset: dict[str, Any], decision: dict[str, Any], audit: dict[str, Any], portfolio: dict[str, Any], config: dict[str, Any], today: str) -> dict[str, Any]:
    rules = config["risk_rules"]
    prules = config["portfolio_rules"]
    triggered = []
    final = "WATCHLIST"
    weight = 0.0
    reason_block = None
    if decision["decision"] != "BUY_DEMO":
        triggered.append(f"decision_agent_action_{decision['decision']}")
    if rules["block_if_data_quality_low"] and asset["data_quality"] == "LOW":
        triggered.append("block_if_data_quality_low")
    if rules["block_if_auditor_blocks"] and audit["audit_result"] == "BLOCKED":
        triggered.append("block_if_auditor_blocks")
    if rules["block_if_auditor_needs_more_data"] and audit["audit_result"] == "NEED_MORE_DATA":
        triggered.append("block_if_auditor_needs_more_data")
    if rules["block_if_value_trap_risk_high"] and audit["value_trap_risk"] == "HIGH":
        triggered.append("block_if_value_trap_risk_high")
    if asset["avg_volume_usd"] < rules["min_avg_volume_usd"]:
        triggered.append("min_avg_volume_usd")
    if asset["metrics"]["net_debt_ebitda"] > rules["max_net_debt_ebitda"]:
        triggered.append("max_net_debt_ebitda")
    ratio = decision.get("upside_downside_ratio")
    if decision["decision"] == "BUY_DEMO" and (ratio is None or ratio < rules["min_upside_downside_ratio"]):
        triggered.append("min_upside_downside_ratio")

    hard_blocks = [r for r in triggered if r.startswith("block_") or r in {"min_avg_volume_usd", "max_net_debt_ebitda", "min_upside_downside_ratio"}]
    if decision["decision"] == "BUY_DEMO" and not hard_blocks:
        final = "EXECUTE_BUY_DEMO"
        weight = min(decision["suggested_weight"], audit["auditor_max_weight"], prules["max_single_trade_weight"], prules["max_weight_per_position"])
        if weight < decision["suggested_weight"]:
            triggered.append("position_weight_adjusted_by_risk_engine")
    elif hard_blocks:
        final = "BLOCKED"
        reason_block = ", ".join(hard_blocks)
    elif decision["decision"] == "NEED_MORE_DATA":
        final = "NEED_MORE_DATA"
    allocated = round(portfolio["portfolio_value_usd"] * weight, 2)
    return {
        "as_of_date": today,
        "ticker": asset["ticker"],
        "company": asset["company"],
        "decision_agent_action": decision["decision"],
        "audit_agent_result": audit["audit_result"],
        "risk_rules_triggered": triggered,
        "final_decision": final,
        "final_weight": round(weight, 4),
        "allocated_amount_usd": allocated,
        "position_size_change_usd": allocated,
        "reason_for_adjustment": "Peso ajustado por límites configurados" if "position_weight_adjusted_by_risk_engine" in triggered else "Sin ajuste de peso",
        "reason_for_blocking": reason_block,
        "monitoring_conditions": decision["monitoring_conditions"],
        "next_review_date": (dt.date.fromisoformat(today) + dt.timedelta(days=30)).isoformat(),
        "human_override_available": config["system"]["human_override_enabled"],
    }


def load_portfolio(config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / config["context_management"]["memory_files"]["portfolio_state"]
    portfolio = read_json(path)
    if not portfolio:
        portfolio = read_json(TEMPLATE_DIR / "portfolio_state.json", {})
    initial = config["system"]["initial_capital_usd"]
    portfolio.setdefault("cash_usd", initial)
    portfolio.setdefault("positions", [])
    portfolio["portfolio_value_usd"] = portfolio.get("cash_usd", initial) + sum(p.get("market_value_usd", 0) for p in portfolio.get("positions", []))
    return portfolio


def update_portfolio(portfolio: dict[str, Any], finals: list[dict[str, Any]], assets_by_ticker: dict[str, dict[str, Any]], config: dict[str, Any], run_id: str, today: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trades = []
    positions = {p["ticker"]: p for p in portfolio.get("positions", [])}
    cash = float(portfolio.get("cash_usd", config["system"]["initial_capital_usd"]))
    for final in finals:
        if final["final_decision"] != "EXECUTE_BUY_DEMO" or final["allocated_amount_usd"] <= 0:
            continue
        asset = assets_by_ticker[final["ticker"]]
        amount = min(final["allocated_amount_usd"], max(0.0, cash - config["portfolio_rules"]["min_cash_weight"] * portfolio["portfolio_value_usd"]))
        if amount <= 0:
            continue
        shares = amount / asset["price_close"]
        cash -= amount
        current = positions.get(asset["ticker"], {"ticker": asset["ticker"], "company": asset["company"], "country": asset["country"], "sector": asset["sector"], "shares": 0.0, "market_value_usd": 0.0})
        current["shares"] = round(current.get("shares", 0.0) + shares, 6)
        current["price_close"] = asset["price_close"]
        current["market_value_usd"] = round(current.get("market_value_usd", 0.0) + amount, 2)
        positions[asset["ticker"]] = current
        trades.append({"run_id": run_id, "date": today, "ticker": asset["ticker"], "action": "BUY_DEMO", "amount_usd": round(amount, 2), "price": asset["price_close"], "shares": round(shares, 6), "real_order": False})
    total = cash + sum(p["market_value_usd"] for p in positions.values())
    updated_positions = []
    for p in positions.values():
        p["weight"] = round(p["market_value_usd"] / total, 4) if total else 0
        updated_positions.append(p)
    portfolio.update({"last_update": today, "run_id": run_id, "mode": config["system"]["mode"], "base_currency": config["system"]["base_currency"], "initial_capital_usd": config["system"]["initial_capital_usd"], "cash_usd": round(cash, 2), "portfolio_value_usd": round(total, 2), "positions": updated_positions, "portfolio_metrics": {"number_of_positions": len(updated_positions), "cash_weight": round(cash / total, 4) if total else 0}, "open_risks": ["Fase 1 usa fixtures/mock; no usar para inversión real"], "human_overrides_active": []})
    return portfolio, trades


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def generate_report(path: Path, run_id: str, today: str, config: dict[str, Any], scored: list[dict[str, Any]], finals: list[dict[str, Any]], trades: list[dict[str, Any]], portfolio: dict[str, Any]) -> None:
    lines = [
        f"# Reporte diario DEMO - {today}",
        "",
        f"- Run ID: `{run_id}`",
        f"- Modo confirmado: `{config['system']['mode']}`",
        f"- Órdenes reales habilitadas: `{config['system']['allow_real_orders']}`",
        "- Fuente de datos: fixture/mock local, sin APIs externas.",
        "- LLMs: no utilizados en Fase 1.",
        "",
        "## Estado de cartera",
        f"- Valor cartera DEMO: USD {portfolio['portfolio_value_usd']:.2f}",
        f"- Cash: USD {portfolio['cash_usd']:.2f}",
        f"- Posiciones: {len(portfolio['positions'])}",
        "",
        "## Decisiones finales",
        "| Ticker | Score | Decisión final | Peso final | Reglas disparadas |",
        "|---|---:|---|---:|---|",
    ]
    by_score = {a["ticker"]: a for a in scored}
    for final in finals:
        lines.append(f"| {final['ticker']} | {by_score[final['ticker']]['total_score']} | {final['final_decision']} | {final['final_weight']:.2%} | {', '.join(final['risk_rules_triggered']) or '-'} |")
    lines += ["", "## Operaciones simuladas", "| Ticker | Acción | Monto USD | Orden real |", "|---|---|---:|---|"]
    if trades:
        for trade in trades:
            lines.append(f"| {trade['ticker']} | {trade['action']} | {trade['amount_usd']:.2f} | {trade['real_order']} |")
    else:
        lines.append("| - | Sin operaciones ejecutadas en paper | 0.00 | false |")
    lines += ["", "## Limitaciones Fase 1", "- Datos simulados; no representan precios ni fundamentals reales.", "- Decisiones y auditorías mock; no se usaron OpenAI, Claude ni Gemini.", "- No hay broker ni posibilidad de órdenes reales."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta Fase 1 DEMO paper trading sin LLM/APIs/broker.")
    parser.add_argument("--date", default=utc_now().date().isoformat(), help="Fecha de corrida YYYY-MM-DD")
    args = parser.parse_args()
    today = args.date
    stamp = utc_now().strftime("%H%M%S")
    run_id = f"{today}_demo_phase1_{stamp}"

    config = load_config()
    errors = validate_demo_safety(config)
    if errors:
        raise SystemExit("Validación DEMO falló: " + "; ".join(errors))
    prompts = load_prompts(config)

    out_root = ROOT / config["outputs"]["daily_output_folder"] / today / run_id
    out_root.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / config["outputs"]["logs_folder"] / f"{run_id}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(log_path, {"event": "run_started", "run_id": run_id, "mode": config["system"]["mode"], "allow_real_orders": config["system"]["allow_real_orders"]})

    memory = ensure_memory(config, run_id, today)
    portfolio = load_portfolio(config)
    assets = read_json(FIXTURE_PATH, [])
    scored = sorted([score_asset(asset, config["scoring_weights"]) for asset in assets], key=lambda row: row["total_score"], reverse=True)
    candidates = scored[: config["candidate_filters"]["max_candidates_for_decision"]]
    decisions = [mock_decision(asset, config, today) for asset in candidates]
    audits = [mock_audit(asset, decision, config, today) for asset, decision in zip(candidates, decisions)]
    finals = [apply_risk(asset, decision, audit, portfolio, config, today) for asset, decision, audit in zip(candidates, decisions, audits)]
    assets_by_ticker = {a["ticker"]: a for a in scored}
    portfolio, trades = update_portfolio(portfolio, finals, assets_by_ticker, config, run_id, today)

    write_json(out_root / "run_manifest.json", {"run_id": run_id, "date": today, "phase": "FASE_1", "mode": config["system"]["mode"], "allow_real_orders": config["system"]["allow_real_orders"], "prompts_loaded": prompts, "memory_files": memory})
    write_json(out_root / "scoring_results.json", scored)
    write_csv(out_root / "scoring_results.csv", scored, ["ticker", "company", "country", "sector", "data_quality", "total_score", "alerts"])
    write_json(out_root / "mock_decisions.json", decisions)
    write_json(out_root / "mock_audits.json", audits)
    write_json(out_root / "risk_engine_results.json", finals)
    write_csv(out_root / "simulated_trades.csv", trades, ["run_id", "date", "ticker", "action", "amount_usd", "price", "shares", "real_order"])
    write_json(out_root / "portfolio_snapshot.json", portfolio)
    generate_report(out_root / "daily_report.md", run_id, today, config, scored, finals, trades, portfolio)

    portfolio_path = ROOT / config["context_management"]["memory_files"]["portfolio_state"]
    write_json(portfolio_path, portfolio)
    for decision, audit, final in zip(decisions, audits, finals):
        append_jsonl(ROOT / config["context_management"]["memory_files"]["decision_ledger"], {"run_id": run_id, "date": today, "ticker": decision["ticker"], "decision_agent_action": decision["decision"], "audit_result": audit["audit_result"], "final_action": final["final_decision"], "reason": final["reason_for_blocking"] or final["reason_for_adjustment"]})
        append_jsonl(ROOT / config["context_management"]["memory_files"]["audit_ledger"], {"run_id": run_id, "date": today, "ticker": audit["ticker"], "audit_result": audit["audit_result"], "main_objections": audit["main_objections"], "value_trap_risk": audit["value_trap_risk"]})
    append_jsonl(log_path, {"event": "run_finished", "run_id": run_id, "outputs": str(out_root.relative_to(ROOT)), "trades": len(trades), "positions": len(portfolio["positions"])})

    print(f"DEMO CONFIRMADA: mode={config['system']['mode']} allow_real_orders={config['system']['allow_real_orders']}")
    print("No se usaron APIs externas, LLMs ni broker. Todas las operaciones son simuladas.")
    print(f"Run ID: {run_id}")
    print(f"Outputs: {out_root.relative_to(ROOT)}")
    print(f"Log: {log_path.relative_to(ROOT)}")
    print(f"Reporte: {(out_root / 'daily_report.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
