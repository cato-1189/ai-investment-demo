#!/usr/bin/env python3
"""Tests básicos de contratos para Fase 2 sin dependencias externas."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from schema_validation import SchemaValidationError, assert_valid, load_schema, validate_schema
import run_demo

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"


class SchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = run_demo.load_config()
        cls.today = "2026-06-27"
        cls.portfolio = {"portfolio_value_usd": 50000, "cash_usd": 50000, "positions": []}
        cls.asset = run_demo.score_asset(run_demo.read_json(run_demo.FIXTURE_PATH)[0], cls.config["scoring_weights"])
        cls.research = run_demo.mock_research(cls.asset, cls.today)
        cls.decision = run_demo.mock_decision(cls.asset, cls.config, cls.today)
        cls.audit = run_demo.mock_audit(cls.asset, cls.decision, cls.config, cls.today)
        cls.final = run_demo.apply_risk(cls.asset, cls.decision, cls.audit, cls.portfolio, cls.config, cls.today)
        cls.manifest = run_demo.build_run_manifest("test_run", cls.today, cls.config, {}, {}, {"status": "VALID", "checked_outputs": 0, "invalid_outputs": 0})
        cls.quality = run_demo.build_data_quality_report([cls.asset], "test_run", cls.today)
        cls.memory = run_demo.build_memory_update("test_run", cls.today, [], [], [])

    def assertSchemaValid(self, schema_name: str, value) -> None:
        assert_valid(value, load_schema(SCHEMA_DIR / schema_name), schema_name)

    def test_expected_outputs_validate(self) -> None:
        cases = [
            ("scoring_output_schema.json", self.asset),
            ("research_output_schema.json", self.research),
            ("decision_agent_output_schema.json", self.decision),
            ("audit_agent_output_schema.json", self.audit),
            ("risk_engine_final_decision_schema.json", self.final),
            ("run_manifest_schema.json", self.manifest),
            ("memory_update_schema.json", self.memory),
            ("data_quality_report_schema.json", self.quality),
        ]
        for schema_name, value in cases:
            with self.subTest(schema=schema_name):
                self.assertSchemaValid(schema_name, value)

    def test_external_memory_update_and_context_packs(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            temp_root = Path(tmp)
            config = dict(self.config)
            context = dict(self.config["context_management"])
            context["memory_files"] = {key: str(temp_root / Path(rel).name) for key, rel in self.config["context_management"]["memory_files"].items()}
            config["context_management"] = context
            memory = run_demo.ensure_memory(config, "test_run", self.today)
            scored = [self.asset]
            decisions = [self.decision]
            audits = [self.audit]
            finals = [self.final]
            portfolio = dict(self.portfolio, positions=[], portfolio_metrics={"number_of_positions": 0, "cash_weight": 1.0}, mode=config["system"]["mode"], base_currency=config["system"]["base_currency"], initial_capital_usd=config["system"]["initial_capital_usd"], last_update=self.today, run_id="test_run", open_risks=[], human_overrides_active=[])
            quality = run_demo.build_data_quality_report(scored, "test_run", self.today)
            diff = run_demo.update_external_memory(config, "test_run", self.today, memory, scored, decisions, audits, finals, portfolio, quality)
            self.assertTrue(diff["changes"])
            out_root = temp_root / "outputs" / "2026-06-27" / "test_run"
            summary = run_demo.build_context_packs(config, "test_run", self.today, memory, scored, [self.research], decisions, audits, finals, portfolio, diff, out_root)
            expected_agents = {"research", "decision", "audit", "risk_orchestrator", "report", "learning_postmortem"}
            self.assertEqual(set(summary["packs"]), expected_agents)
            for agent, info in summary["packs"].items():
                self.assertTrue((ROOT / info["path"]).exists(), agent)
                self.assertTrue(info["within_limit"], agent)

    def test_invalid_output_is_rejected(self) -> None:
        schema = load_schema(SCHEMA_DIR / "decision_agent_output_schema.json")
        invalid = dict(self.decision)
        invalid.pop("decision")
        with self.assertRaises(SchemaValidationError):
            assert_valid(invalid, schema, "decision_agent_output")

    def test_validator_marks_malformed_enum(self) -> None:
        schema = load_schema(SCHEMA_DIR / "risk_engine_final_decision_schema.json")
        invalid = dict(self.final, final_decision="REAL_BUY")
        errors = validate_schema(invalid, schema)
        self.assertTrue(any("enum" in error for error in errors))


class Phase4LLMTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = run_demo.load_config()
        self.today = "2026-06-27"
        self.asset = run_demo.score_asset(run_demo.read_json(run_demo.FIXTURE_PATH)[0], self.config["scoring_weights"])

    def _temp_pack_and_log(self, tmp: Path) -> tuple[Path, Path]:
        pack = {
            "run_id": "test_run",
            "date": self.today,
            "agent": "research",
            "sections": [{"name": "current_candidates", "content": [{"ticker": self.asset["ticker"], "company": self.asset["company"]}]}],
        }
        pack_path = tmp / "context_packs" / "research.json"
        run_demo.write_json(pack_path, pack)
        return pack_path, tmp / "llm.jsonl"

    def test_llm_disabled_uses_mock_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp_name:
            pack_path, log_path = self._temp_pack_and_log(Path(tmp_name))
            outputs, summary = run_demo.research_with_optional_llm(self.config, self.today, [self.asset], pack_path, log_path)
        self.assertEqual(summary["mode"], "mock")
        self.assertEqual(outputs[0]["research_status"], "MOCK_PLACEHOLDER")
        self.assertFalse(log_path.exists())

    def test_missing_api_key_fails_clearly_when_enabled(self) -> None:
        config = dict(self.config)
        config["llm"] = {**run_demo.llm_settings(self.config), "enabled": True, "real_agents": ["research_agent"]}
        with self.assertRaisesRegex(run_demo.LLMConfigError, "ANTHROPIC_API_KEY"):
            run_demo.require_api_key(config, "research_agent")

    def test_invalid_llm_response_falls_back_to_mock_and_logs_validation(self) -> None:
        config = dict(self.config)
        config["llm"] = {**run_demo.llm_settings(self.config), "enabled": True, "real_agents": ["research_agent"], "max_retries": 0, "fallback_to_mock": True, "block_on_invalid_response": False}
        def fake_provider(*args, **kwargs):
            return {"output_text": '{"ticker":"BAD"}', "usage": {"input_tokens": 10, "output_tokens": 3}, "estimated_cost_usd": 0.01}
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp_name:
            tmp = Path(tmp_name)
            pack_path, log_path = self._temp_pack_and_log(tmp)
            import os
            old = os.environ.get("ANTHROPIC_API_KEY")
            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                outputs, summary = run_demo.research_with_optional_llm(config, self.today, [self.asset], pack_path, log_path, fake_provider)
            finally:
                if old is None:
                    os.environ.pop("ANTHROPIC_API_KEY", None)
                else:
                    os.environ["ANTHROPIC_API_KEY"] = old
            self.assertEqual(outputs[0]["research_status"], "MOCK_PLACEHOLDER")
            self.assertEqual(summary["fallbacks"], 1)
            self.assertIn("llm_call", log_path.read_text(encoding="utf-8"))
            self.assertIn("valid", log_path.read_text(encoding="utf-8"))

    def test_valid_llm_response_is_schema_validated(self) -> None:
        config = dict(self.config)
        config["llm"] = {**run_demo.llm_settings(self.config), "enabled": True, "real_agents": ["research_agent"], "max_retries": 0}
        valid = run_demo.mock_research(self.asset, self.today)
        valid["research_status"] = "READY_FOR_FUTURE_LLM"
        def fake_provider(*args, **kwargs):
            return {"output_text": json.dumps(valid), "usage": {"input_tokens": 10, "output_tokens": 30}, "estimated_cost_usd": 0.01}
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp_name:
            pack_path, log_path = self._temp_pack_and_log(Path(tmp_name))
            import os
            old = os.environ.get("ANTHROPIC_API_KEY")
            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                outputs, summary = run_demo.research_with_optional_llm(config, self.today, [self.asset], pack_path, log_path, fake_provider)
            finally:
                if old is None:
                    os.environ.pop("ANTHROPIC_API_KEY", None)
                else:
                    os.environ["ANTHROPIC_API_KEY"] = old
        self.assertEqual(outputs[0]["research_status"], "READY_FOR_FUTURE_LLM")
        self.assertEqual(summary["calls"], 1)


class Phase5MarketDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = run_demo.load_config()
        self.today = "2026-06-27"

    def test_fixture_mode_is_default_and_generates_snapshots(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp_name:
            tmp = Path(tmp_name)
            config = dict(self.config)
            md = {**run_demo.market_data_settings(config), "snapshot_folder": str(tmp / "snapshots")}
            config["market_data"] = md
            out_root = tmp / "out"
            log_path = tmp / "run.jsonl"
            assets, quality, paths = run_demo.load_market_data(config, self.today, "test_run", log_path, out_root)
            self.assertTrue(assets)
            self.assertFalse(quality["external_sources_used"])
            self.assertEqual(quality["source"], "local_fixture")
            self.assertTrue((ROOT / paths["raw"]).exists())
            self.assertTrue((ROOT / paths["normalized"]).exists())

    def test_real_provider_disabled_validation_is_explicit(self) -> None:
        config = dict(self.config)
        config["market_data"] = {**run_demo.market_data_settings(config), "mode": "real", "enabled": False, "provider": "stooq_csv"}
        self.assertTrue(any("enabled=true" in e for e in run_demo.validate_market_data_safety(config)))

    def test_missing_data_blocks_asset_in_quality_report(self) -> None:
        raw = {"provider": "stooq_csv", "as_of_date": self.today, "fetched_at": "2026-06-27T00:00:00+00:00", "assets": [{"ticker": "ACME", "provider_symbol": "aapl.us", "raw": {"Symbol": "ACME", "Date": "N/D", "Close": "N/D", "Volume": "N/D"}}], "errors": []}
        assets, quality = run_demo.normalize_market_data(raw, self.config, self.today)
        self.assertEqual(assets[0]["data_quality"], "LOW")
        self.assertIn("ACME", quality["blocked_assets"])
        self.assertTrue(quality["missing_data"])

    def test_quality_report_tracks_estimated_fundamentals(self) -> None:
        raw = {"provider": "stooq_csv", "as_of_date": self.today, "fetched_at": "2026-06-27T00:00:00+00:00", "assets": [{"ticker": "ACME", "provider_symbol": "aapl.us", "raw": {"Symbol": "ACME", "Date": "2026-06-26", "Close": "200", "Volume": "1000000"}}], "errors": []}
        assets, quality = run_demo.normalize_market_data(raw, self.config, self.today)
        self.assertEqual(assets[0]["data_quality"], "MEDIUM")
        self.assertTrue(quality["estimated_data"])
        self.assertFalse(quality["blocked_assets"])

    def test_low_quality_asset_is_blocked_by_risk(self) -> None:
        asset = run_demo.score_asset({**run_demo.read_json(run_demo.FIXTURE_PATH)[0], "data_quality": "LOW"}, self.config["scoring_weights"])
        decision = run_demo.mock_decision(asset, self.config, self.today)
        audit = run_demo.mock_audit(asset, decision, self.config, self.today)
        final = run_demo.apply_risk(asset, decision, audit, {"portfolio_value_usd": 50000}, self.config, self.today)
        self.assertEqual(final["final_decision"], "BLOCKED")
        self.assertIn("block_if_data_quality_low", final["risk_rules_triggered"])


class Phase6PerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = run_demo.load_config()
        self.today = "2026-06-27"
        fixture = run_demo.read_json(run_demo.FIXTURE_PATH)
        self.assets = [run_demo.score_asset(a, self.config["scoring_weights"]) for a in fixture]
        self.assets_by_ticker = {a["ticker"]: a for a in self.assets}
        self.portfolio = {"portfolio_value_usd": 50000, "cash_usd": 40000, "positions": [{"ticker": "ACME", "company": "ACME", "country": "US", "sector": "Technology", "shares": 10, "price_close": 100, "market_value_usd": 1000}]}

    def test_nav_marks_positions_to_market(self) -> None:
        p = run_demo.mark_to_market_portfolio(dict(self.portfolio), self.assets_by_ticker)
        expected = 40000 + 10 * self.assets_by_ticker["ACME"]["price_close"]
        self.assertEqual(p["portfolio_value_usd"], round(expected, 2))

    def test_return_calculation(self) -> None:
        self.assertEqual(run_demo.pct_return(110, 100), 0.1)
        self.assertIsNone(run_demo.pct_return(110, None))

    def test_benchmark_snapshot_reports_missing_without_inventing(self) -> None:
        rows, prices = run_demo.benchmark_snapshot(self.config, self.today, self.assets_by_ticker, {})
        tickers = {r["ticker"]: r for r in rows}
        self.assertIn("SPY", tickers)
        self.assertTrue(tickers["ARGT"]["missing_data"])
        self.assertNotIn("ARGT", prices)

    def test_decision_tracking_and_forward_pending(self) -> None:
        asset = self.assets_by_ticker["ACME"]
        decision = run_demo.mock_decision(asset, self.config, self.today)
        audit = run_demo.mock_audit(asset, decision, self.config, self.today)
        final = run_demo.apply_risk(asset, decision, audit, {"portfolio_value_usd": 50000}, self.config, self.today)
        tracking = run_demo.build_decision_tracking("test_run", self.today, [decision], [audit], [final], self.assets_by_ticker, self.config)
        self.assertFalse(tracking[0]["real_order"])
        pending = run_demo.forward_pending_rows(tracking, self.config)
        self.assertEqual({int(r["window_months"]) for r in pending}, {3, 6, 12})
        self.assertTrue(all(r["status"] == "PENDING" for r in pending))

    def test_forward_test_evaluates_expired_and_missing_prices(self) -> None:
        with tempfile.TemporaryDirectory(dir=run_demo.ROOT) as tmp_name:
            tmp = Path(tmp_name).relative_to(run_demo.ROOT)
            config = dict(self.config)
            perf = dict(run_demo.performance_settings(self.config))
            perf["forward_test_pending"] = str(tmp / "pending.csv")
            perf["forward_test_results"] = str(tmp / "results.csv")
            perf["benchmark_prices_file"] = str(tmp / "bench.csv")
            config["performance_tracking"] = perf
            run_demo.write_csv(run_demo.ROOT / perf["forward_test_pending"], [
                {"run_id":"old","decision_date":"2026-01-01","ticker":"ACME","window_months":3,"due_date":"2026-04-01","final_action":"APPROVED","reference_price":100,"benchmark_used":"SPY","status":"PENDING"},
                {"run_id":"old","decision_date":"2026-01-01","ticker":"MISSING","window_months":3,"due_date":"2026-04-01","final_action":"BLOCKED","reference_price":50,"benchmark_used":"SPY","status":"PENDING"},
            ], ["run_id","decision_date","ticker","window_months","due_date","final_action","reference_price","benchmark_used","status"])
            run_demo.write_csv(run_demo.ROOT / perf["benchmark_prices_file"], [{"date":"2026-01-01","ticker":"SPY","label":"S&P","price":500,"return_daily":"","missing_data":False,"source":"test"}, {"date":"2026-01-01","ticker":"QQQ","label":"Nasdaq","price":500,"return_daily":"","missing_data":False,"source":"test"}], ["date","ticker","label","price","return_daily","missing_data","source"])
            assets = {"ACME": {**self.assets_by_ticker["ACME"], "price_close": 120, "country":"US", "sector":"Technology"}}
            summary = run_demo.evaluate_forward_tests("eval", "2026-06-27", config, assets, {"benchmarks":[{"ticker":"SPY","price":550}, {"ticker":"QQQ","price":550}], "risk_metrics":{"drawdown":-0.01}})
            self.assertEqual(summary["metrics"]["expired_windows"], 2)
            by_ticker = {r["ticker"]: r for r in summary["rows"]}
            self.assertEqual(by_ticker["ACME"]["status"], "WIN")
            self.assertEqual(by_ticker["MISSING"]["status"], "NOT_EVALUABLE")
            self.assertEqual(summary["metrics"]["hit_rate"], 1.0)

    def test_demo_safety_no_broker_and_real_order_false(self) -> None:
        self.assertFalse(self.config["system"]["allow_real_orders"])
        self.assertFalse(any("broker" in e.lower() for e in run_demo.validate_demo_safety(self.config)))
        asset = self.assets_by_ticker["ACME"]
        final = {"final_decision": "EXECUTE_BUY_DEMO", "allocated_amount_usd": 1000, "ticker": "ACME"}
        p, trades = run_demo.update_portfolio({"portfolio_value_usd": 50000, "cash_usd": 50000, "positions": []}, [final], self.assets_by_ticker, self.config, "test_run", self.today)
        self.assertTrue(trades)
        self.assertTrue(all(t["real_order"] is False for t in trades))


class Phase6BUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = run_demo.load_config()
        self.today = "2026-06-27"

    def test_benchmark_does_not_enter_scoring_when_not_investable(self) -> None:
        config = dict(self.config)
        config["universe_modes"] = {**self.config["universe_modes"], "default": "test_bench", "test_bench": {"symbols": ["SPY", "AAPL"]}}
        config["market_data"] = {**self.config["market_data"], "universe_mode": "test_bench", "universe": []}
        universes = run_demo.resolve_universes(config)
        self.assertNotIn("SPY", {a["ticker"] for a in universes["investable"]})
        self.assertIn("SPY", {a["ticker"] for a in universes["excluded"]})

    def test_investable_asset_enters_scoring_with_sufficient_data(self) -> None:
        asset = {**run_demo.read_json(run_demo.FIXTURE_PATH)[0], "eligible_for_investment": True, "instrument_type": "common_stock"}
        self.assertTrue(run_demo.has_sufficient_data_for_scoring(asset, self.config))
        scored = run_demo.score_asset(asset, self.config["scoring_weights"])
        self.assertEqual(scored["ticker"], asset["ticker"])

    def test_low_quality_asset_is_blocked_before_scoring(self) -> None:
        asset = {**run_demo.read_json(run_demo.FIXTURE_PATH)[0], "data_quality": "LOW", "missing_fields": ["price_close"]}
        self.assertFalse(run_demo.has_sufficient_data_for_scoring(asset, self.config))

    def test_demo_small_is_default_universe_mode(self) -> None:
        self.assertEqual(run_demo.configured_universe_mode(self.config), "demo_small")
        self.assertEqual({a["ticker"] for a in run_demo.resolve_universes(self.config)["investable"]}, {"AAPL", "MSFT", "MELI", "YPF", "VALE", "PBR"})

    def test_liquid_core_loads_more_assets(self) -> None:
        config = dict(self.config)
        config["market_data"] = {**self.config["market_data"], "universe_mode": "liquid_core", "universe": []}
        self.assertGreater(len(run_demo.resolve_universes(config)["investable"]), len(run_demo.resolve_universes(self.config)["investable"]))

    def test_universe_outputs_separate_investable_and_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp_name:
            tmp = Path(tmp_name)
            paths = run_demo.write_universe_snapshots(tmp, run_demo.resolve_universes(self.config))
            investable = json.loads((tmp / "investable_universe_snapshot.json").read_text(encoding="utf-8"))
            benchmarks = json.loads((tmp / "benchmark_universe_snapshot.json").read_text(encoding="utf-8"))
            self.assertIn("investable_universe_snapshot", paths)
            self.assertFalse({"SPY", "QQQ", "EWZ", "ARGT", "BIL"} & {a["ticker"] for a in investable})
            self.assertTrue({"SPY", "QQQ", "EWZ", "ARGT", "BIL"} <= {a["ticker"] for a in benchmarks})


class Phase6CUniverseBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = run_demo.load_config()
        self.today = "2026-06-27"

    def test_external_catalogs_load_versioned_assets(self) -> None:
        rows = run_demo.load_universe_catalogs(self.config)
        self.assertIn("NVDA", {row["ticker"] for row in rows})
        self.assertTrue(all("catalog_source" in row for row in rows))

    def test_broad_market_loads_catalogs_without_breaking(self) -> None:
        config = dict(self.config)
        config["market_data"] = {**self.config["market_data"], "universe_mode": "broad_market", "universe": []}
        universes = run_demo.resolve_universes(config)
        self.assertGreaterEqual(universes["catalog_assets_loaded"], 20)
        self.assertIn("PETR4.SA", {asset["ticker"] for asset in universes["investable"]})

    def test_manual_exclusion_stays_out_of_scoring(self) -> None:
        config = dict(self.config)
        config["universe_modes"] = {**self.config["universe_modes"], "excluded_test": {"symbols": ["AAPL", "BRZU"]}}
        config["market_data"] = {**self.config["market_data"], "universe_mode": "excluded_test", "universe": []}
        universes = run_demo.resolve_universes(config)
        self.assertNotIn("BRZU", {asset["ticker"] for asset in universes["investable"]})
        self.assertIn("BRZU", {asset["ticker"] for asset in universes["excluded"]})

    def test_assets_without_sufficient_data_are_blocked_before_scoring(self) -> None:
        universes = run_demo.resolve_universes(self.config)
        bad_asset = {**run_demo.read_json(run_demo.FIXTURE_PATH)[0], "ticker": "AAPL", "missing_fields": ["price_close"], "data_quality": "LOW"}
        scoring_assets, report = run_demo.filter_assets_for_scoring([bad_asset], universes, self.config)
        self.assertFalse(scoring_assets)
        self.assertEqual(report["blocked_before_scoring"][0]["reason"], "insufficient_data_quality")

    def test_max_assets_for_research_is_respected(self) -> None:
        config = dict(self.config)
        config["universe_builder"] = {**self.config["universe_builder"], "filters": {**self.config["universe_builder"]["filters"], "max_assets_for_research": 2}}
        max_research = run_demo.universe_builder_settings(config)["filters"]["max_assets_for_research"]
        scored = [{"ticker": f"T{i}", "total_score": 100 - i} for i in range(5)]
        candidates = scored[: min(config["candidate_filters"]["max_candidates_for_decision"], max_research)]
        self.assertEqual(len(candidates), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
