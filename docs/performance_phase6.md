# Guía simple de performance DEMO - Fase 6

La Fase 6 mide si la cartera DEMO agrega valor sin conectar broker ni emitir órdenes reales.

## NAV

El NAV es el valor diario de la cartera DEMO: cash disponible más valor de mercado de las posiciones simuladas. Si falta un precio de una posición, el sistema conserva el último valor conocido y marca el dato como faltante.

## Performance

La performance diaria compara el NAV de hoy contra el NAV anterior registrado en memoria. La performance desde inicio compara el NAV actual contra el capital inicial configurado.

## Benchmarks

Los benchmarks iniciales configurables son SPY, QQQ, EWZ, ARGT y BIL. Si falta un precio de benchmark, se informa explícitamente como dato faltante y no se inventa.

## Benchmark compuesto

El benchmark compuesto usa los pesos reales de la cartera DEMO: acciones de Estados Unidos contra SPY, tecnología contra QQQ, Brasil contra EWZ, Argentina contra ARGT y cash contra BIL.

## Forward-test

Cada decisión queda pendiente para evaluación futura a 3, 6 y 12 meses. Cuando venza una ventana, se podrá comparar el retorno del activo contra su benchmark y clasificar si la decisión agregó valor.
