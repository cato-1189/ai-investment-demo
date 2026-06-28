import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

import run_demo  # noqa: E402
from financial_data_providers import empty_record, fetch_yfinance, flatten_record_for_legacy, field  # noqa: E402


def test_yfinance_disabled_by_default():
    payload = fetch_yfinance([{"ticker": "AAPL"}], "2026-06-27", {})
    assert payload["assets"] == []
    assert payload["errors"][0]["provider"] == "yfinance"
    assert "deshabilitado" in payload["errors"][0]["error"]


def test_yfinance_enabled_explicitly_normalizes(monkeypatch):
    class FakeSeries:
        def __init__(self, value): self.value = value
        @property
        def iloc(self): return self
        def __getitem__(self, idx): return self.value
    class FakeHist:
        empty = False
        def dropna(self, how=None): return self
        def tail(self, n): return self
        def __contains__(self, name): return name in {"Close", "Volume", "Adj Close"}
        def __getitem__(self, name): return FakeSeries({"Close": 200.0, "Volume": 1000, "Adj Close": 199.0}[name])
    class FakeTicker:
        def __init__(self, symbol): self.symbol = symbol
        def history(self, period, auto_adjust): return FakeHist()
        def get_info(self): return {"currency": "USD", "exchange": "NMS", "sector": "Technology", "industry": "Consumer Electronics", "marketCap": 1, "totalRevenue": 2, "trailingPE": 20}
    class FakeYF:
        Ticker = FakeTicker
    monkeypatch.setattr(run_demo.importlib if hasattr(run_demo, 'importlib') else __import__('importlib'), 'import_module', lambda name: FakeYF)
    import financial_data_providers
    monkeypatch.setattr(financial_data_providers.importlib, 'import_module', lambda name: FakeYF)
    payload = fetch_yfinance([{"ticker": "AAPL"}], "2026-06-27", {"enable_yfinance_provider": True})
    row = payload["assets"][0]
    assert row["price_data"]["close"]["value"] == 200.0
    legacy = flatten_record_for_legacy(row)
    assets, _ = run_demo.normalize_market_data({**payload, "assets": [legacy]}, {"market_data": {"required_fields_for_scoring": ["price_data.close", "price_data.volume"]}}, "2026-06-27")
    assert assets[0]["financial_data"]["metadata_data"]["currency"]["value"] == "USD"
    assert assets[0]["scoring_readiness"]["price_ready"] is True


def test_missing_required_financial_field_blocks_asset():
    rec = empty_record("AAPL", "yfinance", "now")
    rec["price_data"]["close"] = field(200.0, "yfinance", "now")
    legacy = flatten_record_for_legacy(rec)
    assets, _ = run_demo.normalize_market_data({"provider": "yfinance", "assets": [legacy], "errors": []}, {"market_data": {"required_fields_for_scoring": ["price_data.close", "price_data.volume"], "fail_if_required_financial_fields_missing": True}}, "2026-06-27")
    assert "price_data.volume" in assets[0]["missing_fields"]
    assert run_demo.has_sufficient_data_for_scoring(assets[0], {"market_data": {"required_fields_for_scoring": ["price_data.close", "price_data.volume"]}}) is False


def test_benchmarks_outside_scoring_with_financial_layer():
    cfg = run_demo.load_config()
    universes = run_demo.resolve_universes(cfg)
    benchmark = {**universes["benchmarks"][0], "price_close": 100, "avg_volume_usd": 10_000_000, "metrics": {"pe_ttm": 15, "ev_ebitda": 9, "fcf_yield": .04, "roe": .1, "revenue_growth": .04, "net_debt_ebitda": 2, "momentum_6m": 0, "drawdown_52w": -.1}, "data_quality": "HIGH", "missing_fields": []}
    scoring, pre = run_demo.filter_assets_for_scoring([benchmark], universes, cfg)
    assert scoring == []
    assert pre["blocked_before_scoring"][0]["reason"] == "benchmark_not_investable"


def test_real_order_false_and_no_broker_in_trades(tmp_path):
    proc = __import__('subprocess').run([sys.executable, str(ROOT / "scripts" / "run_demo.py"), "--date", "2026-06-27"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    out = next(ROOT / line.split("Outputs: ", 1)[1] for line in proc.stdout.splitlines() if line.startswith("Outputs: "))
    manifest = json.loads((out / "run_manifest.json").read_text())
    trades = (out / "simulated_trades.csv").read_text()
    assert manifest["broker_connected"] is False
    assert manifest["allow_real_orders"] is False
    assert "True" not in trades
