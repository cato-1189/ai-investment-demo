# AI Investment Demo

Sistema DEMO de inversión autónoma en modo paper trading. La Fase 2 mantiene el flujo de Fase 1 y agrega contratos formales de datos y validaciones para los outputs críticos, sin conectar LLMs reales, APIs externas, datos reales de mercado ni brokers.

## Ejecutar la demo

```bash
python scripts/run_demo.py
```

Opcionalmente se puede fijar la fecha de corrida:

```bash
python scripts/run_demo.py --date 2026-06-27
```

## Ejecutar tests básicos de Fase 2

```bash
python scripts/run_schema_tests.py
```

Los tests usan `unittest` de la librería estándar de Python y no requieren dependencias externas.

## Qué hace la Fase 2

1. Lee `config/config_demo.yaml`.
2. Valida `system.mode: DEMO_PAPER_TRADING`.
3. Valida `system.allow_real_orders: false`.
4. Lee los prompts `.txt` configurados, pero no llama a LLMs.
5. Crea un `run_id` fechable.
6. Usa fixtures locales de `data/fixtures/demo_assets.json`.
7. Calcula scoring determinístico simple.
8. Genera research, decisiones y auditorías mock estructuradas.
9. Aplica reglas determinísticas de riesgo desde config.
10. Actualiza cartera DEMO en `memory/portfolio_state.json`.
11. Valida outputs críticos contra schemas versionados en `schemas/`.
12. Genera outputs fechados, reporte humano, reporte de validación y log de ejecución.

## Contratos y schemas agregados

Los contratos están documentados para lectura humana en `docs/phase2_data_contracts.md` y versionados como `phase2.v1` en `schemas/`:

- `scoring_output_schema.json`: scoring calculado sobre fixtures locales.
- `research_output_schema.json`: output de research mock/futuro.
- `decision_agent_output_schema.json`: recomendación del agente decisor mock.
- `audit_agent_output_schema.json`: revisión del auditor mock.
- `risk_engine_final_decision_schema.json`: decisión final del motor de riesgo.
- `simulated_trade_schema.json`: operación paper simulada; `real_order` debe ser `false`.
- `portfolio_snapshot_schema.json`: estado de cartera DEMO.
- `run_manifest_schema.json`: manifiesto de corrida.
- `memory_update_schema.json`: actualización mock de memoria.
- `data_quality_report_schema.json`: reporte de calidad de datos de fixtures.

## Cómo se validan los outputs

El validador mínimo vive en `scripts/schema_validation.py`. No agrega dependencias: implementa el subconjunto de JSON Schema necesario para esta fase (`type`, `required`, `properties`, `items`, `enum`, `minimum`, `maximum`, `minLength`).

Durante la corrida, `scripts/run_demo.py` valida cada output crítico antes de escribir el resultado final. Si un output crítico está incompleto o mal formado, la corrida termina con un error explícito de contrato. Si todo está correcto, se escribe `validation_report.json` junto al resto de outputs.

## Outputs principales

Cada corrida escribe en:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/
```

Archivos relevantes:

- `run_manifest.json`: modo DEMO, prompts cargados, memoria usada y resumen de validación.
- `scoring_results.json` y `scoring_results.csv`: ranking fixture validado.
- `mock_research.json`: research mock validado.
- `mock_decisions.json`: decisiones mock validadas.
- `mock_audits.json`: auditorías mock validadas.
- `risk_engine_results.json`: reglas aplicadas y decisión final validada.
- `simulated_trades.csv`: operaciones paper; `real_order` siempre es `false`.
- `portfolio_snapshot.json`: cartera simulada al cierre de la corrida.
- `memory_update.json`: actualización mock de memoria.
- `data_quality_report.json`: calidad de datos de fixtures.
- `validation_report.json`: resultado de validaciones de contratos.
- `daily_report.md`: reporte humano básico.

Los logs se guardan en `logs/<run_id>.jsonl`.

## Limitaciones de Fase 2

- No usa OpenAI, Claude ni Gemini.
- No consume APIs externas.
- No usa datos reales de mercado.
- No integra broker.
- No activa GitHub Actions.
- Las decisiones, auditorías y research son mock para validar estructura y trazabilidad.
- El scoring es simple y sirve solo para probar contratos y flujo.
- Los schemas no reemplazan una validación económica real; solo garantizan forma, tipos y valores permitidos.

## Seguridad DEMO

La corrida falla si la configuración no está en `DEMO_PAPER_TRADING` o si `allow_real_orders` no es `false`.

## Para pasar a Fase 3

Antes de avanzar se requiere aprobación explícita. La siguiente fase debería definir qué integración se habilita primero, cómo se gestionan credenciales, qué proveedores se permiten, cómo se monitorean costos y cómo se validará la calidad de datos reales antes de usar cualquier señal en decisiones.
