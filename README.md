# AI Investment Demo

Sistema DEMO de inversión autónoma en modo paper trading. La Fase 6B separa formalmente el universo invertible de los benchmarks, mantiene ingesta controlada de datos de cierre, snapshots auditables, data quality report, fallback a fixtures y bloqueo por baja calidad. El sistema sigue sin broker, sin órdenes reales, sin automatización diaria y con `decision_agent`/`audit_agent` reales deshabilitados.

## Ejecutar en modo fixture recomendado

```bash
python scripts/run_demo.py --date 2026-06-27
```

Este es el modo default. No requiere credenciales ni red. Usa `data/fixtures/demo_assets.json`, genera snapshots marcados como fixture y mantiene compatibilidad con el comando histórico.

## Ejecutar tests básicos

```bash
python scripts/run_schema_tests.py
```

Los tests usan `unittest` de la librería estándar. Cubren modo fixture, proveedor real deshabilitado, datos faltantes, snapshots, data quality report, validación de schemas, memoria/context packs, LLM opcional de Fase 4 y bloqueo por baja calidad.

## Configurar fixture vs datos reales

### Fixture/mock default

No cambiar nada:

```yaml
market_data:
  mode: "fixture"
  enabled: false
  provider: "fixture"
```

### Datos reales de cierre bajo decisión humana

Un humano debe editar manualmente `config/config_demo.yaml` y habilitar explícitamente ambas banderas:

```yaml
market_data:
  mode: "real"
  enabled: true
  provider: "stooq_csv"
  fallback_to_fixture: true
  block_on_low_quality: true
```

Luego ejecutar:

```bash
python scripts/run_demo.py --date 2026-06-27
```

`stooq_csv` no requiere API key. Si el proveedor falla, el error se registra en logs; si `fallback_to_fixture: true`, la corrida vuelve a fixtures y lo marca claramente como fixture/mock. Si se desactiva el fallback, el error del proveedor bloquea la corrida.

## Universo invertible vs benchmarks (Fase 6B)

El objetivo del sistema es buscar acciones subvaluadas de EEUU, Brasil y Argentina. Por eso la configuración separa tres listas editables en `config/config_demo.yaml`:

- `investable_universe`: acciones comunes, ADRs, CEDEARs u otros instrumentos accionarios habilitados para análisis.
- `benchmark_universe`: ETFs/proxies como `SPY`, `QQQ`, `EWZ`, `ARGT` y `BIL`, usados solo para comparar performance.
- `excluded_symbols`: instrumentos que no deben entrar al scoring, por ejemplo ETFs apalancados o símbolos fuera de alcance.

Cada activo incluye ticker, nombre, país, mercado, moneda, tipo de instrumento, sector, industria, proveedor preferido, elegibilidad como inversión, elegibilidad como benchmark, liquidez mínima y notas. Los tipos permitidos por default están en `allowed_instrument_types`: `common_stock`, `adr` y `cedear`.

### Modos de universo

`market_data.universe_mode` queda en `demo_small` por default para no romper la demo:

- `demo_small`: universo reducido para pruebas sin credenciales.
- `liquid_core`: universo líquido ampliado de EEUU, Brasil y Argentina.
- `broad_market`: universo amplio configurable, sin intentar cargar automáticamente “todo el mercado”.

Para ampliar el universo sin tocar código, un humano edita `config/config_demo.yaml`: agrega o ajusta activos en `investable_universe` y suma sus tickers al modo deseado dentro de `universe_modes`. Para correr un modo más grande, cambiar `market_data.universe_mode: "liquid_core"` o `"broad_market"`.

### Protección contra benchmarks como candidatos

Por default `SPY`, `QQQ`, `EWZ`, `ARGT` y `BIL` tienen `eligible_for_investment: false` y `eligible_as_benchmark: true`. Si accidentalmente aparecen en un modo de universo, quedan bloqueados como no invertibles y no pasan al scoring. Solo podrían ser candidatos si un humano cambia explícitamente su metadata para permitir inversión, lo cual no es el default de Fase 6B.

## Snapshots auditables

Cada corrida escribe snapshots en dos lugares:

```text
data/snapshots/<YYYY-MM-DD>/<run_id>/raw_market_data.json
 data/snapshots/<YYYY-MM-DD>/<run_id>/normalized_market_data.json
 data/snapshots/<YYYY-MM-DD>/<run_id>/data_quality_report.json
```

