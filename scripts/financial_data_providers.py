"""Financial data provider layer for DEMO/paper-trading runs.

All providers are read-only data providers. They never connect to brokers and never
create orders. Yahoo/yfinance is best-effort and unofficial.
"""
from __future__ import annotations

import datetime as dt
import importlib
from typing import Any

FIELD_CATEGORIES = {
    "price_data": ["close", "volume", "adjusted_close"],
    "fundamentals_data": ["market_cap", "revenue", "net_income", "ebitda", "eps", "total_assets", "total_liabilities", "equity", "cash", "debt", "operating_cash_flow", "free_cash_flow"],
    "ratios_data": ["pe", "pb", "ps", "ev_ebitda", "debt_equity", "roe", "roa", "dividend_yield"],
    "metadata_data": ["currency", "exchange", "sector", "industry"],
    "corporate_actions": ["dividends", "splits"],
}


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def field(value: Any, provider: str, timestamp: str, *, estimated: bool = False, quality: str | None = None) -> dict[str, Any]:
    missing = value in (None, "", {})
    return {"value": None if missing else value, "provider": provider, "timestamp": timestamp, "is_estimated": estimated, "is_missing": missing, "quality_status": quality or ("MISSING" if missing else "OK")}


def empty_record(ticker: str, provider: str, timestamp: str, error: str | None = None) -> dict[str, Any]:
    record = {"ticker": ticker, "provider": provider, "timestamp": timestamp, "price_data": {}, "fundamentals_data": {}, "ratios_data": {}, "metadata_data": {}, "corporate_actions": {}, "provider_errors": []}
    for category, names in FIELD_CATEGORIES.items():
        for name in names:
            record[category][name] = field(None, provider, timestamp)
    if error:
        record["provider_errors"].append({"provider": provider, "ticker": ticker, "error": error})
    return record


def fetch_yfinance(universe: list[dict[str, Any]], today: str, settings: dict[str, Any]) -> dict[str, Any]:
    provider = "yfinance"
    timestamp = utc_now_iso()
    if not settings.get("enable_yfinance_provider", False):
        return {"provider": provider, "as_of_date": today, "fetched_at": timestamp, "assets": [], "errors": [{"provider": provider, "error": "yfinance deshabilitado explícitamente por configuración"}]}
    yf = importlib.import_module("yfinance")
    assets: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in universe:
        ticker = item["ticker"] if isinstance(item, dict) else str(item)
        symbol = (item.get("provider_symbol") if isinstance(item, dict) else None) or ticker
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d", auto_adjust=False)
            info = t.get_info() if hasattr(t, "get_info") else getattr(t, "info", {})
            last = hist.dropna(how="all").tail(1) if hasattr(hist, "dropna") else hist.tail(1)
            def hcol(name: str) -> Any:
                if last.empty or name not in last:
                    return None
                value = last[name].iloc[-1]
                return None if value != value else value
            rec = empty_record(ticker, provider, timestamp)
            rec["provider_symbol"] = symbol
            rec["price_data"].update({
                "close": field(hcol("Close"), provider, timestamp),
                "volume": field(hcol("Volume"), provider, timestamp),
                "adjusted_close": field(hcol("Adj Close"), provider, timestamp),
            })
            mapping = {
                "metadata_data": {"currency": "currency", "exchange": "exchange", "sector": "sector", "industry": "industry"},
                "fundamentals_data": {"market_cap": "marketCap", "revenue": "totalRevenue", "net_income": "netIncomeToCommon", "ebitda": "ebitda", "eps": "trailingEps", "total_assets": "totalAssets", "total_liabilities": "totalLiab", "equity": "totalStockholderEquity", "cash": "totalCash", "debt": "totalDebt", "operating_cash_flow": "operatingCashflow", "free_cash_flow": "freeCashflow"},
                "ratios_data": {"pe": "trailingPE", "pb": "priceToBook", "ps": "priceToSalesTrailing12Months", "ev_ebitda": "enterpriseToEbitda", "debt_equity": "debtToEquity", "roe": "returnOnEquity", "roa": "returnOnAssets", "dividend_yield": "dividendYield"},
            }
            for category, m in mapping.items():
                for out, key in m.items():
                    rec[category][out] = field(info.get(key), provider, timestamp)
            assets.append(rec)
        except Exception as exc:
            msg = str(exc)
            errors.append({"provider": provider, "ticker": ticker, "error": msg})
            assets.append(empty_record(ticker, provider, timestamp, msg))
    return {"provider": provider, "as_of_date": today, "fetched_at": timestamp, "assets": assets, "errors": errors, "best_effort": True, "official_api": False}


def flatten_record_for_legacy(record: dict[str, Any]) -> dict[str, Any]:
    def value(cat: str, name: str) -> Any:
        return record.get(cat, {}).get(name, {}).get("value")
    close = value("price_data", "close")
    volume = value("price_data", "volume")
    return {
        "ticker": record["ticker"],
        "provider": record.get("provider", "unknown"),
        "provider_symbol": record.get("provider_symbol", record["ticker"]),
        "raw": {"Date": record.get("timestamp"), "Close": close, "Volume": volume, "Adj Close": value("price_data", "adjusted_close"), "Currency": value("metadata_data", "currency")},
        "financial_data": record,
    }
