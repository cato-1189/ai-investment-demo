# AI Investment Demo

Sistema DEMO de inversión autónoma en modo paper trading. La Fase 1 valida el flujo end-to-end sin LLMs reales, sin APIs externas, sin datos reales y sin broker.

## Ejecutar Fase 1

```bash
python scripts/run_demo.py
```

Opcionalmente se puede fijar la fecha de corrida:

```bash
python scripts/run_demo.py --date 2026-06-27
```

## Qué hace la Fase 1

1. Lee `config/config_demo.yaml`.
2. Valida `system.mode: DEMO_PAPER_TRADING`.
3. Valida `system.allow_real_orders: false`.
4. Lee los prompts `.txt` configurados.
5. Crea un `run_id` fechable.
6. Usa fixtures locales de `data/fixtures/demo_assets.json`.
7. Calcula scoring determinístico simple.
8. Genera decisiones y auditorías mock estructuradas.
9. Aplica reglas determinísticas de riesgo desde config.
10. Actualiza cartera DEMO en `memory/portfolio_state.json`.
11. Genera outputs fechados y log de ejecución.

## Outputs principales

Cada corrida escribe en:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/
```

Archivos relevantes:

- `run_manifest.json`: modo DEMO, prompts cargados, memoria usada.
- `scoring_results.json` y `scoring_results.csv`: ranking fixture.
- `mock_decisions.json`: decisiones mock.
- `mock_audits.json`: auditorías mock.
- `risk_engine_results.json`: reglas aplicadas y decisión final.
- `simulated_trades.csv`: operaciones paper; `real_order` siempre es `false`.
- `portfolio_snapshot.json`: cartera simulada al cierre de la corrida.
- `daily_report.md`: reporte humano básico.

Los logs se guardan en `logs/<run_id>.jsonl`.

## Limitaciones de Fase 1

- No usa OpenAI, Claude ni Gemini.
- No consume APIs externas.
- No usa datos reales de mercado.
- No integra broker.
- Las decisiones y auditorías son mock para validar el flujo.
- El scoring es simple y sirve solo para probar trazabilidad.

## Seguridad DEMO

La corrida falla si la configuración no está en `DEMO_PAPER_TRADING` o si `allow_real_orders` no es `false`.