La carpeta de outputs de cada corrida agrega:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/investable_universe_snapshot.csv/json
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/benchmark_universe_snapshot.csv/json
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/excluded_universe_snapshot.csv/json
```

También copia snapshots de la corrida dentro de:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/snapshots/
```

## Data quality report

`data_quality_report.json` identifica:

- activos invertibles con datos suficientes;
- activos invertibles bloqueados por baja calidad o datos insuficientes;
- benchmarks disponibles para comparación;
- benchmarks faltantes sin inventar datos;
- símbolos excluidos;
- datos faltantes por campo;
- datos estimados;
- errores por proveedor;
- timestamp UTC de datos;
- si se usaron fuentes externas.

La calidad se resume como `HIGH`, `MEDIUM` o `LOW`. Si faltan precio de cierre, volumen o fecha del proveedor, el activo queda `LOW` y bloqueado. Si precio/volumen están presentes pero fundamentals vienen de fixture/base DEMO, queda como `MEDIUM` con campos estimados.

## Logs

Cada corrida escribe:

```text
logs/<run_id>.jsonl
```

Los eventos `market_data_started`, `market_data_provider_error` y `market_data_finished` muestran proveedor, modo, errores, cantidad de activos, activos bloqueados y paths de snapshots.

## Seguridad DEMO/paper trading

La Fase 5 mantiene:

- `system.mode: DEMO_PAPER_TRADING` obligatorio;
- `system.allow_real_orders: false` obligatorio;
- `real_order` siempre `false` en operaciones simuladas;
- sin broker ni cuenta operativa;
- sin GitHub Actions activado;
- `decision_agent` y `audit_agent` reales sin conexión;
- sin modificación automática de reglas humanas;
- sin API keys hardcodeadas.

## Troubleshooting básico

- **Quiero correr sin red o sin credenciales:** usar el modo default `market_data.mode: fixture` y `enabled: false`.
- **La config real falla al arrancar:** verificar que `market_data.mode: "real"` y `market_data.enabled: true` estén ambos seteados explícitamente.
- **El proveedor devuelve datos incompletos:** revisar `data_quality_report.json` y `logs/<run_id>.jsonl`; el sistema no completa datos faltantes como reales.
- **Aparecen activos bloqueados:** revisar `blocked_assets`, `missing_data` y las reglas `risk_rules.block_if_data_quality_low`.
- **Necesito volver al modo seguro:** restaurar `market_data.mode: "fixture"`, `enabled: false`, `provider: "fixture"`.

## Limitaciones de Fase 6B

- No hay automatización diaria.
- No hay broker ni órdenes reales.
- No hay recomendaciones financieras reales.
- `decision_agent` y `audit_agent` siguen mock.
- El proveedor real preparado es CSV público de Stooq; no todos los tickers/fundamentals pueden estar disponibles.
- Los fundamentals básicos se usan solo si el proveedor los entrega; si no, quedan marcados como estimados o faltantes.

## Para Fase 7

Fase 7 debería definir si se incorpora un proveedor pago/robusto de fundamentals, ampliar normalización multi-mercado, mejorar controles de moneda/FX, decidir si research cualitativo usa datos reales enriquecidos y diseñar revisión humana antes de cualquier paso hacia automatización. Broker, órdenes reales, decision_agent real y audit_agent real siguen fuera de alcance hasta aprobación explícita futura.

## Fase 6C: Universe Builder desde catálogos externos

La Fase 6C agrega una capa **Universe Builder** para ampliar el universo invertible sin hardcodear listas grandes en Python. El modo default sigue siendo `demo_small`, así que la DEMO histórica no cambia.

### Catálogos versionables

Los activos ampliables viven en archivos CSV separados:

```text
data/universe_catalogs/us_equities.csv
data/universe_catalogs/brazil_equities.csv
data/universe_catalogs/argentina_equities.csv
data/universe_catalogs/adrs.csv
data/universe_catalogs/manual_overrides.csv
```

Para agregar una acción, editar el CSV correspondiente y completar como mínimo: `ticker`, `name`, `country`, `market`, `exchange`, `currency`, `instrument_type`, `sector`, `industry`, `preferred_data_provider`, `eligible_for_investment`, `eligible_as_benchmark`, `min_liquidity_required_usd`, `analysis_priority` y `notes`. No hace falta tocar código.

