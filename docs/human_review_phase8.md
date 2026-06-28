# Revisión humana y versionado metodológico - Fase 8

Una recomendación metodológica es una sugerencia generada por el sistema DEMO a partir del forward-test, post-mortems y memorias externas. Puede proponer revisar umbrales, reglas de riesgo, cobertura de datos o criterios de evaluación, pero no es una conclusión definitiva ni una orden operativa.

## Por qué no se aplica automáticamente

La demo sigue en paper trading. Las recomendaciones pueden tener evidencia incompleta, ventanas `NOT_EVALUABLE` o muestras pequeñas. Por seguridad, una recomendación aprobada solo genera cambios metodológicos propuestos y versionados; nunca modifica automáticamente `config/config_demo.yaml`, no activa broker, no habilita órdenes reales y no reemplaza la revisión humana.

## Cómo revisar

1. Abrí `memory/human_review_queue.jsonl`.
2. Buscá el `recommendation_id`.
3. Evaluá la descripción, evidencia, métrica afectada, severidad y sección metodológica impactada.
4. Registrá la decisión humana agregando una línea JSON a `memory/human_review_decisions.jsonl` con:

```json
{"recommendation_id":"REC-EJEMPLO","status":"APPROVED","human_comment":"Aprobado para propuesta; no aplicar a config todavía.","decision_date":"2026-06-28"}
```

Estados permitidos: `PENDING`, `APPROVED`, `REJECTED`, `NEEDS_MORE_EVIDENCE`.

## Cómo interpretar versiones

`memory/methodology_versions.jsonl` registra versiones metodológicas propuestas por aprobaciones humanas. Cada versión incluye hash del estado metodológico anterior y hash del estado propuesto. Es una pista auditable de intención metodológica; no implica cambio automático en reglas ni configuración.
