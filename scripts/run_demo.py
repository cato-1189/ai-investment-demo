#!/usr/bin/env python3
"""Fase 8: revisión humana y versionado metodológico DEMO."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from schema_validation import SchemaValidationError, assert_valid, load_schema, validate_schema


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config_demo.yaml"
FIXTURE_PATH = ROOT / "data" / "fixtures" / "demo_assets.json"
TEMPLATE_DIR = ROOT / "memory_templates"
SCHEMA_DIR = ROOT / "schemas"

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


def estimate_tokens(value: Any) -> int:
    """Estimación conservadora sin tokenizador externo: 1 token ~= 4 caracteres."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return max(1, (len(value) + 3) // 4)


def config_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def append_markdown_section(path: Path, title: str, bullets: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_text(path)
    lines = [existing.rstrip(), "", f"## {title}", ""] if existing.strip() else [f"# {path.stem.replace('_', ' ').title()}", "", f"## {title}", ""]
    lines.extend(f"- {bullet}" for bullet in bullets)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or limit <= 0:
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:]


def config_pack_limits(config: dict[str, Any], agent: str) -> dict[str, int]:
    limits = config["context_management"].get("context_pack_limits", {}).get(agent, {})
    return {
        "max_tokens": int(limits.get("max_tokens", config["context_management"]["max_context_budget_tokens_per_agent_call"])),
        "max_items": int(limits.get("max_items", 20)),
    }


def trim_pack_sections(pack: dict[str, Any], max_items: int) -> None:
    for section in pack["sections"]:
        content = section.get("content")
        if isinstance(content, list) and len(content) > max_items:
            section["content"] = content[-max_items:]
            section["truncated"] = True


def validate_context_pack_limits(pack: dict[str, Any], config: dict[str, Any], agent: str) -> dict[str, Any]:
    limits = config_pack_limits(config, agent)
    trim_pack_sections(pack, limits["max_items"])
    tokens = estimate_tokens(pack)
    while tokens > limits["max_tokens"] and pack["sections"]:
        reducible = [s for s in pack["sections"] if isinstance(s.get("content"), list) and s["content"]]
        if not reducible:
            break
        largest = max(reducible, key=lambda s: len(s["content"]))
        largest["content"] = largest["content"][1:]
        largest["truncated"] = True
        tokens = estimate_tokens(pack)
    pack["context_limits"] = {**limits, "estimated_tokens": tokens, "within_limit": tokens <= limits["max_tokens"]}
    return pack["context_limits"]


def build_context_packs(config: dict[str, Any], run_id: str, today: str, memory: dict[str, str], scored: list[dict[str, Any]], research: list[dict[str, Any]], decisions: list[dict[str, Any]], audits: list[dict[str, Any]], finals: list[dict[str, Any]], portfolio: dict[str, Any], memory_diff: dict[str, Any], out_root: Path, human_review: dict[str, Any] | None = None) -> dict[str, Any]:
    candidates = [a["ticker"] for a in scored[: config["candidate_filters"]["max_candidates_for_decision"]]]
    mf = {k: ROOT / v for k, v in memory.items()}
    common = {
        "run_id": run_id,
        "date": today,
        "phase": "FASE_8",
        "config_digest": config_digest(config),
        "safety": {"mode": config["system"]["mode"], "allow_real_orders": config["system"]["allow_real_orders"], "external_apis_used": llm_enabled(config) or market_data_settings(config).get("enabled"), "llms_used": llm_enabled(config), "broker_connected": False},
    }
    recent_decisions = tail_jsonl(mf["decision_ledger"], 30)
    recent_audits = tail_jsonl(mf["audit_ledger"], 30)
    thesis = tail_jsonl(mf["asset_thesis_memory"], 40)
    rejected = tail_jsonl(mf["rejected_assets_memory"], 40)
    relevant_thesis = [x for x in thesis if x.get("ticker") in candidates]
    relevant_rejected = [x for x in rejected if x.get("ticker") in candidates]
    overrides = read_text(mf["human_overrides"], "").strip()
    methodology = read_text(mf["methodology_state"], "").strip()[:2400]
    data_quality = read_text(mf["data_quality_memory"], "").strip()[:1800]
    perf = read_text(mf["performance_memory"], "").strip().splitlines()[-12:]
    fwd = read_csv_rows(ROOT / performance_settings(config).get("forward_test_results", "memory/forward_test_results.csv"))[-12:]
    postmortem_excerpt = read_text(mf["postmortem_memory"], "").strip()[-2400:]
    coverage_path = out_root / "universe_coverage_report.json"
    coverage = read_json(coverage_path, {}) if coverage_path.exists() else {}
    packs = {
        "research": [
            {"name": "methodology_excerpt", "content": methodology},
            {"name": "current_candidates", "content": [{"ticker": a["ticker"], "company": a["company"], "sector": a["sector"], "score": a["total_score"], "alerts": a["alerts"], "data_quality": a["data_quality"]} for a in scored if a["ticker"] in candidates]},
            {"name": "asset_specific_thesis", "content": relevant_thesis},
            {"name": "recent_memory_diff", "content": memory_diff["changes"]},
        ],
        "decision": [
            {"name": "portfolio_snapshot", "content": portfolio},
            {"name": "research_outputs", "content": research},
            {"name": "prior_candidate_decisions", "content": [x for x in recent_decisions if x.get("ticker") in candidates]},
            {"name": "human_overrides", "content": overrides},
        ],
        "audit": [
            {"name": "decisions_to_audit", "content": decisions},
            {"name": "research_evidence", "content": research},
            {"name": "data_quality_memory", "content": data_quality},
            {"name": "prior_auditor_objections", "content": [x for x in recent_audits if x.get("ticker") in candidates]},
            {"name": "rejected_assets", "content": relevant_rejected},
        ],
        "risk_orchestrator": [
            {"name": "portfolio_snapshot", "content": portfolio},
            {"name": "risk_config", "content": {"portfolio_rules": config["portfolio_rules"], "risk_rules": config["risk_rules"]}},
            {"name": "final_decisions", "content": finals},
            {"name": "human_overrides", "content": overrides},
        ],
        "report": [
            {"name": "run_summary", "content": {"candidates": candidates, "finals": finals, "positions": len(portfolio.get("positions", []))}},
            {"name": "memory_diff", "content": memory_diff["changes"]},
            {"name": "performance_recent", "content": perf},
            {"name": "forward_test_results_recent", "content": fwd},
            {"name": "universe_coverage", "content": coverage},
            {"name": "postmortem_lessons", "content": postmortem_excerpt},
            {"name": "human_review", "content": human_review or {}},
        ],
        "learning_postmortem": [
            {"name": "decisions", "content": decisions},
            {"name": "audits", "content": audits},
            {"name": "risk_results", "content": finals},
            {"name": "memory_diff", "content": memory_diff["changes"]},
            {"name": "forward_test_results_recent", "content": fwd},
            {"name": "postmortem_lessons", "content": postmortem_excerpt},
            {"name": "human_review", "content": human_review or {}},
        ],
    }
    pack_dir = out_root / "context_packs"
    summary = {"folder": str(pack_dir.relative_to(ROOT)), "packs": {}}
    for agent, sections in packs.items():
        pack = {**common, "agent": agent, "purpose": "Contexto mínimo, relevante y auditable para agente futuro; no contiene historial completo bruto.", "sections": sections}
        limits = validate_context_pack_limits(pack, config, agent)
        write_json(pack_dir / f"{agent}.json", pack)
        summary["packs"][agent] = {"path": str((pack_dir / f"{agent}.json").relative_to(ROOT)), **limits}
    return summary