### Cambiar entre demo_small, liquid_core y broad_market

Editar manualmente `config/config_demo.yaml`:

```yaml
market_data:
  universe_mode: "demo_small"   # demo_small | liquid_core | broad_market
```

- `demo_small`: pocos tickers, estable para correr sin credenciales.
- `liquid_core`: más activos líquidos, pero controlados.
- `broad_market`: carga desde catálogos y aplica `universe_builder.filters.max_assets_per_run_broad_market` para no forzar análisis masivo si no hay datos suficientes.

### Filtros antes del scoring

`universe_builder.filters` permite controlar países, mercados, tipos de instrumento, liquidez mínima, precio mínimo, volumen mínimo, calidad mínima de datos, exclusiones manuales y límites de research/procesamiento. Un activo que no cumpla esos filtros queda bloqueado antes del scoring.

### Evitar que ETFs benchmark entren al scoring

`SPY`, `QQQ`, `EWZ`, `ARGT` y `BIL` están marcados como `eligible_for_investment: false` y `eligible_as_benchmark: true`. Además, `universe_builder.allow_benchmarks_in_scoring: false` mantiene esos ETFs/proxies fuera del scoring aunque aparezcan por error en un modo de universo. Siguen disponibles solo para comparación de performance.

### Reporte de cobertura del universo

Cada corrida genera `outputs/daily_runs/<YYYY-MM-DD>/<run_id>/universe_coverage_report.json` y también incluye el mismo resumen dentro de `data_quality_report.json` bajo `universe_coverage`. El reporte muestra total cargado, elegibles, bloqueados, sin datos, baja liquidez, sin soporte del proveedor, enviados a scoring y enviados a research.

## Fase 7: forward-test, post-mortem y aprendizaje metodológico

La Fase 7 evalúa ventanas vencidas de 3, 6 y 12 meses desde `memory/forward_test_pending.csv`, escribe resultados en `memory/forward_test_results.csv` y genera un post-mortem por corrida en `forward_test_postmortem.md`. Si faltan precios, la fila queda como `NOT_EVALUABLE`; no se inventan datos. Las recomendaciones metodológicas se agregan a memoria externa como sugerencias y no modifican reglas ni `config_demo.yaml` automáticamente.

Ver `docs/forward_test_phase7.md` para la interpretación de hit rate, decisiones aprobadas/bloqueadas y uso de benchmarks.

## Fase 9: prueba integral end-to-end DEMO

La Fase 9 agrega una validación integral reproducible para comprobar que las fases anteriores funcionen juntas antes de pasar a un piloto con datos reales. La prueba corre por default con fixtures/mocks locales, no requiere credenciales ni red, no conecta broker, no ejecuta órdenes reales y no habilita `decision_agent` ni `audit_agent` reales.

### Cómo correr la validación integral

```bash
python scripts/run_e2e_validation.py --date 2026-06-27
```

El script ejecuta internamente `scripts/run_demo.py` con una muestra temporal de validación. Esa muestra no modifica automáticamente `config/config_demo.yaml` ni las reglas humanas.

### Muestra controlada

Activos invertibles validados:

- EEUU: `AAPL`, `MSFT`, `NVDA`.
- Argentina/ADRs: `YPF`, `GGAL`, `MELI`.
- Brasil/ADRs: `VALE`, `PBR`, `ITUB`.

Benchmarks validados como benchmarks, no como candidatos de scoring:

- `SPY`, `QQQ`, `EWZ`, `ARGT`, `BIL`.

### Outputs principales

