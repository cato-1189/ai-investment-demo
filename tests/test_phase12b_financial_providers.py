import json
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import run_demo  # noqa: E402


def test_retry_backoff_simulated(monkeypatch):
    calls = {"n": 0, "sleeps": []}
    def fetcher():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return {"provider": "manual_csv", "fetched_at": "now", "assets": [], "errors": []}
    monkeypatch.setattr(run_demo.time, "sleep", lambda seconds: calls["sleeps"].append(seconds))
    payload = run_demo.run_provider_with_resilience("manual_csv", fetcher, {"retry_attempts": 2, "retry_backoff_seconds": 0.25})
    assert payload["attempts"] == 2
    assert calls["sleeps"] == [0.25]


def test_timeout_simulated():
    def fetcher():
        time.sleep(0.05)
        return {"provider": "manual_csv", "assets": [], "errors": []}
    payload = run_demo.run_provider_with_resilience("manual_csv", fetcher, {"retry_attempts": 1, "provider_timeout_seconds": 0.001})
    assert payload["assets"] == []
    assert "timeout" in payload["errors"][0]["error"]


def test_provider_health_missing_fields_and_snapshot_history(tmp_path, monkeypatch):
    monkeypatch.setattr(run_demo, "ROOT", tmp_path)
    asset = {"ticker": "AAPL", "data_quality": "LOW", "missing_fields": ["provider_date"], "provider": "manual_csv"}
    payload = {"provider": "manual_csv", "fetched_at": "now", "errors": [{"ticker": "AAPL", "error": "x"}]}
    health = run_demo.build_provider_health_report({"provider": "multi_provider", "provider_health": [run_demo.provider_health_from_payload("manual_csv", ["AAPL"], payload, [asset], {"minimum_provider_success_rate": 0.6})]}, "2026-06-29", "run1")
    quality = {"complete_assets": [], "source": "manual_csv"}
    snap = tmp_path / "data" / "financial_snapshots" / "2026-06-29" / "run1"
    run_demo.write_json(snap / "financial_data_normalized.json", [asset])
    run_demo.write_json(snap / "provider_health_report.json", health)
    run_demo.persist_financial_history("2026-06-29", "run1", [asset], quality, health)
    assert health["providers"][0]["provider_status"] == "FAILED"
    assert "provider_date" in health["providers"][0]["fields_missing"]
    assert (snap / "financial_data_normalized.json").exists()
    assert (tmp_path / "memory" / "financial_data_coverage.csv").exists()
    assert (tmp_path / "memory" / "provider_health_history.csv").exists()


def test_stale_visible_and_blocked_by_policy():
    asset = {"ticker": "AAPL", "price_close": 10, "avg_volume_usd": 2_000_000, "data_quality": "HIGH", "missing_fields": [], "provider_date": "2026-01-01", "price_data": {"price_close": {"value": 10}, "avg_volume_usd": {"value": 2000000}}, "fundamentals_data": {}, "ratios_data": {}, "metrics": {"pe_ttm": 10}}
    freshness = run_demo.annotate_freshness(asset, {"max_price_age_days": 7}, "2026-06-29")
    asset["freshness"] = freshness
    asset["stale_data"] = freshness["stale_data"]
    assert freshness["stale_price"] is True
    assert run_demo.has_sufficient_data_for_scoring(asset, {"market_data": {"block_stale_prices": False, "min_quality_for_scoring": "MEDIUM"}}) is True
    assert run_demo.has_sufficient_data_for_scoring(asset, {"market_data": {"block_stale_prices": True, "min_quality_for_scoring": "MEDIUM"}}) is False


def test_benchmarks_outside_scoring_and_no_real_orders():
    cfg = run_demo.load_config()
    universes = {"investable": [{"ticker": "AAPL"}], "benchmarks": [{"ticker": "SPY"}], "excluded": [], "filters": {"min_price": 1, "min_avg_volume_usd": 1, "min_data_quality": "MEDIUM"}}
    assets = [{"ticker": "SPY", "price_close": 1, "avg_volume_usd": 10, "data_quality": "HIGH", "missing_fields": []}]
    scoring, pre = run_demo.filter_assets_for_scoring(assets, universes, cfg)
    assert scoring == []
    assert pre["blocked_before_scoring"][0]["reason"] == "benchmark_not_investable"
    assert run_demo.validate_demo_safety(cfg) == []
    assert cfg["system"]["allow_real_orders"] is False
