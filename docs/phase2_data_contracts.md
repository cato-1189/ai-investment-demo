# Fase 2 — Contratos de datos y validaciones

La Fase 2 agrega contratos formales para los archivos críticos de la DEMO. Un contrato define, en lenguaje verificable, qué campos debe tener cada output, qué tipo de dato espera y qué valores están permitidos.

## Principio operativo

- La DEMO sigue siendo local y simulada.
- No se conectan OpenAI, Claude, Gemini, APIs de mercado ni brokers.
- Las reglas de negocio siguen leyendo límites desde `config/config_demo.yaml`.
- Si un output crítico está incompleto o mal formado, la corrida falla con un error claro antes de escribir un resultado inválido.

## Schemas versionados

Todos los schemas viven en `schemas/` y usan la versión lógica `phase2.v1`:

- `scoring_output_schema.json`: scoring calculado sobre fixtures locales.
- `research_output_schema.json`: research mock/futuro, sin fuentes externas en esta fase.
- `decision_agent_output_schema.json`: recomendación estructurada del decisor mock.
- `audit_agent_output_schema.json`: revisión estructurada del auditor mock.
- `risk_engine_final_decision_schema.json`: decisión final luego de reglas de riesgo.
- `simulated_trade_schema.json`: operación paper; `real_order` debe ser siempre `false`.
- `portfolio_snapshot_schema.json`: estado de cartera simulada.
- `run_manifest_schema.json`: manifiesto de la corrida, fase, seguridad y validación.
- `memory_update_schema.json`: actualización mock de memoria externa.
- `data_quality_report_schema.json`: resumen de calidad de datos de fixtures.

## Validación

`sudo` no es necesario y no se usan dependencias externas. El validador mínimo está en `scripts/schema_validation.py` y soporta el subconjunto de JSON Schema usado por la DEMO: `type`, `required`, `properties`, `items`, `enum`, `minimum`, `maximum` y `minLength`.

Durante `python scripts/run_demo.py`, el flujo valida:

1. Cada fila de scoring.
2. Cada research mock.
3. Cada decisión mock.
4. Cada auditoría mock.
5. Cada decisión final del risk engine.
6. Cada trade simulado.
7. Snapshot de cartera.
8. Update de memoria.
9. Reporte de calidad de datos.
10. Manifiesto de corrida.

La corrida también escribe `validation_report.json` con el estado de cada contrato evaluado.
