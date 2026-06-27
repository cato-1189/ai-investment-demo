# AI Investment Demo

Sistema DEMO de inversión autónoma en modo paper trading. La Fase 4 agrega una capa LLM controlada, segura, auditable y reversible sobre los Context Packs de Fase 3. Por defecto todo corre en **mock**, sin credenciales, sin datos reales de mercado, sin broker y sin ejecución real de órdenes.

## Ejecutar en modo mock recomendado

```bash
python scripts/run_demo.py --date 2026-06-27
```

En este modo `config/config_demo.yaml` mantiene `llm.enabled: false`. No se necesita API key y la demo conserva compatibilidad con el comando histórico.

## Ejecutar tests básicos

```bash
python scripts/run_schema_tests.py
```

Los tests usan `unittest` de la librería estándar de Python y no requieren dependencias externas.

## Qué hace la Fase 4

1. Lee `config/config_demo.yaml`.
2. Valida `system.mode: DEMO_PAPER_TRADING` y `system.allow_real_orders: false`.
3. Mantiene `llm.enabled: false` como default.
4. Solo permite activar LLM real si `config/config_demo.yaml` lo declara explícitamente.
5. Solo habilita un agente real de bajo riesgo: `research_agent`.
6. Mantiene `decision_agent` y `audit_agent` en mock.
7. Lee prompts desde archivos `.txt` configurados; no hardcodea prompts.
8. Construye Context Packs por agente antes de cualquier llamada LLM.
9. Si `research_agent` real está habilitado, envía solo el Context Pack del agente y solo hasta `llm.max_candidates_sent` candidatos.
10. Valida la respuesta LLM contra `schemas/research_output_schema.json`.
11. Si la respuesta es inválida, registra el error y aplica fallback a mock o bloqueo según config.
12. Registra cada llamada LLM en `logs/<run_id>.jsonl` con run_id, agente, proveedor, modelo, prompt, context pack, output, validación, error y tokens/costos si el proveedor los informa.
13. Aplica límites de seguridad: API key obligatoria en modo real, timeout, reintentos, máximo de candidatos, costo máximo y fallback.
14. Genera outputs fechados, reporte humano, memoria externa, Context Packs y reporte de validación.

## Configurar mock vs LLM real

### Mock default

No cambiar nada:

```yaml
llm:
  enabled: false
  real_agents: []
```

### LLM real controlado para research_agent

Un humano puede editar manualmente `config/config_demo.yaml`:

```yaml
llm:
  enabled: true
  real_agents: ["research_agent"]
  fallback_to_mock: true
  block_on_invalid_response: false
  max_candidates_sent: 3
  max_retries: 1
  timeout_seconds: 20
  max_cost_usd_per_run: 1.00
```

Luego debe exportar la credencial del proveedor elegido por `agents.research_agent.provider`. La config actual deja preparado `anthropic` para research:

```bash
export ANTHROPIC_API_KEY="..."
python scripts/run_demo.py --date 2026-06-27
```

También queda preparado `openai` como proveedor soportado por la capa técnica, con credencial por variable de entorno:

```bash
export OPENAI_API_KEY="..."
```

Las API keys se leen exclusivamente desde variables de entorno declaradas en `llm.providers.<provider>.api_key_env`. Nunca se guardan en archivos versionados.

## Proveedores preparados

- `anthropic`: usado por `research_agent` en la configuración actual mediante `ANTHROPIC_API_KEY`.
- `openai`: implementado en la capa de proveedor mediante `OPENAI_API_KEY`, listo para configurar en agentes permitidos de fases futuras.

Otros proveedores mencionados en prompts/config, como `google`, no se ejecutan en Fase 4.

## Validación de respuestas LLM

La respuesta del modelo debe ser un objeto JSON. El sistema intenta parsear ese JSON y lo valida contra `schemas/research_output_schema.json`. Si faltan campos, hay enums inválidos o el contenido no es JSON, la llamada queda marcada como inválida en logs.

- Con `fallback_to_mock: true` y `block_on_invalid_response: false`, el sistema vuelve a `mock_research` para ese candidato.
- Con `block_on_invalid_response: true`, la corrida se bloquea con un error explícito.

## Logs auditables

La corrida escribe logs JSONL en:

```text
logs/<run_id>.jsonl
```

Cada evento `llm_call` incluye:

- `run_id`
- `agent`
- `provider`
- `model`
- `prompt_file`
- `prompt_sha256`
- `context_pack`
- `target_ticker`
- `attempt`
- `output`
- `validation`
- `usage`
- `estimated_cost_usd`
- `duration_seconds`
- `error`

## Costos y tokens

Si el proveedor devuelve `usage`, se guarda en el log. Si la capa puede estimar o recibir costo, acumula `estimated_cost_usd` y compara contra `llm.max_cost_usd_per_run`. Si se excede el máximo configurado, la corrida LLM se bloquea. En modo mock no hay costo real.

## Outputs principales

Cada corrida escribe en:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/
```

Archivos relevantes:

- `run_manifest.json`: modo DEMO, prompts cargados, memoria usada, LLMs usados/no usados y resumen de validación.
- `scoring_results.json` y `scoring_results.csv`: ranking fixture validado.
- `mock_research.json`: research final validado; puede contener salida LLM validada para los candidatos habilitados y mock para fallback/resto.
- `mock_decisions.json`: decisiones mock validadas.
- `mock_audits.json`: auditorías mock validadas.
- `risk_engine_results.json`: reglas aplicadas y decisión final validada.
- `simulated_trades.csv`: operaciones paper; `real_order` siempre es `false`.
- `portfolio_snapshot.json`: cartera simulada.
- `memory_update.json`, `memory_diff.json`, `memory_diff.md`: memoria externa y cambios auditables.
- `context_pack_summary.json` y `context_packs/*.json`: paquetes mínimos por agente.
- `validation_report.json`: resultado de validaciones de contratos.
- `daily_report.md`: reporte humano básico.

## Limitaciones de Fase 4

- No consume datos reales de mercado.
- No integra broker.
- No activa GitHub Actions.
- No ejecuta órdenes reales.
- `real_order` permanece siempre en `false`.
- Solo `research_agent` puede usar LLM real.
- `decision_agent` y `audit_agent` siguen mock.
- No se envía historial completo a modelos; se usan Context Packs compactos.
- La salida LLM no constituye recomendación financiera real.

## Para Fase 5

Fase 5 debería definir si se habilita otro agente real, cómo mejorar estimación de costos por proveedor/modelo, cómo agregar evaluación humana de calidad de research, y cómo preparar datos externos sin romper las restricciones de no broker/no órdenes reales hasta que exista aprobación explícita.
