#!/usr/bin/env python3
"""Tests básicos de contratos para Fase 2 sin dependencias externas."""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
