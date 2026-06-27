# AI Investment Demo

Sistema DEMO de inversión autónoma en modo paper trading. La Fase 3 mantiene los contratos formales de Fase 2 y agrega memoria externa robusta con Context Packs por agente, sin conectar LLMs reales, APIs externas, datos reales de mercado ni brokers.

## Ejecutar la demo

```bash
python scripts/run_demo.py
```

Opcionalmente se puede fijar la fecha de corrida:

```bash
python scripts/run_demo.py --date 2026-06-27
```

## Ejecutar tests básicos

```bash
python scripts/run_schema_tests.py
```

Los tests usan `unittest` de la librería estándar de Python y no requieren dependencias externas.

## Qué hace la Fase 3

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
11. Actualiza memoria externa separada para proyecto, metodología, decisiones, auditorías, tesis, rechazos, performance, calidad de datos, overrides y configuración.
12. Genera `memory_diff.json` y `memory_diff.md` por corrida.
13. Genera Context Packs específicos para `research`, `decision`, `audit`, `risk_orchestrator`, `report` y `learning_postmortem`.
14. Valida outputs críticos contra schemas versionados en `schemas/`.
15. Genera outputs fechados, reporte humano, reporte de validación y log de ejecución.

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

## Memoria externa y Context Packs

La documentación operativa está en `docs/external_memory_phase3.md`. La lista de archivos persistentes vive en `config/config_demo.yaml` bajo `context_management.memory_files`. La corrida no depende del historial del chat: crea/actualiza memoria externa y luego arma paquetes compactos por rol.

Los límites de contexto se controlan desde `context_management.context_pack_limits`; cada pack incluye una estimación local de tokens y un flag `within_limit`.

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
- `memory_update.json`: actualización de memoria compatible con schema de Fase 2.
- `memory_diff.json` y `memory_diff.md`: resumen auditable de cambios por archivo de memoria.
- `context_pack_summary.json`: rutas y límites de los packs generados.
- `context_packs/*.json`: paquetes mínimos por agente futuro.
- `data_quality_report.json`: calidad de datos de fixtures.
- `validation_report.json`: resultado de validaciones de contratos.
- `daily_report.md`: reporte humano básico.

Los logs se guardan en `logs/<run_id>.jsonl`.

## Limitaciones de Fase 3

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

## Para pasar a Fase 4

Antes de avanzar se requiere aprobación explícita. La siguiente fase debería definir qué integración se habilita primero, cómo se gestionan credenciales, qué proveedores se permiten, cómo se monitorean costos, cómo se validará calidad de datos reales y cómo se usarán los Context Packs antes de cualquier llamada a LLM.
