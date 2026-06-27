# Memoria externa y Context Packs - Fase 3

La Fase 3 conserva el contexto operativo fuera de la ventana de cualquier IA. En vez de depender del historial del chat o de memoria interna de modelos, cada corrida actualiza archivos persistentes bajo `memory/` y genera Context Packs mínimos por rol.

## Archivos de memoria

La lista de memorias se define en `config/config_demo.yaml` bajo `context_management.memory_files`. La corrida crea o actualiza memorias separadas para estado del proyecto, metodología, cartera demo, decisiones, auditorías, tesis por activo, activos rechazados, performance, calidad de datos, overrides humanos y cambios de configuración.

## Actualización por corrida

`scripts/run_demo.py` ejecuta estos pasos:

1. Crea cualquier archivo de memoria faltante desde `memory_templates/` o con un formato mínimo seguro.
2. Ejecuta el flujo DEMO con fixtures locales y outputs mock validados por schemas de Fase 2.
3. Actualiza la cartera demo y los ledgers de memoria con `run_id`, fecha y origen.
4. Marca como no verificados los hechos provenientes del entorno mock.
5. Calcula `memory_diff.json` y `memory_diff.md` comparando hashes y deltas de líneas antes/después de la corrida.

## Context Packs por agente

Los packs se guardan en:

```text
outputs/daily_runs/<fecha>/<run_id>/context_packs/
```

Se generan seis packs:

- `research.json`: metodología resumida, candidatos actuales, tesis por activo y cambios recientes.
- `decision.json`: cartera, research mock, decisiones previas relevantes y overrides humanos.
- `audit.json`: decisiones a auditar, evidencia disponible, memoria de calidad de datos, objeciones previas y rechazos relevantes.
- `risk_orchestrator.json`: cartera, reglas de riesgo/cartera, decisiones finales y overrides.
- `report.json`: resumen de corrida, diff de memoria y performance reciente.
- `learning_postmortem.json`: decisiones, auditorías, resultados de riesgo y cambios de memoria.

## Control de límites

Los límites salen de `context_management.context_pack_limits` en `config/config_demo.yaml`. Cada pack registra `estimated_tokens`, `max_tokens`, `max_items` y `within_limit`. La estimación usa una regla local conservadora, sin tokenizadores externos ni APIs. Si un pack excede límites, se recortan listas por antigüedad hasta cumplir o hasta que no quede contenido reducible.

## Por qué evita pérdida de contexto y reduce tokens

La memoria compacta e incremental permite reconstruir contexto relevante desde archivos persistentes aunque el chat se pierda o cambie el modelo. Los agentes futuros recibirán solo el pack específico de su rol, no todo el historial bruto. Esto reduce tokens, evita duplicación y deja una pista auditable de qué información fue incluida.

## Limitaciones de esta fase

- No llama a OpenAI, Claude ni Gemini.
- No consume APIs externas.
- No usa datos reales de mercado.
- No integra broker ni órdenes reales.
- Los packs preparan handoffs futuros, pero todavía no se envían a LLMs.
