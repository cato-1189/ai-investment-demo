# Fase 7: forward-test y post-mortem DEMO

La Fase 7 evalúa decisiones pasadas únicamente en **paper trading**. No conecta broker, no genera órdenes reales, no habilita `decision_agent` real ni `audit_agent` real, y no cambia `config_demo.yaml` ni reglas humanas automáticamente.

## Cómo interpretar los archivos

- `memory/forward_test_pending.csv`: ventanas pendientes creadas al registrar cada decisión. Cada decisión se agenda a 3, 6 y 12 meses.
- `memory/forward_test_results.csv`: resultados acumulados cuando una ventana ya venció.
- `outputs/daily_runs/<fecha>/<run_id>/forward_test_results.csv`: resultados de la corrida actual.
- `outputs/daily_runs/<fecha>/<run_id>/forward_test_postmortem.md`: post-mortem de la corrida con métricas y recomendaciones.

## Ventanas vencidas

Una ventana está vencida cuando `due_date <= fecha de corrida`. Si no hay ventanas vencidas, la demo informa `sin evaluaciones vencidas` y sigue ejecutando normalmente.

## Estados

- `WIN`: la decisión superó al benchmark por más de 1 punto porcentual.
- `LOSS`: la decisión quedó por debajo del benchmark por más de 1 punto porcentual.
- `NEUTRAL`: quedó dentro de ±1 punto porcentual contra benchmark.
- `NOT_EVALUABLE`: falta precio inicial/final del activo o del benchmark. El sistema no inventa ni imputa precios.

## Hit rate

`hit_rate = cantidad de WIN / cantidad de decisiones evaluables`.

Las decisiones `NOT_EVALUABLE` se muestran pero no entran al denominador del hit rate.

## Decisiones aprobadas y bloqueadas

- Aprobadas: filas con `final_action == APPROVED`.
- Bloqueadas: filas con `final_action == BLOCKED` o `NEED_MORE_DATA`.
- Aprobadas exitosas/fallidas: aprobadas con estado `WIN` o `LOSS`.
- Bloqueadas que hubieran funcionado: bloqueadas con estado `WIN`.
- Bloqueadas que evitaron pérdidas: bloqueadas con estado `LOSS`.

## Benchmarks

Los benchmarks se usan solo para comparación de retorno relativo. La separación de Fase 6B/6C se mantiene: ETFs benchmark como `SPY`, `QQQ`, `EWZ`, `ARGT` y `BIL` no pasan al scoring como candidatos salvo habilitación humana explícita en configuración.