Cada corrida genera una carpeta como:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/
```

Dentro de esa carpeta, además de los outputs normales de la demo, la validación Fase 9 escribe:

```text
e2e_validation_report.json
e2e_validation_report.md
```

El reporte muestra `PASS`, `FAIL` o `WARNING` por componente e indica outputs faltantes, warnings, datos faltantes, activos bloqueados, benchmarks que hubieran entrado incorrectamente al scoring, señales de broker u órdenes reales, estado de `real_order`, estado de `allow_real_orders` y confirmación de que `decision_agent` y `audit_agent` siguen mock.

### Cómo interpretar el resultado

- `PASS`: la validación se cumplió.
- `WARNING`: hay una limitación visible y auditada, por ejemplo datos faltantes de benchmark en fixture o activos bloqueados. No se oculta.
- `FAIL`: falta un output obligatorio o se violó una condición de seguridad/integridad.

La validación confirma seguridad revisando `run_manifest.json`, `simulated_trades.csv`, outputs mock y reportes generados. En particular:

- `broker_connected` debe ser `false`.
- `allow_real_orders` debe ser `false`.
- todo `real_order` observado debe ser `false`.
- `SPY`, `QQQ`, `EWZ`, `ARGT` y `BIL` no deben aparecer en `scoring_results.json`.
- Los context packs deben existir por agente y respetar sus límites configurados.

### Demo normal sigue igual

El comando histórico se mantiene compatible:

```bash
python scripts/run_demo.py --date 2026-06-27
```

La opción interna `--universe-symbols` existe para validaciones reproducibles y no es necesaria para el uso normal.

### Limitaciones que se mantienen en Fase 9

- Sin broker.
- Sin órdenes reales.
- Sin `real_order: true`.
- Sin datos reales por default.
- Sin API keys por default.
- Sin GitHub Actions activado.
- `decision_agent` y `audit_agent` reales siguen deshabilitados.
- No se modifican automáticamente `config/config_demo.yaml` ni reglas humanas.
- La deduplicación semántica profunda de memoria queda como control futuro; Fase 9 valida diff, idempotencia básica y ausencia de duplicaciones críticas evidentes.

### Para Fase 10

Fase 10 debería enfocarse en definir el piloto controlado con datos reales: proveedor de datos robusto, criterios de calidad más estrictos, manejo de moneda/FX, revisión humana obligatoria antes de cualquier automatización y controles adicionales antes de considerar cualquier integración operativa. Broker y órdenes reales deben seguir fuera de alcance salvo aprobación explícita futura.

## Fase 10: piloto controlado con datos reales al cierre

La Fase 10 agrega un piloto pequeño con datos reales de cierre, pero **sigue siendo DEMO / paper trading**. No conecta broker, no envía órdenes reales, no habilita `decision_agent` real y no habilita `audit_agent` real.

### Demo normal (default con fixture)

```bash
python scripts/run_demo.py --date 2026-06-27
```

Esta corrida no requiere credenciales ni APIs externas. `config/config_demo.yaml` conserva `market_data.mode: fixture` y `market_data.enabled: false` por default.

### Validación end-to-end de Fase 9

```bash
python scripts/run_e2e_validation.py --date 2026-06-27
```

La validación E2E sigue usando fixture/mock por default y verifica el flujo completo sin broker ni órdenes reales.

### Piloto con datos reales de Fase 10

El piloto real requiere activación explícita:

```bash
python scripts/run_real_data_pilot.py --date 2026-06-27 --activate-real-data-pilot
```

El wrapper llama internamente a `scripts/run_demo.py` con `--real-data-pilot`, `market_data.mode=real`, `market_data.enabled=true`, proveedor `stooq_csv` y una muestra cerrada. No modifica automáticamente `config/config_demo.yaml`.

### Muestra usada

Activos invertibles solicitados:

- EEUU: `AAPL`, `MSFT`, `NVDA`.
- Argentina / ADRs: `YPF`, `GGAL`, `MELI`.
- Brasil / ADRs: `VALE`, `PBR`, `ITUB`.

Benchmarks solicitados, solo para comparación y performance:

- `SPY`, `QQQ`, `EWZ`, `ARGT`, `BIL`.

Los benchmarks no entran al scoring. Si aparecen como activos excluidos, es esperado: significa que fueron reconocidos como benchmarks/no invertibles.

### Reportes del piloto

Cada corrida guarda los reportes en la carpeta diaria de outputs, por ejemplo:

```text
outputs/daily_runs/<fecha>/<run_id>/real_data_pilot_report.json
outputs/daily_runs/<fecha>/<run_id>/real_data_pilot_report.md
```

El reporte muestra:

- tickers solicitados;
- tickers con datos reales disponibles;
- tickers sin datos o con datos insuficientes;
- tickers bloqueados antes de scoring;
- benchmarks disponibles y faltantes;
- activos enviados a scoring;
- activos excluidos del scoring;
- si el flujo completo terminó;
- warnings y errores;
- confirmación de `broker_connected=false`, `allow_real_orders=false` y `real_order=false`.

### Cómo interpretar cobertura, WARNING y FAIL

- **PASS**: la muestra tuvo cobertura suficiente y todas las validaciones de seguridad pasaron.
- **WARNING**: el flujo terminó, pero faltaron datos reales o hubo activos bloqueados. Esto es aceptable en Fase 10 siempre que los faltantes queden visibles y no se inventen datos.
- **FAIL**: falló una regla de seguridad o integridad, por ejemplo broker conectado, `real_order=true`, benchmarks dentro del scoring, outputs obligatorios faltantes o agentes reales no permitidos.

### Por qué sigue siendo paper trading

El sistema mantiene `system.mode=DEMO_PAPER_TRADING`, `system.allow_real_orders=false`, `broker_connected=false` en el manifiesto de corrida y todas las filas de `simulated_trades.csv` tienen `real_order=false`. Las operaciones son simuladas y solo actualizan el portfolio DEMO local.

### Qué revisar antes de ampliar universo

Antes de pasar a una muestra mayor o a una Fase 11, revisar manualmente:

- cobertura real por ticker y benchmark;
- tickers bloqueados por datos insuficientes o liquidez;
- errores del proveedor `stooq_csv`;
- que benchmarks sigan fuera del scoring;
- que no haya credenciales, broker ni órdenes reales;
- consistencia entre `real_data_pilot_report.json`, `data_quality_report.json`, `universe_coverage_report.json` y `daily_report.md`.

## Fase 10B: fallback multi-provider y CSV manual controlado

La demo normal sigue usando fixtures por default y no requiere credenciales. El piloto real controlado solo se activa con `--activate-real-data-pilot`; mantiene `DEMO_PAPER_TRADING`, broker desconectado, `allow_real_orders=false`, `real_order=false`, `decision_agent` mock y `audit_agent` mock.

La sección `market_data` de `config/config_demo.yaml` ahora documenta estos parámetros de piloto:

- `provider_priority`: orden de proveedores para precios reales de cierre, por ejemplo `["stooq_csv", "manual_csv"]`.
- `minimum_real_data_coverage_pct`: cobertura mínima de activos invertibles para considerar PASS.
- `allow_manual_csv_fallback`: habilita explícitamente el fallback local/manual.
- `manual_csv_path`: ruta parametrizable, por default `data/manual_market_data/{date}/market_data.csv`.
- `fail_if_no_real_prices`: fuerza FAIL si ningún activo invertible tiene precio real válido.

### CSV manual

El CSV manual es un fallback controlado para entornos donde el proveedor externo no es accesible. Debe guardarse en:

```text
data/manual_market_data/<YYYY-MM-DD>/market_data.csv
```

Columnas mínimas requeridas, separadas por `;` o `,`:

```text
ticker;date;close;volume;currency;source
```

Cada fila se valida con las mismas reglas de calidad que un proveedor externo: `close` debe ser numérico, `volume` debe ser numérico, `date` debe estar presente, y se calcula `avg_volume_usd = close * volume`. Si faltan campos o no son parseables, el activo queda con calidad LOW, se bloquea antes de scoring y el error queda visible; no se inventan precios faltantes.

### Estados del piloto

- **PASS**: no hay errores de seguridad/integridad y la cobertura real de activos invertibles alcanza `minimum_real_data_coverage_pct`.
- **WARNING**: hay datos reales parciales, pero faltan activos o no se alcanza la cobertura mínima.
- **FAIL**: no hay datos para ningún activo invertible con `fail_if_no_real_prices=true`, aparece broker conectado, aparece `real_order=true`, benchmarks entran al scoring, faltan outputs críticos, se habilitan LLM/agentes reales no permitidos o se detecta una violación de integridad.

El reporte se guarda junto a los outputs diarios como `real_data_pilot_report.json` y `real_data_pilot_report.md`, incluyendo cobertura por proveedor, cobertura total, activos cubiertos/sin datos, benchmarks cubiertos/faltantes, errores por proveedor y si se usó CSV manual.

### Comandos

```bash
python scripts/run_demo.py --date 2026-06-27
python scripts/run_e2e_validation.py --date 2026-06-27
python scripts/run_real_data_pilot.py --date 2026-06-27 --activate-real-data-pilot
```
