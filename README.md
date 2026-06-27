# AI Investment Demo

Sistema DEMO de inversión autónoma en modo paper trading. La Fase 5 agrega ingesta controlada de datos de cierre para un universo acotado, con snapshots auditables, data quality report, fallback a fixtures y bloqueo por baja calidad. El sistema sigue sin broker, sin órdenes reales, sin automatización diaria y con `decision_agent`/`audit_agent` reales deshabilitados.

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

## Universo inicial editable

La configuración deja un universo acotado y editable en `market_data.universe`:

- Acciones líquidas de EEUU: `AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`, `JPM`, `XOM`, `KO`, `WMT`.
- ADRs argentinos principales: `MELI`, `YPF`, `GGAL`, `BMA`, `PAM`.
- Brasil/ETFs vinculados: `VALE`, `PBR`, `ITUB`, `BBD`, `ABEV`, `EWZ`, `BRZU`.

El usuario puede editar esa lista sin tocar código. En Fase 5 los fundamentals no provistos por el proveedor quedan marcados como `estimated_fields`; no se inventan como datos reales.

## Snapshots auditables

Cada corrida escribe snapshots en dos lugares:

```text
data/snapshots/<YYYY-MM-DD>/<run_id>/raw_market_data.json
 data/snapshots/<YYYY-MM-DD>/<run_id>/normalized_market_data.json
 data/snapshots/<YYYY-MM-DD>/<run_id>/data_quality_report.json
```

También copia snapshots de la corrida dentro de:

```text
outputs/daily_runs/<YYYY-MM-DD>/<run_id>/snapshots/
```

## Data quality report

`data_quality_report.json` identifica:

- activos completos;
- datos faltantes por campo;
- datos estimados;
- errores por proveedor;
- activos bloqueados;
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

## Limitaciones de Fase 5

- No hay automatización diaria.
- No hay broker ni órdenes reales.
- No hay recomendaciones financieras reales.
- `decision_agent` y `audit_agent` siguen mock.
- El proveedor real preparado es CSV público de Stooq; no todos los tickers/fundamentals pueden estar disponibles.
- Los fundamentals básicos se usan solo si el proveedor los entrega; si no, quedan marcados como estimados o faltantes.

## Para Fase 6

Fase 6 debería definir si se incorpora un proveedor pago/robusto de fundamentals, ampliar normalización multi-mercado, mejorar controles de moneda/FX, decidir si research cualitativo usa datos reales enriquecidos y diseñar revisión humana antes de cualquier paso hacia automatización. Broker y órdenes reales siguen fuera de alcance hasta aprobación explícita futura.