def methodology_review_files(config: dict[str, Any]) -> dict[str, Path]:
    files = config["context_management"].get("human_review_files", {})
    return {
        "queue": ROOT / files.get("queue", "memory/human_review_queue.jsonl"),
        "decisions": ROOT / files.get("decisions", "memory/human_review_decisions.jsonl"),
        "versions": ROOT / files.get("versions", "memory/methodology_versions.jsonl"),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return tail_jsonl(path, 1_000_000)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_methodology_version(config: dict[str, Any]) -> str:
    versions = read_jsonl(methodology_review_files(config)["versions"])
    if versions:
        return str(versions[-1].get("methodology_version", "METHODOLOGY-000"))
    return "METHODOLOGY-000"


def next_methodology_version(version: str) -> str:
    try:
        n = int(str(version).split("-")[-1]) + 1
    except (ValueError, IndexError):
        n = 1
    return f"METHODOLOGY-{n:03d}"


def recommendation_id(payload: dict[str, Any]) -> str:
    key = json.dumps({k: payload.get(k) for k in ["run_id", "source", "description", "affected_metric", "impacted_methodology_section"]}, sort_keys=True, ensure_ascii=False)
    return "REC-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12].upper()


def make_recommendation(run_id: str, today: str, source: str, description: str, evidence: dict[str, Any], metric: str, severity: str, section: str, origin_version: str) -> dict[str, Any]:
    row = {"recommendation_id": "", "run_id": run_id, "date": today, "source": source, "description": description, "evidence": evidence, "affected_metric": metric, "severity": severity, "impacted_methodology_section": section, "status": "PENDING", "human_comment": None, "decision_date": None, "methodology_version_origin": origin_version, "methodology_version_target": None}
    row["recommendation_id"] = recommendation_id(row)
    return row


def extract_methodology_recommendations(config: dict[str, Any], out_root: Path, run_id: str, today: str, forward: dict[str, Any]) -> list[dict[str, Any]]:
    origin = current_methodology_version(config)
    m = forward.get("metrics", {})
    rows = forward.get("rows", [])
    recs = [
        make_recommendation(run_id, today, "forward_test_postmortem.md", "Revisar manualmente patrones de decisiones LOSS antes de cambiar reglas; evidencia insuficiente no debe aplicarse automáticamente.", {"loss_rows": [r for r in rows if r.get("status") == "LOSS"][:10], "postmortem": str((out_root / "forward_test_postmortem.md").relative_to(ROOT))}, "hit_rate", "MEDIUM", "risk_rules.loss_review", origin),
        make_recommendation(run_id, today, "forward_test_summary.json", "Analizar si las decisiones bloqueadas que hubieran funcionado indican exceso de conservadurismo antes de proponer cambios de umbrales.", {"blocked_would_have_worked": m.get("blocked_would_have_worked"), "summary": str((out_root / "forward_test_summary.json").relative_to(ROOT))}, "blocked_would_have_worked", "LOW" if not m.get("blocked_would_have_worked") else "MEDIUM", "risk_rules.conservatism", origin),
        make_recommendation(run_id, today, "forward_test_results.csv", "Mantener NOT_EVALUABLE visible y mejorar cobertura de datos sin imputar precios.", {"not_evaluable": m.get("not_evaluable"), "results": str((out_root / "forward_test_results.csv").relative_to(ROOT))}, "not_evaluable", "MEDIUM" if m.get("not_evaluable") else "LOW", "data_quality.forward_test_coverage", origin),
    ]
    # Traza de fuentes de memoria usadas para contexto; no convierte texto libre en hechos definitivos.
    recs.append(make_recommendation(run_id, today, "memory/methodology_state.md + memory/postmortem_memory.md", "Contrastar recomendaciones nuevas contra memoria metodológica y post-mortems antes de aprobar cambios versionados.", {"methodology_state_hash": sha256_text(read_text(ROOT / config["context_management"]["memory_files"]["methodology_state"])), "postmortem_memory_hash": sha256_text(read_text(ROOT / config["context_management"]["memory_files"]["postmortem_memory"]))}, "methodology_consistency", "LOW", "methodology_review.process", origin))
    return recs


def sync_human_review(config: dict[str, Any], out_root: Path, run_id: str, today: str, forward: dict[str, Any]) -> dict[str, Any]:
    files = methodology_review_files(config)
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    existing = {r.get("recommendation_id") for r in read_jsonl(files["queue"])}
    new_rows = [r for r in extract_methodology_recommendations(config, out_root, run_id, today, forward) if r["recommendation_id"] not in existing]
    for row in new_rows:
        append_jsonl(files["queue"], row)
    queue = {r["recommendation_id"]: r for r in read_jsonl(files["queue"])}
    decisions = read_jsonl(files["decisions"])
    for d in decisions:
        rid = d.get("recommendation_id")
        if rid in queue and d.get("status") in {"APPROVED", "REJECTED", "NEEDS_MORE_EVIDENCE"}:
            queue[rid].update({"status": d["status"], "human_comment": d.get("human_comment"), "decision_date": d.get("decision_date"), "methodology_version_target": d.get("methodology_version_target")})
    rows = list(queue.values())
    counts = {s: sum(1 for r in rows if r.get("status") == s) for s in ["PENDING", "APPROVED", "REJECTED", "NEEDS_MORE_EVIDENCE"]}
    approved = [r for r in rows if r.get("status") == "APPROVED"]
    rejected = [r for r in rows if r.get("status") == "REJECTED"]
    version = current_methodology_version(config)
    target = next_methodology_version(version) if approved else None
    proposed = {"run_id": run_id, "date": today, "current_methodology_version": version, "proposed_methodology_version": target, "approved_recommendations": approved, "rejected_recommendations": rejected, "pending_counts": counts, "config_demo_yaml_modified": False, "paper_trading_only": True, "broker_connected": False, "real_order": False}
    write_json(out_root / "proposed_methodology_changes.json", proposed)
    md = [f"# Cambios metodológicos propuestos - {today}", "", f"- Versión vigente: `{version}`", f"- Versión propuesta: `{target or 'sin cambios aprobados'}`", "- No modifica `config/config_demo.yaml` automáticamente.", "- Paper trading únicamente; broker desconectado; real_order=false.", "", "## Aprobadas"]
    md += [f"- `{r['recommendation_id']}`: {r['description']}" for r in approved] or ["- Ninguna."]
    md += ["", "## Rechazadas"] + ([f"- `{r['recommendation_id']}`: {r['description']}" for r in rejected] or ["- Ninguna."])
    (out_root / "proposed_methodology_changes.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if approved:
        prev = read_text(ROOT / config["context_management"]["memory_files"]["methodology_state"])
        new_state = prev + "\n\n<!-- proposed-only: approved human review recommendations; config not auto-modified -->\n" + json.dumps([r["recommendation_id"] for r in approved], ensure_ascii=False)
        version_row = {"methodology_version": target, "date": today, "run_id": run_id, "approved_changes": [r["recommendation_id"] for r in approved], "rejected_recommendations": [r["recommendation_id"] for r in rejected], "evidence": [r.get("evidence") for r in approved], "previous_methodology_hash": sha256_text(prev), "proposed_methodology_hash": sha256_text(new_state), "config_demo_yaml_modified": False}
        known_versions = {v.get("methodology_version") for v in read_jsonl(files["versions"])}
        if target not in known_versions:
            append_jsonl(files["versions"], version_row)
    return {"files": {k: str(v.relative_to(ROOT)) for k, v in files.items()}, "new_recommendations": len(new_rows), "counts": counts, "current_methodology_version": current_methodology_version(config), "approved": approved[-10:], "rejected": rejected[-10:], "pending": [r for r in rows if r.get("status") == "PENDING"][-10:], "needs_more_evidence": [r for r in rows if r.get("status") == "NEEDS_MORE_EVIDENCE"][-10:], "proposed_changes": str((out_root / "proposed_methodology_changes.json").relative_to(ROOT))}

def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "si", "sí"}


def asset_identity(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": asset.get("ticker"),
        "name": asset.get("name") or asset.get("company") or asset.get("ticker"),
        "country": asset.get("country", "US"),
        "market": asset.get("market", asset.get("country", "US")),
        "currency": asset.get("currency", "USD"),
        "instrument_type": asset.get("instrument_type", "common_stock"),
        "sector": asset.get("sector", "Unknown"),
        "industry": asset.get("industry", "Unknown"),
        "preferred_data_provider": asset.get("preferred_data_provider", "fixture"),
        "exchange": asset.get("exchange", asset.get("market", asset.get("country", "US"))),
        "eligible_for_investment": parse_bool(asset.get("eligible_for_investment", False)),
        "eligible_as_benchmark": parse_bool(asset.get("eligible_as_benchmark", False)),
        "min_liquidity_required_usd": safe_float(asset.get("min_liquidity_required_usd")) or 1000000,
        "analysis_priority": int(safe_float(asset.get("analysis_priority")) or 100),
        "notes": asset.get("notes", ""),
    }


def configured_universe_mode(config: dict[str, Any]) -> str:
    modes = config.get("universe_modes", {})
    return config.get("market_data", {}).get("universe_mode") or modes.get("default", "demo_small")


def fixture_metadata_for_ticker(ticker: str) -> dict[str, Any]:
    by_ticker = {a["ticker"]: a for a in read_json(FIXTURE_PATH, [])}
    base = by_ticker.get(ticker, {"ticker": ticker, "company": ticker, "country": "US", "market": "US", "currency": "USD", "sector": "Unknown"})
    country = base.get("country", "US")
    instrument = "adr" if country in {"BR", "AR"} and base.get("market", "US") == "US" else "common_stock"
    defaults = {"price_close": 100.0, "avg_volume_usd": 5000000, "data_quality": "MEDIUM", "metrics": {"pe_ttm": 15.0, "ev_ebitda": 9.0, "fcf_yield": 0.04, "roe": 0.10, "revenue_growth": 0.04, "net_debt_ebitda": 2.0, "momentum_6m": 0.0, "drawdown_52w": -0.10}}
    return {**defaults, **base, "name": base.get("company", ticker), "instrument_type": instrument, "industry": base.get("industry", "Unknown"), "preferred_data_provider": "fixture", "eligible_for_investment": True, "eligible_as_benchmark": False, "min_liquidity_required_usd": 1000000, "notes": "Cargado desde fixture para modo de universo ampliado."}


def universe_builder_settings(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "catalog_folder": "data/universe_catalogs",
        "catalog_files": ["us_equities.csv", "brazil_equities.csv", "argentina_equities.csv", "adrs.csv", "manual_overrides.csv"],
        "allow_benchmarks_in_scoring": False,
        "filters": {
            "allowed_countries": ["US", "BR", "AR"],
            "allowed_markets": ["US", "NYSE", "NASDAQ", "NYSEARCA", "B3", "BYMA", "BR", "AR"],
            "allowed_instrument_types": config.get("allowed_instrument_types", ["common_stock", "adr", "cedear"]),
            "min_liquidity_usd": 1000000,
            "min_price": 1.0,
            "min_avg_volume_usd": 1000000,
            "min_data_quality": config.get("market_data", {}).get("min_quality_for_scoring", "MEDIUM"),
            "manual_exclusions": [],
            "max_assets_for_research": config.get("candidate_filters", {}).get("max_candidates_for_research", 25),
            "max_assets_per_run_broad_market": 50,
        },
    }
    configured = config.get("universe_builder", {})
    merged = {**defaults, **configured}
    merged["filters"] = {**defaults["filters"], **configured.get("filters", {})}
    return merged


def load_universe_catalogs(config: dict[str, Any]) -> list[dict[str, Any]]:
    settings = universe_builder_settings(config)
    folder = ROOT / settings["catalog_folder"]
    rows: list[dict[str, Any]] = []
    for filename in settings.get("catalog_files", []):
        path = folder / filename
        for row in read_csv_rows(path):
            if not row.get("ticker"):
                continue
            cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            cleaned["catalog_source"] = str(path.relative_to(ROOT))
            rows.append(cleaned)
    return rows


def merge_catalog_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = row["ticker"].strip().upper()
        merged[ticker] = {**merged.get(ticker, {}), **row, "ticker": ticker}
    return merged


def resolve_universes(config: dict[str, Any]) -> dict[str, Any]:
    mode = configured_universe_mode(config)
    mode_cfg = config.get("universe_modes", {}).get(mode, {})
    symbols = [str(s).upper() for s in mode_cfg.get("symbols", [])]
    settings = universe_builder_settings(config)
    filters = settings["filters"]
    catalog_by_ticker = merge_catalog_rows(load_universe_catalogs(config))
    legacy_investable = {a["ticker"].upper(): a for a in config.get("investable_universe", [])}
    benchmark_cfg = {a["ticker"].upper(): a for a in config.get("benchmark_universe", [])}
    excluded_cfg = {a["ticker"].upper(): a for a in config.get("excluded_symbols", [])}
    manual_exclusions = {str(t).upper() for t in filters.get("manual_exclusions", [])}

    selected = symbols or sorted(catalog_by_ticker)
    if mode == "broad_market":
        selected = sorted(set(selected) | set(catalog_by_ticker))
        selected = selected[: int(filters.get("max_assets_per_run_broad_market", 50))]

    investable, blocked = [], []
    allowed_types = set(filters.get("allowed_instrument_types", config.get("allowed_instrument_types", [])))
    allowed_countries = set(filters.get("allowed_countries", []))
    allowed_markets = set(filters.get("allowed_markets", []))
    for ticker in selected:
        if ticker in excluded_cfg or ticker in manual_exclusions:
            source = excluded_cfg.get(ticker) or catalog_by_ticker.get(ticker) or legacy_investable.get(ticker) or {"ticker": ticker}
            blocked.append({**asset_identity(source), "block_reason": "excluded_symbol"})
            continue
        if ticker in benchmark_cfg and not settings.get("allow_benchmarks_in_scoring", False):
            blocked.append({**asset_identity(benchmark_cfg[ticker]), "block_reason": "benchmark_not_investable"})
            continue
        meta = {**legacy_investable.get(ticker, {}), **catalog_by_ticker.get(ticker, {})} or fixture_metadata_for_ticker(ticker)
        ident = asset_identity(meta)
        reason = None
        if not ident["eligible_for_investment"]:
            reason = "not_eligible_for_investment"
        elif ident["eligible_as_benchmark"] and not settings.get("allow_benchmarks_in_scoring", False):
            reason = "benchmark_not_investable"
        elif allowed_countries and ident["country"] not in allowed_countries:
            reason = "country_not_allowed"
        elif allowed_markets and ident["market"] not in allowed_markets:
            reason = "market_not_allowed"
        elif ident["instrument_type"] not in allowed_types:
            reason = "instrument_type_not_allowed"
        elif ident["min_liquidity_required_usd"] < float(filters.get("min_liquidity_usd", 0)):
            reason = "minimum_liquidity_requirement_too_low"
        if reason:
            blocked.append({**ident, "block_reason": reason})
        else:
            investable.append(ident)
    investable.sort(key=lambda a: (a.get("analysis_priority", 100), a["ticker"]))
    benchmarks = [asset_identity(a) for a in config.get("benchmark_universe", []) if parse_bool(a.get("eligible_as_benchmark"))]
    excluded = [asset_identity(a) for a in config.get("excluded_symbols", [])] + blocked
    return {"mode": mode, "investable": investable, "benchmarks": benchmarks, "excluded": excluded, "catalog_assets_loaded": len(catalog_by_ticker), "filters": filters}

def has_sufficient_data_for_scoring(asset: dict[str, Any], config: dict[str, Any]) -> bool:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    minimum = market_data_settings(config).get("min_quality_for_scoring", "MEDIUM")
    required = ["price_close", "avg_volume_usd", "metrics"]
    return all(asset.get(k) not in (None, {}) for k in required) and not asset.get("missing_fields") and order.get(asset.get("data_quality", "LOW"), 0) >= order.get(minimum, 1)


class MarketDataProviderError(RuntimeError):
    """Error explícito de proveedor de datos de mercado."""


def market_data_settings(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "mode": "fixture",
        "enabled": False,
        "provider": "fixture",
        "fallback_to_fixture": True,
        "block_on_low_quality": True,
        "min_quality_for_scoring": "MEDIUM",
        "snapshot_folder": "data/snapshots",
        "timeout_seconds": 20,
        "universe": [],
        "providers": {},
    }
    settings = {**defaults, **config.get("market_data", {})}
    if not settings.get("universe"):
        settings["universe"] = resolve_universes(config)["investable"]
    if settings.get("mode") == "fixture":
        settings["enabled"] = False
    return settings


def validate_market_data_safety(config: dict[str, Any]) -> list[str]:
    settings = market_data_settings(config)
    errors: list[str] = []
    if settings.get("mode") not in {"fixture", "real"}:
        errors.append("market_data.mode debe ser fixture o real")
    if settings.get("mode") == "real" and not settings.get("enabled"):
        errors.append("market_data.mode=real requiere market_data.enabled=true explícito")
    if settings.get("enabled") and settings.get("mode") != "real":
        errors.append("market_data.enabled=true requiere market_data.mode=real explícito")
    if settings.get("provider") not in {"fixture", "stooq_csv"}:
        errors.append(f"market_data.provider no soportado en Fase 6: {settings.get('provider')}")
    return errors


def fixture_raw_payload(today: str) -> dict[str, Any]:
    return {"provider": "fixture", "as_of_date": today, "fetched_at": utc_now().isoformat(), "assets": read_json(FIXTURE_PATH, [])}


def stooq_symbol(ticker: str) -> str:
    return ticker.lower() if "." in ticker else f"{ticker.lower()}.us"


def fetch_stooq_csv(universe: list[dict[str, Any]], today: str, timeout: int) -> dict[str, Any]:
    rows = []
    errors = []
    for item in universe:
        ticker = item["ticker"] if isinstance(item, dict) else str(item)
        symbol = (item.get("provider_symbol") if isinstance(item, dict) else None) or stooq_symbol(ticker)
        url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - proveedor explícito configurado
                text = response.read().decode("utf-8")
            parsed = list(csv.DictReader(text.splitlines()))
            raw = parsed[0] if parsed else {}
            rows.append({"ticker": ticker, "provider_symbol": symbol, "url": url, "raw": raw})
        except Exception as exc:
            errors.append({"ticker": ticker, "provider_symbol": symbol, "error": str(exc)})
    return {"provider": "stooq_csv", "as_of_date": today, "fetched_at": utc_now().isoformat(), "assets": rows, "errors": errors}


def normalize_market_data(raw_payload: dict[str, Any], config: dict[str, Any], today: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if raw_payload["provider"] == "fixture":
        assets = []
        for asset in raw_payload["assets"]:
            cloned = json.loads(json.dumps(asset))
            cloned["company"] = cloned.get("company") or cloned.get("name") or cloned.get("ticker")
            cloned.update({"data_source": "fixture", "as_of_date": today, "estimated_fields": [], "missing_fields": [], "provider_errors": []})
            assets.append(cloned)
        return assets, build_data_quality_report([], "pending", today, assets, raw_payload)

    fixture_by_ticker = {a["ticker"]: a for a in read_json(FIXTURE_PATH, [])}
    assets = []
    required = ["price_close", "avg_volume_usd"]
    for row in raw_payload.get("assets", []):
        ticker = row["ticker"]
        base = json.loads(json.dumps(fixture_by_ticker.get(ticker, {"ticker": ticker, "company": ticker, "country": "US", "market": "US", "sector": "Unknown", "currency": "USD", "metrics": {"pe_ttm": 15.0, "ev_ebitda": 9.0, "fcf_yield": 0.04, "roe": 0.10, "revenue_growth": 0.04, "net_debt_ebitda": 2.0, "momentum_6m": 0.0, "drawdown_52w": -0.10}})))
        raw = row.get("raw", {})
        missing = []
        errors = []
        close = raw.get("Close")
        vol = raw.get("Volume")
        try:
            close_value = float(close) if close not in {None, "", "N/D"} else None
        except ValueError:
            close_value = None
        try:
            volume_value = float(vol) if vol not in {None, "", "N/D"} else None
        except ValueError:
            volume_value = None
        if close_value is None:
            missing.append("price_close")
        else:
            base["price_close"] = close_value
        if volume_value is None or close_value is None:
            missing.append("avg_volume_usd")
        else:
            base["avg_volume_usd"] = close_value * volume_value
        for field in required:
            if field not in base or base.get(field) is None:
                missing.append(field)
        if raw.get("Date") in {None, "", "N/D"}:
            missing.append("provider_date")
        # Fundamentals remain from fixture baseline and are explicitly estimated, never invented as real.
        estimated = [f"metrics.{k}" for k in base.get("metrics", {})]
        quality = "HIGH" if not missing and len(estimated) == 0 else "MEDIUM" if not missing else "LOW"
        base.update({"data_source": "real_provider_with_fixture_fundamentals" if estimated else "real_provider", "provider": raw_payload["provider"], "provider_date": raw.get("Date"), "as_of_date": today, "data_quality": quality, "estimated_fields": estimated, "missing_fields": sorted(set(missing)), "provider_errors": errors})
        assets.append(base)
    error_by_ticker = {e.get("ticker"): e.get("error") for e in raw_payload.get("errors", [])}
    for ticker, error in error_by_ticker.items():
        if ticker not in {a["ticker"] for a in assets}:
            base = json.loads(json.dumps(fixture_by_ticker.get(ticker, {"ticker": ticker, "company": ticker, "country": "US", "market": "US", "sector": "Unknown", "currency": "USD", "metrics": {"pe_ttm": 15.0, "ev_ebitda": 9.0, "fcf_yield": 0.04, "roe": 0.10, "revenue_growth": 0.04, "net_debt_ebitda": 2.0, "momentum_6m": 0.0, "drawdown_52w": -0.10}})))
            base.update({"data_source": "real_provider_failed", "provider": raw_payload["provider"], "as_of_date": today, "data_quality": "LOW", "estimated_fields": [], "missing_fields": required, "provider_errors": [error]})
            assets.append(base)
    return assets, build_data_quality_report([], "pending", today, assets, raw_payload)


def load_market_data(config: dict[str, Any], today: str, run_id: str, log_path: Path, out_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    settings = market_data_settings(config)
    provider = "fixture" if not settings.get("enabled") else settings.get("provider")
    append_jsonl(log_path, {"event": "market_data_started", "run_id": run_id, "provider": provider, "mode": settings.get("mode")})
    try:
        raw = fixture_raw_payload(today) if provider == "fixture" else fetch_stooq_csv(settings.get("universe", []), today, int(settings.get("timeout_seconds", 20)))
        if provider == "fixture":
            wanted = {item["ticker"] for item in settings.get("universe", [])}
            metadata = {item["ticker"]: item for item in settings.get("universe", [])}
            fixture_assets = {asset.get("ticker"): asset for asset in raw.get("assets", [])}
            raw["assets"] = [{**fixture_assets.get(ticker, fixture_metadata_for_ticker(ticker)), **metadata.get(ticker, {})} for ticker in wanted]
    except Exception as exc:
        append_jsonl(log_path, {"event": "market_data_provider_error", "run_id": run_id, "provider": provider, "error": str(exc)})
        if not settings.get("fallback_to_fixture"):
            raise MarketDataProviderError(str(exc)) from exc
        raw = fixture_raw_payload(today)
        raw["fallback_reason"] = str(exc)
    normalized, quality = normalize_market_data(raw, config, today)
    quality.update({"run_id": run_id, "date": today})
    snap_dir = ROOT / settings.get("snapshot_folder", "data/snapshots") / today / run_id
    write_json(snap_dir / "raw_market_data.json", raw)
    write_json(snap_dir / "normalized_market_data.json", normalized)
    write_json(snap_dir / "data_quality_report.json", quality)
    write_json(out_root / "snapshots" / "raw_market_data.json", raw)
    write_json(out_root / "snapshots" / "normalized_market_data.json", normalized)
    paths = {"raw": str((snap_dir / "raw_market_data.json").relative_to(ROOT)), "normalized": str((snap_dir / "normalized_market_data.json").relative_to(ROOT)), "quality": str((snap_dir / "data_quality_report.json").relative_to(ROOT))}
    append_jsonl(log_path, {"event": "market_data_finished", "run_id": run_id, "provider": raw.get("provider"), "assets": len(normalized), "blocked_assets": quality.get("blocked_assets", []), "snapshots": paths, "errors": raw.get("errors", [])})
    return normalized, quality, paths


class LLMConfigError(RuntimeError):
    """Configuración LLM incompleta o insegura para modo real."""


class LLMResponseError(RuntimeError):
    """Respuesta LLM inválida o no parseable."""


def llm_settings(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "enabled": False,
        "real_agents": [],
        "fallback_to_mock": True,
        "block_on_invalid_response": False,
        "max_candidates_sent": 3,
        "max_retries": 1,
        "timeout_seconds": 20,
        "max_cost_usd_per_run": 1.0,
        "providers": {},
    }
    return {**defaults, **config.get("llm", {})}


def llm_enabled(config: dict[str, Any]) -> bool:
    return bool(llm_settings(config).get("enabled"))


def real_agent_enabled(config: dict[str, Any], agent: str) -> bool:
    settings = llm_settings(config)
    return bool(settings.get("enabled") and agent in set(settings.get("real_agents", [])))


def validate_llm_safety(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    settings = llm_settings(config)
    real_agents = set(settings.get("real_agents", []))
    unsupported = real_agents - {"research_agent"}
    if unsupported:
        errors.append(f"Fase 4 solo permite research_agent real; no permitido: {sorted(unsupported)}")
    if settings.get("enabled") and not real_agents:
        errors.append("llm.enabled=true requiere llm.real_agents con research_agent explícito")
    if int(settings.get("max_candidates_sent", 0)) < 1:
        errors.append("llm.max_candidates_sent debe ser >= 1")
    if float(settings.get("max_cost_usd_per_run", 0)) <= 0:
        errors.append("llm.max_cost_usd_per_run debe ser > 0")
    return errors


def require_api_key(config: dict[str, Any], agent_key: str) -> tuple[str, str, str]:
    agent_cfg = config["agents"][agent_key]
    provider = agent_cfg["provider"]
    model = agent_cfg.get("model")
    provider_cfg = llm_settings(config).get("providers", {}).get(provider, {})
    env_var = provider_cfg.get("api_key_env")
    if not env_var:
        raise LLMConfigError(f"Proveedor LLM '{provider}' no tiene api_key_env configurado.")
    api_key = os.environ.get(env_var)
    if not api_key:
        raise LLMConfigError(f"Falta API key requerida para {agent_key}: defina variable de entorno {env_var}.")
    if not model:
        raise LLMConfigError(f"{agent_key} no tiene modelo configurado.")
    return provider, model, api_key


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise LLMResponseError("La respuesta LLM no contiene JSON object.")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise LLMResponseError("La respuesta LLM debe ser un objeto JSON.")
    return value


def llm_audit_log(log_path: Path, row: dict[str, Any]) -> None:
    append_jsonl(log_path, {"event": "llm_call", **row})


def call_openai_chat(api_key: str, model: str, prompt: str, context_pack: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(context_pack, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - URL fija del proveedor configurado
        payload = json.loads(response.read().decode("utf-8"))
    output = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage", {})
    return {"output_text": output, "usage": usage, "estimated_cost_usd": None}


def call_anthropic_messages(api_key: str, model: str, prompt: str, context_pack: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps({
        "model": model,
        "max_tokens": 1500,
        "system": prompt,
        "messages": [{"role": "user", "content": json.dumps(context_pack, ensure_ascii=False)}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - URL fija del proveedor configurado
        payload = json.loads(response.read().decode("utf-8"))
    output = "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
    usage = payload.get("usage", {})
    return {"output_text": output, "usage": usage, "estimated_cost_usd": None}


def call_llm_provider(provider: str, api_key: str, model: str, prompt: str, context_pack: dict[str, Any], timeout: int) -> dict[str, Any]:
    if provider == "openai":
        return call_openai_chat(api_key, model, prompt, context_pack, timeout)
    if provider == "anthropic":
        return call_anthropic_messages(api_key, model, prompt, context_pack, timeout)
    raise LLMConfigError(f"Proveedor LLM no implementado en Fase 4: {provider}")


def build_research_pack_for_asset(research_pack: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    packed = json.loads(json.dumps(research_pack, ensure_ascii=False))
    for section in packed.get("sections", []):
        if section.get("name") == "current_candidates" and isinstance(section.get("content"), list):
            section["content"] = [row for row in section["content"] if row.get("ticker") == asset["ticker"]]
        if section.get("name") == "asset_specific_thesis" and isinstance(section.get("content"), list):
            section["content"] = [row for row in section["content"] if row.get("ticker") == asset["ticker"]]
    packed["target_ticker"] = asset["ticker"]
    return packed


def research_with_optional_llm(config: dict[str, Any], today: str, candidates: list[dict[str, Any]], context_pack_path: Path, log_path: Path, call_provider: Callable[..., dict[str, Any]] = call_llm_provider) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = llm_settings(config)
    mock_all = [mock_research(asset, today) for asset in candidates]
    summary = {"enabled": llm_enabled(config), "agent": "research_agent", "mode": "mock", "calls": 0, "fallbacks": 0, "estimated_cost_usd": 0.0}
    if not real_agent_enabled(config, "research_agent"):
        return mock_all, summary
    provider, model, api_key = require_api_key(config, "research_agent")
    prompt_path = ROOT / config["agents"]["research_agent"]["prompt_file"]
    prompt = prompt_path.read_text(encoding="utf-8")
    research_pack = read_json(context_pack_path, {})
    max_candidates = min(len(candidates), int(settings["max_candidates_sent"]))
    outputs = mock_all[:]
    for idx, asset in enumerate(candidates[:max_candidates]):
        per_asset_pack = build_research_pack_for_asset(research_pack, asset)
        attempts = 0
        last_error = None
        while attempts <= int(settings["max_retries"]):
            attempts += 1
            started = time.time()
            raw_output = ""
            validation = {"valid": False, "errors": []}
            try:
                result = call_provider(provider, api_key, model, prompt, per_asset_pack, int(settings["timeout_seconds"]))
                raw_output = result.get("output_text", "")
                parsed = extract_json_object(raw_output)
                errors = validate_schema(parsed, load_schema(SCHEMA_DIR / "research_output_schema.json"))
                validation = {"valid": not errors, "errors": errors}
                usage = result.get("usage", {})
                cost = result.get("estimated_cost_usd")
                if cost is not None:
                    summary["estimated_cost_usd"] += float(cost)
                llm_audit_log(log_path, {"run_id": per_asset_pack["run_id"], "agent": "research_agent", "provider": provider, "model": model, "prompt_file": str(prompt_path.relative_to(ROOT)), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "context_pack": str(context_pack_path.relative_to(ROOT)), "target_ticker": asset["ticker"], "attempt": attempts, "output": raw_output, "validation": validation, "usage": usage, "estimated_cost_usd": cost, "duration_seconds": round(time.time() - started, 3), "error": None})
                if errors:
                    raise LLMResponseError("; ".join(errors))
                outputs[idx] = parsed
                summary["calls"] += 1
                break
            except Exception as exc:  # clear logged failure; fallback/block decided below
                last_error = str(exc)
                llm_audit_log(log_path, {"run_id": research_pack.get("run_id"), "agent": "research_agent", "provider": provider, "model": model, "prompt_file": str(prompt_path.relative_to(ROOT)), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "context_pack": str(context_pack_path.relative_to(ROOT)), "target_ticker": asset["ticker"], "attempt": attempts, "output": raw_output, "validation": validation, "usage": {}, "estimated_cost_usd": None, "duration_seconds": round(time.time() - started, 3), "error": last_error})
                if attempts > int(settings["max_retries"]):
                    if settings.get("fallback_to_mock") and not settings.get("block_on_invalid_response"):
                        summary["fallbacks"] += 1
                        break
                    raise LLMResponseError(f"research_agent falló para {asset['ticker']}: {last_error}") from exc
        if summary["estimated_cost_usd"] > float(settings["max_cost_usd_per_run"]):
            raise LLMConfigError("Costo máximo por corrida excedido; se bloquea la corrida LLM.")
    summary["mode"] = "real_with_mock_fallback" if summary["fallbacks"] else "real"
    return outputs, summary


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(item.strip()) for item in inner.split(",") if item.strip()]
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
            item_text = stripped[2:].strip()
            if ":" in item_text and not (item_text.startswith("'") or item_text.startswith("\"")):
                item_key, item_sep, item_value = item_text.partition(":")
                item: dict[str, Any] = {}
                item[item_key.strip()] = parse_scalar(item_value.strip()) if item_value.strip() else {}
                parent.append(item)
                stack.append((indent, item))
            else:
                parent.append(parse_scalar(item_text))
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


def mock_research(asset: dict[str, Any], today: str) -> dict[str, Any]:
    return {
        "as_of_date": today,
        "ticker": asset["ticker"],
        "company": asset["company"],
        "research_status": "MOCK_PLACEHOLDER",
        "summary": "Research mock Fase 2: contrato reservado para integración futura, sin LLM ni datos reales.",
        "positive_factors": ["Score fixture disponible"],
        "negative_factors": asset["alerts"],
        "unknowns": ["No se consultaron fuentes externas", "No hay research cualitativo real"],
        "sources": [],
        "confidence": "LOW",
        "data_quality": asset["data_quality"],
    }


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




def pct_return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return round((current / previous) - 1, 6)


def safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def performance_settings(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("performance_tracking", {})


def mark_to_market_portfolio(portfolio: dict[str, Any], assets_by_ticker: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cash = float(portfolio.get("cash_usd", 0.0))
    positions = []
    for pos in portfolio.get("positions", []):
        updated = dict(pos)
        asset = assets_by_ticker.get(pos.get("ticker"))
        if asset and safe_float(asset.get("price_close")) is not None:
            updated["price_close"] = float(asset["price_close"])
            updated["market_value_usd"] = round(float(updated.get("shares", 0.0)) * updated["price_close"], 2)
        updated["price_data_missing"] = not bool(asset and safe_float(asset.get("price_close")) is not None)
        positions.append(updated)
    total = round(cash + sum(float(p.get("market_value_usd", 0.0)) for p in positions), 2)
    for p in positions:
        p["weight"] = round(float(p.get("market_value_usd", 0.0)) / total, 6) if total else 0.0
    portfolio["positions"] = positions
    portfolio["portfolio_value_usd"] = total
    portfolio["portfolio_metrics"] = {"number_of_positions": len(positions), "cash_weight": round(cash / total, 6) if total else 0.0}
    return portfolio


def portfolio_exposures(portfolio: dict[str, Any]) -> dict[str, Any]:
    total = float(portfolio.get("portfolio_value_usd", 0.0)) or 0.0
    buckets = {"country": {}, "sector": {}, "asset": {}}
    for p in portfolio.get("positions", []):
        value = float(p.get("market_value_usd", 0.0))
        weight = round(value / total, 6) if total else 0.0
        buckets["asset"][p["ticker"]] = weight
        for key in ["country", "sector"]:
            name = p.get(key, "Unknown")
            buckets[key][name] = round(buckets[key].get(name, 0.0) + weight, 6)
    buckets["asset"]["CASH"] = round(float(portfolio.get("cash_usd", 0.0)) / total, 6) if total else 0.0
    return buckets


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_csv_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f) for f in fields})


def benchmark_snapshot(config: dict[str, Any], today: str, assets_by_ticker: dict[str, dict[str, Any]], exposures: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    settings = performance_settings(config)
    bench_cfg = settings.get("benchmarks", {})
    history_path = ROOT / settings.get("benchmark_prices_file", "memory/benchmark_prices.csv")
    prior = {(r.get("ticker")): safe_float(r.get("price")) for r in read_csv_rows(history_path) if r.get("date") < today}
    rows = []
    price_by_ticker: dict[str, float] = {}
    for ticker, cfg in bench_cfg.items():
        asset = assets_by_ticker.get(ticker)
        price = safe_float(asset.get("price_close")) if asset else safe_float(cfg.get("fixture_price"))
        missing = price is None
        previous = prior.get(ticker)
        rows.append({"date": today, "ticker": ticker, "label": cfg.get("label"), "price": price, "return_daily": pct_return(price, previous), "missing_data": missing, "source": "market_data_or_fixture_config" if not missing else "missing_not_invented"})
        if price is not None:
            price_by_ticker[ticker] = price
    append_csv_rows(history_path, rows, ["date", "ticker", "label", "price", "return_daily", "missing_data", "source"])
    return rows, price_by_ticker


def composite_weights(config: dict[str, Any], portfolio: dict[str, Any]) -> dict[str, float]:
    comp = performance_settings(config).get("composite_benchmark", {})
    mapping = comp.get("country_to_benchmark", {})
    sector_overrides = comp.get("sector_overrides", {})
    weights: dict[str, float] = {}
    total = float(portfolio.get("portfolio_value_usd", 0.0)) or 0.0
    for p in portfolio.get("positions", []):
        w = float(p.get("market_value_usd", 0.0)) / total if total else 0.0
        bench = sector_overrides.get(p.get("sector")) or mapping.get(p.get("country"))
        if bench:
            weights[bench] = weights.get(bench, 0.0) + w
    cash_w = float(portfolio.get("cash_usd", 0.0)) / total if total else 0.0
    cash_b = comp.get("default_cash_benchmark")
    if cash_b:
        weights[cash_b] = weights.get(cash_b, 0.0) + cash_w
    return {k: round(v, 6) for k, v in weights.items()}


def build_performance(run_id: str, today: str, config: dict[str, Any], portfolio: dict[str, Any], assets_by_ticker: dict[str, dict[str, Any]]) -> dict[str, Any]:
    portfolio = mark_to_market_portfolio(portfolio, assets_by_ticker)
    exposures = portfolio_exposures(portfolio)
    rows = read_csv_rows(ROOT / config["context_management"]["memory_files"]["performance_memory"])
    prev_nav = None
    if rows:
        prev_nav = safe_float(rows[-1].get("nav") or rows[-1].get("portfolio_value_usd"))
    nav = float(portfolio.get("portfolio_value_usd", 0.0))
    daily = pct_return(nav, prev_nav)
    initial = float(config["system"]["initial_capital_usd"])
    bench_rows, _ = benchmark_snapshot(config, today, assets_by_ticker, exposures)
    comp_w = composite_weights(config, portfolio)
    comp_ret_parts = []
    for r in bench_rows:
        if r["ticker"] in comp_w and r["return_daily"] is not None:
            comp_ret_parts.append(comp_w[r["ticker"]] * r["return_daily"])
    comp_daily = round(sum(comp_ret_parts), 6) if comp_ret_parts else None
    return {"run_id": run_id, "date": today, "nav": round(nav, 2), "cash_usd": portfolio.get("cash_usd"), "positions_count": len(portfolio.get("positions", [])), "daily_return": daily, "cumulative_return": pct_return(nav, initial), "weekly_return": daily, "monthly_return": daily, "since_inception_return": pct_return(nav, initial), "exposures": exposures, "benchmarks": bench_rows, "composite_benchmark": {"weights": comp_w, "daily_return": comp_daily}, "risk_metrics": {"drawdown": min(0.0, pct_return(nav, max([safe_float(r.get('nav') or r.get('portfolio_value_usd')) or nav for r in rows] + [nav])) or 0.0), "volatility_simple": None if daily is None else 0.0}, "missing_benchmark_data": [r["ticker"] for r in bench_rows if r["missing_data"]]}


def build_decision_tracking(run_id: str, today: str, decisions: list[dict[str, Any]], audits: list[dict[str, Any]], finals: list[dict[str, Any]], assets_by_ticker: dict[str, dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    rows=[]
    for d,a,f in zip(decisions,audits,finals):
        rows.append({"run_id":run_id,"date":today,"ticker":d["ticker"],"proposed_action":d["decision"],"final_action":f["final_decision"],"suggested_weight":d["suggested_weight"],"final_weight":f["final_weight"],"reference_price":assets_by_ticker.get(d["ticker"],{}).get("price_close"),"approval_reduction_or_block_reason":f.get("reason_for_blocking") or f.get("reason_for_adjustment"),"mock_audit":a,"risk_rules_applied":f["risk_rules_triggered"],"benchmark_used":benchmark_for_asset(config, assets_by_ticker.get(d["ticker"])),"real_order":False})
    return rows


def forward_pending_rows(tracking: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    rows=[]
    for row in tracking:
        for months in performance_settings(config).get("windows_months", [3,6,12]):
            due = add_months_approx(row["date"], int(months))
            rows.append({"run_id":row["run_id"],"decision_date":row["date"],"ticker":row["ticker"],"window_months":months,"due_date":due,"final_action":row["final_action"],"reference_price":row["reference_price"],"benchmark_used":row.get("benchmark_used") or benchmark_for_asset(config, None),"status":"PENDING"})
    return rows




def add_months_approx(date_text: str, months: int) -> str:
    return (dt.date.fromisoformat(date_text) + dt.timedelta(days=30 * int(months))).isoformat()


def benchmark_for_asset(config: dict[str, Any], asset: dict[str, Any] | None) -> str:
    comp = performance_settings(config).get("composite_benchmark", {})
    if asset:
        override = comp.get("sector_overrides", {}).get(asset.get("sector"))
        if override:
            return override
        mapped = comp.get("country_to_benchmark", {}).get(asset.get("country"))
        if mapped:
            return mapped
    return config.get("markets", {}).get("benchmarks", {}).get("US", "SPY")


def price_on_or_after(rows: list[dict[str, str]], ticker: str, target_date: str) -> float | None:
    dated = sorted((r for r in rows if r.get("ticker") == ticker and r.get("date", "") >= target_date), key=lambda r: r.get("date", ""))
    for row in dated:
        price = safe_float(row.get("price"))
        if price is not None:
            return price
    return None


def evaluate_forward_tests(run_id: str, today: str, config: dict[str, Any], assets_by_ticker: dict[str, dict[str, Any]], performance: dict[str, Any]) -> dict[str, Any]:
    settings = performance_settings(config)
    pending_path = ROOT / settings.get("forward_test_pending", "memory/forward_test_pending.csv")
    result_path = ROOT / settings.get("forward_test_results", "memory/forward_test_results.csv")
    pending = read_csv_rows(pending_path)
    prior_results = read_csv_rows(result_path)
    done = {(r.get("source_run_id"), r.get("decision_date"), r.get("ticker"), str(r.get("window_months"))) for r in prior_results}
    benchmark_rows = read_csv_rows(ROOT / settings.get("benchmark_prices_file", "memory/benchmark_prices.csv"))
    benchmark_current = {r["ticker"]: safe_float(r.get("price")) for r in performance.get("benchmarks", []) if safe_float(r.get("price")) is not None}
    rows: list[dict[str, Any]] = []
    asof = dt.date.fromisoformat(today)
    for item in pending:
        key = (item.get("run_id"), item.get("decision_date"), item.get("ticker"), str(item.get("window_months")))
        if key in done or item.get("status") == "EVALUATED":
            continue
        due = item.get("due_date") or add_months_approx(item["decision_date"], int(item.get("window_months", 0)))
        if dt.date.fromisoformat(due) > asof:
            continue
        ticker = item.get("ticker", "")
        asset = assets_by_ticker.get(ticker)
        initial = safe_float(item.get("reference_price"))
        final = safe_float(asset.get("price_close")) if asset else None
        bench = benchmark_for_asset(config, asset) if asset else (item.get("benchmark_used") or benchmark_for_asset(config, None))
        bench_initial = price_on_or_after(benchmark_rows, bench, item.get("decision_date", ""))
        bench_final = benchmark_current.get(bench) or price_on_or_after(benchmark_rows, bench, today)
        ret = pct_return(final, initial)
        bret = pct_return(bench_final, bench_initial)
        rel = round(ret - bret, 6) if ret is not None and bret is not None else None
        if ret is None or bret is None:
            status = "NOT_EVALUABLE"
            why = "Faltan precios iniciales/finales del activo o benchmark; no se inventan datos."
        else:
            status = "WIN" if rel > 0.01 else "LOSS" if rel < -0.01 else "NEUTRAL"
            why = f"Retorno activo {ret:.2%} vs benchmark {bench} {bret:.2%}; relativo {rel:.2%}."
        rows.append({"evaluation_run_id": run_id, "source_run_id": item.get("run_id"), "decision_date": item.get("decision_date"), "ticker": ticker, "window_months": item.get("window_months"), "due_date": due, "final_action": item.get("final_action"), "initial_price": initial, "final_price": final, "benchmark_used": bench, "benchmark_initial_price": bench_initial, "benchmark_final_price": bench_final, "absolute_return": ret, "benchmark_return": bret, "relative_return": rel, "status": status, "explanation": why})
    fields = ["evaluation_run_id","source_run_id","decision_date","ticker","window_months","due_date","final_action","initial_price","final_price","benchmark_used","benchmark_initial_price","benchmark_final_price","absolute_return","benchmark_return","relative_return","status","explanation"]
    if rows:
        append_csv_rows(result_path, rows, fields)
    evaluable = [r for r in rows if r["status"] != "NOT_EVALUABLE"]
    approved = [r for r in evaluable if r.get("final_action") == "APPROVED"]
    blocked = [r for r in evaluable if r.get("final_action") in {"BLOCKED", "NEED_MORE_DATA"}]
    wins = [r for r in evaluable if r["status"] == "WIN"]
    metrics = {"expired_windows": len(rows), "evaluable_decisions": len(evaluable), "not_evaluable": len(rows)-len(evaluable), "hit_rate": round(len(wins)/len(evaluable), 6) if evaluable else None, "avg_return_approved": avg([r["absolute_return"] for r in approved]), "avg_return_blocked": avg([r["absolute_return"] for r in blocked]), "approved_successful": len([r for r in approved if r["status"] == "WIN"]), "approved_failed": len([r for r in approved if r["status"] == "LOSS"]), "blocked_would_have_worked": len([r for r in blocked if r["status"] == "WIN"]), "blocked_avoided_losses": len([r for r in blocked if r["status"] == "LOSS"]), "avg_relative_return_vs_benchmark": avg([r["relative_return"] for r in evaluable]), "portfolio_drawdown": performance.get("risk_metrics", {}).get("drawdown"), "best_decisions": sorted(evaluable, key=lambda r: r.get("relative_return") or -999, reverse=True)[:3], "worst_decisions": sorted(evaluable, key=lambda r: r.get("relative_return") or 999)[:3]}
    return {"date": today, "rows": rows, "metrics": metrics, "status": "sin evaluaciones vencidas" if not rows else "evaluaciones procesadas"}


def avg(values: list[Any]) -> float | None:
    nums = [safe_float(v) for v in values if safe_float(v) is not None]
    return round(sum(nums)/len(nums), 6) if nums else None


def write_forward_postmortem(out_root: Path, run_id: str, today: str, forward: dict[str, Any]) -> Path:
    m = forward["metrics"]
    lines = [f"# Post-mortem forward-test DEMO - {today}", "", f"- Run ID: `{run_id}`", f"- Estado: {forward['status']}", f"- Ventanas vencidas procesadas: {m['expired_windows']}", f"- Evaluables: {m['evaluable_decisions']}; no evaluables: {m['not_evaluable']}", f"- Hit rate: {m['hit_rate']}", f"- Retorno promedio aprobadas: {m['avg_return_approved']}", f"- Retorno promedio bloqueadas: {m['avg_return_blocked']}", f"- Retorno relativo promedio vs benchmark: {m['avg_relative_return_vs_benchmark']}", f"- Drawdown cartera: {m['portfolio_drawdown']}", "", "## Recomendaciones metodológicas", "- Revisar manualmente patrones de decisiones LOSS antes de cambiar reglas.", "- Analizar blocked_would_have_worked como posible exceso de conservadurismo.", "- Mantener NOT_EVALUABLE visible y mejorar cobertura de datos sin imputar precios."]
    if forward["rows"]:
        lines += ["", "## Resultados", "| Ticker | Ventana | Acción | Estado | Retorno | Benchmark | Relativo |", "|---|---:|---|---|---:|---|---:|"]
        for r in forward["rows"]:
            lines.append(f"| {r['ticker']} | {r['window_months']} | {r['final_action']} | {r['status']} | {r['absolute_return']} | {r['benchmark_used']} | {r['relative_return']} |")
    path = out_root / "forward_test_postmortem.md"
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return path


def persist_performance_outputs(out_root: Path, config: dict[str, Any], perf: dict[str, Any], tracking: list[dict[str, Any]]) -> None:
    write_json(out_root / "performance_snapshot.json", perf)
    ts_fields=["date","run_id","nav","cash_usd","positions_count","daily_return","weekly_return","monthly_return","since_inception_return","composite_benchmark_daily_return"]
    ts={"date":perf["date"],"run_id":perf["run_id"],"nav":perf["nav"],"cash_usd":perf["cash_usd"],"positions_count":perf["positions_count"],"daily_return":perf["daily_return"],"weekly_return":perf["weekly_return"],"monthly_return":perf["monthly_return"],"since_inception_return":perf["since_inception_return"],"composite_benchmark_daily_return":perf["composite_benchmark"]["daily_return"]}
    write_csv(out_root / "performance_timeseries.csv", [ts], ts_fields)
    append_csv_rows(ROOT / config["context_management"]["memory_files"]["performance_memory"], [ts], ts_fields)
    write_csv(out_root / "benchmark_performance.csv", perf["benchmarks"], ["date","ticker","label","price","return_daily","missing_data","source"])
    ledger_path=ROOT / performance_settings(config).get("decision_tracking_ledger","memory/decision_tracking_ledger.jsonl")
    for row in tracking:
        append_jsonl(out_root / "decision_tracking_ledger.jsonl", row)
        append_jsonl(ledger_path, row)
    pending=forward_pending_rows(tracking, config)
    fields=["run_id","decision_date","ticker","window_months","due_date","final_action","reference_price","benchmark_used","status"]
    write_csv(out_root / "forward_test_pending.csv", pending, fields)
    append_csv_rows(ROOT / performance_settings(config).get("forward_test_pending","memory/forward_test_pending.csv"), pending, fields)
    write_csv(out_root / "forward_test_results.csv", [], ["run_id","decision_date","ticker","window_months","due_date","result_return","benchmark_return","relative_return","classification"])
    lines=[f"# Performance DEMO - {perf['date']}","",f"- NAV: USD {perf['nav']:.2f}",f"- Retorno diario: {perf['daily_return']}",f"- Retorno desde inicio: {perf['since_inception_return']}",f"- Benchmark compuesto: {perf['composite_benchmark']}",f"- Benchmarks sin datos: {', '.join(perf['missing_benchmark_data']) or 'ninguno'}","","## Cómo interpretar","NAV = cash + valor de mercado de posiciones DEMO. Los benchmarks faltantes se muestran como missing y no se inventan."]
    (out_root / "performance_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})



def filter_assets_for_scoring(assets: list[dict[str, Any]], universes: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filters = universes.get("filters", universe_builder_settings(config)["filters"])
    eligible = {item["ticker"] for item in universes["investable"]}
    benchmark_tickers = {item["ticker"] for item in universes["benchmarks"]}
    excluded_tickers = {item["ticker"] for item in universes["excluded"]}
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    min_quality = filters.get("min_data_quality", market_data_settings(config).get("min_quality_for_scoring", "MEDIUM"))
    scoring_assets, blocked_rows = [], []
    for asset in assets:
        ticker = asset.get("ticker")
        reason = None
        if ticker in benchmark_tickers and not universe_builder_settings(config).get("allow_benchmarks_in_scoring", False):
            reason = "benchmark_not_investable"
        elif ticker not in eligible:
            reason = "not_in_investable_universe"
        elif ticker in excluded_tickers:
            reason = "excluded_symbol"
        elif safe_float(asset.get("price_close")) is None:
            reason = "no_data"
        elif safe_float(asset.get("price_close")) < float(filters.get("min_price", 0)):
            reason = "price_below_minimum"
        elif safe_float(asset.get("avg_volume_usd")) is None:
            reason = "no_data"
        elif safe_float(asset.get("avg_volume_usd")) < float(filters.get("min_avg_volume_usd", 0)):
            reason = "low_liquidity"
        elif order.get(asset.get("data_quality", "LOW"), 0) < order.get(min_quality, 1) or not has_sufficient_data_for_scoring(asset, config):
            reason = "insufficient_data_quality"
        if reason:
            blocked_rows.append({"ticker": ticker, "reason": reason})
        else:
            scoring_assets.append(asset)
    return scoring_assets, {"blocked_before_scoring": blocked_rows}


def build_universe_coverage_report(universes: dict[str, Any], assets: list[dict[str, Any]], scoring_assets: list[dict[str, Any]], candidates: list[dict[str, Any]], pre_scoring: dict[str, Any], raw_quality: dict[str, Any]) -> dict[str, Any]:
    loaded = {a["ticker"] for a in universes["investable"]} | {a["ticker"] for a in universes["excluded"]}
    asset_by_ticker = {a.get("ticker"): a for a in assets}
    blocked_reasons = [r.get("reason") or r.get("block_reason") for r in pre_scoring.get("blocked_before_scoring", [])] + [e.get("block_reason") for e in universes["excluded"]]
    return {
        "mode": universes["mode"],
        "total_assets_loaded": len(loaded),
        "catalog_assets_loaded": universes.get("catalog_assets_loaded", 0),
        "eligible_assets": len(universes["investable"]),
        "blocked_assets": len(universes["excluded"]) + len(pre_scoring.get("blocked_before_scoring", [])),
        "assets_without_data": sum(1 for t in loaded if t not in asset_by_ticker) + blocked_reasons.count("no_data"),
        "assets_with_low_liquidity": blocked_reasons.count("low_liquidity"),
        "assets_without_provider_support": len(raw_quality.get("provider_errors", [])),
        "assets_sent_to_scoring": len(scoring_assets),
        "assets_sent_to_research": len(candidates),
        "blocked_reason_counts": {reason: blocked_reasons.count(reason) for reason in sorted(set(filter(None, blocked_reasons)))},
    }


def write_universe_snapshots(out_root: Path, universes: dict[str, Any]) -> dict[str, str]:
    fields = ["ticker", "name", "country", "market", "exchange", "currency", "instrument_type", "sector", "industry", "preferred_data_provider", "eligible_for_investment", "eligible_as_benchmark", "min_liquidity_required_usd", "analysis_priority", "notes", "block_reason"]
    outputs = {
        "investable_universe_snapshot": universes["investable"],
        "benchmark_universe_snapshot": universes["benchmarks"],
        "excluded_universe_snapshot": universes["excluded"],
    }
    paths = {}
    for name, rows in outputs.items():
        write_json(out_root / f"{name}.json", rows)
        write_csv(out_root / f"{name}.csv", rows, fields)
        paths[name] = str((out_root / f"{name}.json").relative_to(ROOT))
    return paths

def generate_report(path: Path, run_id: str, today: str, config: dict[str, Any], scored: list[dict[str, Any]], finals: list[dict[str, Any]], trades: list[dict[str, Any]], portfolio: dict[str, Any], validation_report: dict[str, Any], performance: dict[str, Any] | None = None, forward_test: dict[str, Any] | None = None, human_review: dict[str, Any] | None = None) -> None:
    lines = [
        f"# Reporte diario DEMO - {today}",
        "",
        f"- Run ID: `{run_id}`",
        f"- Modo confirmado: `{config['system']['mode']}`",
        f"- Órdenes reales habilitadas: `{config['system']['allow_real_orders']}`",
        f"- Fuente de datos: `{market_data_settings(config).get('provider')}`; externas solo si market_data.enabled=true.",
        "- LLMs: solo research_agent opcional; decision_agent y audit_agent siguen mock.",
        f"- Validación de contratos: `{validation_report['status']}` ({validation_report['valid_outputs']}/{validation_report['checked_outputs']} outputs válidos).",
        "",
        "## Estado de cartera",
        f"- Valor cartera DEMO: USD {portfolio['portfolio_value_usd']:.2f}",
        f"- Cash: USD {portfolio['cash_usd']:.2f}",
        f"- Posiciones: {len(portfolio['positions'])}",
        "",
        "## Performance DEMO vs benchmarks",
        f"- NAV: USD {(performance or {}).get('nav', portfolio['portfolio_value_usd']):.2f}",
        f"- Retorno diario: {(performance or {}).get('daily_return')}",
        f"- Retorno desde inicio: {(performance or {}).get('since_inception_return')}",
        f"- Benchmark compuesto diario: {((performance or {}).get('composite_benchmark') or {}).get('daily_return')}",
        f"- Benchmarks configurados: {', '.join((performance or {}).get('composite_benchmark', {}).get('weights', {}).keys()) if performance else 'SPY, QQQ, EWZ, ARGT, BIL'}",
        f"- Datos faltantes de benchmark visibles: {', '.join((performance or {}).get('missing_benchmark_data', [])) if performance else '-'}",
        "",
        "## Forward-test y post-mortem",
        f"- Estado: {(forward_test or {}).get('status', 'no calculado')}",
        f"- Ventanas vencidas: {((forward_test or {}).get('metrics') or {}).get('expired_windows')}",
        f"- Hit rate: {((forward_test or {}).get('metrics') or {}).get('hit_rate')}",
        "- Recomendaciones metodológicas: informativas; no modifican config ni reglas humanas.",
        "",
        "## Revisión humana metodológica",
        f"- Versión metodológica actual: `{(human_review or {}).get('current_methodology_version', 'METHODOLOGY-000')}`",
        f"- Pendientes: `{((human_review or {}).get('counts') or {}).get('PENDING', 0)}`",
        f"- Aprobadas: `{((human_review or {}).get('counts') or {}).get('APPROVED', 0)}`",
        f"- Rechazadas: `{((human_review or {}).get('counts') or {}).get('REJECTED', 0)}`",
        f"- Requieren más evidencia: `{((human_review or {}).get('counts') or {}).get('NEEDS_MORE_EVIDENCE', 0)}`",
        f"- Cola: `{((human_review or {}).get('files') or {}).get('queue', 'memory/human_review_queue.jsonl')}`",
        f"- Cambios propuestos: `{(human_review or {}).get('proposed_changes', 'no generado')}`",
        "- Las aprobaciones generan propuestas versionadas; no modifican config automáticamente.",
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
    lines += ["", "## Contratos Fase 2", "- Los outputs críticos se validan contra schemas versionados en `schemas/`.", "- Si un contrato crítico falla, la corrida termina con un error explícito antes de persistir el resultado inválido."]
    lines += ["", "## Limitaciones Fase 8", "- Datos reales solo si config los habilita; fixtures siguen default y fallback.", "- decision_agent y audit_agent siguen mock.", "- No hay broker ni posibilidad de órdenes reales."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_output(name: str, value: Any, schema_name: str, report: dict[str, Any], critical: bool = True) -> None:
    schema = load_schema(SCHEMA_DIR / schema_name)
    errors = validate_schema(value, schema)
    entry = {"name": name, "schema": schema_name, "valid": not errors, "errors": errors}
    report["results"].append(entry)
    if errors and critical:
        raise SchemaValidationError(f"Output crítico inválido: {name}: " + "; ".join(errors))


def validate_output_list(name: str, rows: list[dict[str, Any]], schema_name: str, report: dict[str, Any], critical: bool = True) -> None:
    for idx, row in enumerate(rows):
        validate_output(f"{name}[{idx}]", row, schema_name, report, critical)


def summarize_validation_report(report: dict[str, Any]) -> dict[str, Any]:
    checked = len(report["results"])
    valid = sum(1 for item in report["results"] if item["valid"])
    report["checked_outputs"] = checked
    report["valid_outputs"] = valid
    report["invalid_outputs"] = checked - valid
    report["status"] = "VALID" if report["invalid_outputs"] == 0 else "INVALID"
    return report


def build_data_quality_report(scored: list[dict[str, Any]], run_id: str, today: str, normalized_assets: list[dict[str, Any]] | None = None, raw_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    assets = normalized_assets if normalized_assets is not None else scored
    provider = (raw_payload or {}).get("provider", "fixture")
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    flagged = []
    complete, missing, estimated, blocked = [], [], [], []
    provider_errors = (raw_payload or {}).get("errors", [])
    for asset in assets:
        quality = asset.get("data_quality", "LOW")
        counts[quality] = counts.get(quality, 0) + 1
        alerts = list(asset.get("alerts", []))
        missing_fields = asset.get("missing_fields", [])
        estimated_fields = asset.get("estimated_fields", [])
        if missing_fields:
            alerts.append("MISSING_MARKET_DATA")
            missing.append({"ticker": asset["ticker"], "fields": missing_fields})
        if estimated_fields:
            alerts.append("ESTIMATED_FIELDS")
            estimated.append({"ticker": asset["ticker"], "fields": estimated_fields})
        if quality == "LOW" or missing_fields or not has_sufficient_data_for_scoring(asset, {"market_data": {"min_quality_for_scoring": "MEDIUM"}}):
            blocked.append(asset["ticker"])
        if alerts:
            flagged.append({"ticker": asset["ticker"], "alerts": sorted(set(alerts))})
        if not missing_fields and quality in {"HIGH", "MEDIUM"}:
            complete.append(asset["ticker"])
    return {
        "run_id": run_id,
        "date": today,
        "source": "local_fixture" if provider == "fixture" else provider,
        "total_assets_checked": len(assets),
        "quality_counts": counts,
        "flagged_assets": flagged,
        "external_sources_used": provider != "fixture",
        "complete_assets": complete,
        "missing_data": missing,
        "estimated_data": estimated,
        "provider_errors": provider_errors,
        "blocked_assets": sorted(set(blocked)),
        "investable_assets_with_sufficient_data": sorted(set(complete) - set(blocked)),
        "investable_assets_blocked": sorted(set(blocked)),
        "benchmarks_available": [],
        "benchmarks_missing": [],
        "excluded_symbols": [],
        "data_timestamp_utc": (raw_payload or {}).get("fetched_at", utc_now().isoformat()),
    }


def build_memory_update(run_id: str, today: str, decisions: list[dict[str, Any]], audits: list[dict[str, Any]], finals: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for decision, audit, final in zip(decisions, audits, finals):
        items.append({"ticker": decision["ticker"], "fact_type": "demo_decision", "summary": f"{decision['decision']} / {audit['audit_result']} / {final['final_decision']}", "source_run_id": run_id, "verified": False})
    return {"run_id": run_id, "date": today, "update_status": "MOCK_RECORDED", "items": items, "human_readable_summary": "Actualización de memoria externa Fase 8; datos de mercado pueden ser fixture o proveedor explícito según data_quality_report."}


def update_external_memory(config: dict[str, Any], run_id: str, today: str, memory: dict[str, str], scored: list[dict[str, Any]], decisions: list[dict[str, Any]], audits: list[dict[str, Any]], finals: list[dict[str, Any]], portfolio: dict[str, Any], data_quality_report: dict[str, Any], forward_test: dict[str, Any] | None = None) -> dict[str, Any]:
    before = {key: (ROOT / rel).read_text(encoding="utf-8") if (ROOT / rel).exists() else "" for key, rel in memory.items()}
    changed: list[dict[str, Any]] = []
    mf = {key: ROOT / rel for key, rel in memory.items()}

    write_json(mf["portfolio_state"], portfolio)
    append_markdown_section(mf["project_state"], f"Corrida {run_id}", [f"Fecha: {today}.", "Fase 8 ejecutada en DEMO; datos de cierre controlados, forward-test paper y sin broker.", f"Context packs construidos para agentes futuros desde memoria externa."])
    append_markdown_section(mf["methodology_state"], f"Revisión operativa {run_id}", ["Se mantiene metodología DEMO determinística basada en fixtures locales.", "Los hechos no verificados se registran explícitamente como no verificados."])
    if forward_test is not None:
        fm = forward_test["metrics"]
        append_markdown_section(mf["methodology_state"], f"Recomendaciones forward-test {run_id}", [f"Hit rate observado: {fm['hit_rate']} sobre {fm['evaluable_decisions']} decisiones evaluables; recomendación informativa, no modifica reglas.", f"Bloqueadas que hubieran funcionado: {fm['blocked_would_have_worked']}; bloqueadas que evitaron pérdidas: {fm['blocked_avoided_losses']}.", "No actualizar config_demo.yaml ni reglas humanas automáticamente."])
    append_markdown_section(mf["data_quality_memory"], f"Calidad de datos {run_id}", [f"Fuente: {data_quality_report['source']}.", f"Activos revisados: {data_quality_report['total_assets_checked']}.", f"Conteo por calidad: {data_quality_report['quality_counts']}."])
    append_markdown_section(mf["config_change_log"], f"Digest config {run_id}", [f"sha256: {config_digest(config)}.", "No se registraron credenciales ni integraciones operativas."])
    append_markdown_section(mf["postmortem_memory"], f"Aprendizaje {run_id}", ["Fase 8 evalúa ventanas forward-test vencidas en paper trading; si faltan datos marca NOT_EVALUABLE.", f"Estado forward-test: {forward_test['status'] if forward_test else 'no calculado'}.", f"Métricas: {forward_test['metrics'] if forward_test else {}}."])

    for decision, audit, final in zip(decisions, audits, finals):
        item = {"run_id": run_id, "date": today, "ticker": decision["ticker"], "decision_agent_action": decision["decision"], "audit_result": audit["audit_result"], "final_action": final["final_decision"], "reason": final["reason_for_blocking"] or final["reason_for_adjustment"]}
        append_jsonl(mf["decision_ledger"], item)
        append_jsonl(mf["audit_ledger"], {"run_id": run_id, "date": today, "ticker": audit["ticker"], "audit_result": audit["audit_result"], "main_objections": audit["main_objections"], "value_trap_risk": audit["value_trap_risk"]})
        append_jsonl(mf["asset_thesis_memory"], {"run_id": run_id, "date": today, "ticker": decision["ticker"], "thesis_type": "demo_mock", "summary": decision["main_thesis"], "verified": False, "review_date": final["next_review_date"]})
        if final["final_decision"] in {"BLOCKED", "NEED_MORE_DATA"}:
            append_jsonl(mf["rejected_assets_memory"], {"run_id": run_id, "date": today, "ticker": decision["ticker"], "status": final["final_decision"], "reason": final["reason_for_blocking"] or decision["reason_for_decision"], "review_date": final["next_review_date"]})

    after = {key: (ROOT / rel).read_text(encoding="utf-8") if (ROOT / rel).exists() else "" for key, rel in memory.items()}
    for key, rel in memory.items():
        before_lines = len(before.get(key, "").splitlines())
        after_lines = len(after.get(key, "").splitlines())
        if before.get(key, "") != after.get(key, ""):
            changed.append({"memory_key": key, "path": rel, "line_delta": after_lines - before_lines, "sha256_before": hashlib.sha256(before.get(key, "").encode("utf-8")).hexdigest(), "sha256_after": hashlib.sha256(after.get(key, "").encode("utf-8")).hexdigest()})
    return {"run_id": run_id, "date": today, "summary": f"{len(changed)} archivos de memoria creados o actualizados.", "changes": changed}


def write_memory_diff_markdown(path: Path, memory_diff: dict[str, Any]) -> None:
    lines = [f"# Memory diff - {memory_diff['date']}", "", f"Run ID: `{memory_diff['run_id']}`", "", memory_diff["summary"], "", "| Memoria | Path | Delta líneas |", "|---|---|---:|"]
    for change in memory_diff["changes"]:
        lines.append(f"| {change['memory_key']} | `{change['path']}` | {change['line_delta']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_run_manifest(run_id: str, today: str, config: dict[str, Any], prompts: dict[str, Any], memory: dict[str, str], validation_report: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": run_id, "date": today, "phase": "FASE_8", "mode": config["system"]["mode"], "allow_real_orders": config["system"]["allow_real_orders"], "external_apis_used": llm_enabled(config) or market_data_settings(config).get("enabled"), "llms_used": llm_enabled(config), "broker_connected": False, "schemas_version": "phase2.v1", "prompts_loaded": prompts, "memory_files": memory, "validation_summary": {"status": validation_report.get("status", "PENDING"), "checked_outputs": validation_report.get("checked_outputs", 0), "invalid_outputs": validation_report.get("invalid_outputs", 0)}}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta Fase 8 DEMO con forward-test y post-mortem, sin broker.")
    parser.add_argument("--date", default=utc_now().date().isoformat(), help="Fecha de corrida YYYY-MM-DD")
    args = parser.parse_args()
    today = args.date
    stamp = utc_now().strftime("%H%M%S")
    run_id = f"{today}_demo_phase8_{stamp}"

    config = load_config()
    errors = validate_demo_safety(config) + validate_market_data_safety(config) + validate_llm_safety(config)
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
    universes = resolve_universes(config)
    universe_snapshot_paths = write_universe_snapshots(out_root, universes)
    assets, data_quality_report, snapshot_paths = load_market_data(config, today, run_id, log_path, out_root)
    eligible_tickers = {item["ticker"] for item in universes["investable"]}
    scoring_assets, pre_scoring_report = filter_assets_for_scoring(assets, universes, config)
    scored = sorted([score_asset(asset, config["scoring_weights"]) for asset in scoring_assets], key=lambda row: row["total_score"], reverse=True)
    max_research = int(universe_builder_settings(config)["filters"].get("max_assets_for_research", config["candidate_filters"]["max_candidates_for_research"]))
    candidates = scored[: min(config["candidate_filters"]["max_candidates_for_decision"], max_research)]
    research_outputs = [mock_research(asset, today) for asset in candidates]
    decisions = [mock_decision(asset, config, today) for asset in candidates]
    audits = [mock_audit(asset, decision, config, today) for asset, decision in zip(candidates, decisions)]
    finals = [apply_risk(asset, decision, audit, portfolio, config, today) for asset, decision, audit in zip(candidates, decisions, audits)]
    assets_by_ticker = {a["ticker"]: a for a in scored}
    portfolio, trades = update_portfolio(portfolio, finals, assets_by_ticker, config, run_id, today)
    performance = build_performance(run_id, today, config, portfolio, assets_by_ticker)
    decision_tracking = build_decision_tracking(run_id, today, decisions, audits, finals, assets_by_ticker, config)
    persist_performance_outputs(out_root, config, performance, decision_tracking)
    forward_test = evaluate_forward_tests(run_id, today, config, assets_by_ticker, performance)
    forward_fields = ["evaluation_run_id","source_run_id","decision_date","ticker","window_months","due_date","final_action","initial_price","final_price","benchmark_used","benchmark_initial_price","benchmark_final_price","absolute_return","benchmark_return","relative_return","status","explanation"]
    write_csv(out_root / "forward_test_results.csv", forward_test["rows"], forward_fields)
    write_json(out_root / "forward_test_summary.json", {"date": today, "status": forward_test["status"], "metrics": forward_test["metrics"]})
    postmortem_path = write_forward_postmortem(out_root, run_id, today, forward_test)
    quality_provider = "fixture" if data_quality_report["source"] == "local_fixture" else data_quality_report["source"]
    data_quality_report = build_data_quality_report(scored, run_id, today, scored, {"provider": quality_provider, "fetched_at": data_quality_report.get("data_timestamp_utc"), "errors": data_quality_report.get("provider_errors", [])})
    coverage_report = build_universe_coverage_report(universes, assets, scoring_assets, candidates, pre_scoring_report, data_quality_report)
    data_quality_report["universe_coverage"] = coverage_report
    data_quality_report["snapshot_paths"] = {**snapshot_paths, **universe_snapshot_paths}
    bench_tickers = {b["ticker"] for b in universes["benchmarks"]}
    benchmark_available = {r["ticker"] for r in performance.get("benchmarks", []) if not r.get("missing_data")}
    data_quality_report["benchmarks_available"] = sorted(benchmark_available)
    data_quality_report["benchmarks_missing"] = sorted(bench_tickers - benchmark_available)
    data_quality_report["excluded_symbols"] = sorted({e["ticker"] for e in universes["excluded"]})
    data_quality_report["investable_assets_with_sufficient_data"] = sorted({a["ticker"] for a in scoring_assets})
    data_quality_report["investable_assets_blocked"] = sorted(eligible_tickers - {a["ticker"] for a in scoring_assets})
    memory_update = build_memory_update(run_id, today, decisions, audits, finals)
    human_review = sync_human_review(config, out_root, run_id, today, forward_test)
    memory_diff = update_external_memory(config, run_id, today, memory, scored, decisions, audits, finals, portfolio, data_quality_report, forward_test)
    context_pack_summary = build_context_packs(config, run_id, today, memory, scored, research_outputs, decisions, audits, finals, portfolio, memory_diff, out_root, human_review)
    research_outputs, llm_summary = research_with_optional_llm(config, today, candidates, out_root / "context_packs" / "research.json", log_path)
    if llm_summary["enabled"]:
        context_pack_summary = build_context_packs(config, run_id, today, memory, scored, research_outputs, decisions, audits, finals, portfolio, memory_diff, out_root, human_review)
    append_jsonl(log_path, {"event": "llm_summary", "run_id": run_id, **llm_summary})

    validation_report = {"run_id": run_id, "date": today, "schema_version": "phase2.v1", "results": []}
    validate_output_list("scoring_results", scored, "scoring_output_schema.json", validation_report)
    validate_output_list("research_outputs", research_outputs, "research_output_schema.json", validation_report)
    validate_output_list("mock_decisions", decisions, "decision_agent_output_schema.json", validation_report)
    validate_output_list("mock_audits", audits, "audit_agent_output_schema.json", validation_report)
    validate_output_list("risk_engine_results", finals, "risk_engine_final_decision_schema.json", validation_report)
    validate_output_list("simulated_trades", trades, "simulated_trade_schema.json", validation_report)
    validate_output("portfolio_snapshot", portfolio, "portfolio_snapshot_schema.json", validation_report)
    validate_output("memory_update", memory_update, "memory_update_schema.json", validation_report)
    validate_output("data_quality_report", data_quality_report, "data_quality_report_schema.json", validation_report)
    summarize_validation_report(validation_report)
    manifest = build_run_manifest(run_id, today, config, prompts, memory, validation_report)
    validate_output("run_manifest", manifest, "run_manifest_schema.json", validation_report)
    summarize_validation_report(validation_report)

    write_json(out_root / "run_manifest.json", manifest)
    write_json(out_root / "scoring_results.json", scored)
    write_csv(out_root / "scoring_results.csv", scored, ["ticker", "company", "country", "sector", "data_quality", "total_score", "alerts"])
    write_json(out_root / "mock_research.json", research_outputs)
    write_json(out_root / "mock_decisions.json", decisions)
    write_json(out_root / "mock_audits.json", audits)
    write_json(out_root / "risk_engine_results.json", finals)
    write_csv(out_root / "simulated_trades.csv", trades, ["run_id", "date", "ticker", "action", "amount_usd", "price", "shares", "real_order"])
    write_json(out_root / "portfolio_snapshot.json", portfolio)
    write_json(out_root / "performance_snapshot.json", performance)
    write_json(out_root / "memory_update.json", memory_update)
    write_json(out_root / "memory_diff.json", memory_diff)
    write_memory_diff_markdown(out_root / "memory_diff.md", memory_diff)
    write_json(out_root / "context_pack_summary.json", context_pack_summary)
    write_json(out_root / "data_quality_report.json", data_quality_report)
    write_json(out_root / "universe_coverage_report.json", coverage_report)
    write_json(out_root / "validation_report.json", validation_report)
    generate_report(out_root / "daily_report.md", run_id, today, config, scored, finals, trades, portfolio, validation_report, performance, forward_test, human_review)

    portfolio_path = ROOT / config["context_management"]["memory_files"]["portfolio_state"]
    write_json(portfolio_path, portfolio)
    append_jsonl(log_path, {"event": "run_finished", "run_id": run_id, "outputs": str(out_root.relative_to(ROOT)), "trades": len(trades), "positions": len(portfolio["positions"]), "context_packs": context_pack_summary["folder"]})

    print(f"DEMO CONFIRMADA: mode={config['system']['mode']} allow_real_orders={config['system']['allow_real_orders']}")
    print("Broker no usado. Datos reales solo si market_data.mode=real y enabled=true; operaciones siempre simuladas.")
    print(f"Contratos Fase 2 compatibles: {validation_report['status']} ({validation_report['valid_outputs']}/{validation_report['checked_outputs']} outputs válidos)")
    print(f"Run ID: {run_id}")
    print(f"Outputs: {out_root.relative_to(ROOT)}")
    print(f"Log: {log_path.relative_to(ROOT)}")
    print(f"Reporte: {(out_root / 'daily_report.md').relative_to(ROOT)}")
    print(f"Memory diff: {(out_root / 'memory_diff.md').relative_to(ROOT)}")
    print(f"Performance: {(out_root / 'performance_report.md').relative_to(ROOT)}")
    print(f"Universo: modo={universes['mode']} snapshots={universe_snapshot_paths['investable_universe_snapshot']}")
    print(f"Context packs: {(out_root / 'context_packs').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
