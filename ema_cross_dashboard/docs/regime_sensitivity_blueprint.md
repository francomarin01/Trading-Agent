# Especificación de investigación: régimen y score

Reglas confirmadas por el usuario el 2026-10-13. Implementación aislada del
dashboard operativo. Fuente de OHLCV: SQLite existente; Put/Call TOTAL: Cboe
diario fechado con procedencia por observación. Corte reproducible: 2026-10-12.

## Régimen

FULL = SPY y QQQ Close>SMA50>SMA200 AND breadth>50% AND VIX<22 AND Put/Call<1.05.
PARTIAL_NO_PC elimina únicamente Put/Call y queda expresamente identificado.
NO_GATE, TREND_ONLY y TREND_BREADTH son controles de ablación.
Dato ausente bloquea nuevas entradas; el gate no cambia la salida de una
posición ya abierta. Put/Call se toma de la rueda de SPY inmediatamente anterior
al cierre de señal: no consta hora histórica de publicación del dato del día.

Breadth se calcula sobre el universo configurado actual, con 50 observaciones
previas suficientes. Se publican denominador válido, exclusiones y límites
inferior/superior ante observaciones desconocidas. Si el límite inferior supera
50%, la condición es cierta incluso bajo los casos desconocidos; si el superior
no supera 50%, es falsa; en el intervalo ambiguo el gate bloquea. Estos límites
no rellenan precios ni asignan valores a componentes faltantes. Es breadth de
universo retrospectivo sobreviviente (BREADTH_PROXY), no constituyentes históricos.

## Score

Cada pilar se normaliza a [0,1] y se multiplica por su peso. Base 25/30/30/15.

- Tendencia: 60% por Close>EMA21>SMA50>SMA200, 20% por SMA200>SMA200[20],
  20% por cruce/rebote EMA200 en las últimas 5 barras. Cruce: cierre cruza la
  EMA200 hacia arriba. Rebote: mínimo toca EMA200 y cierre termina arriba.
- RS: tercio por ratio Activo/SPY>SMA50(ratio), tercio por RS-Momentum>100,
  tercio por aceleración RS-Momentum>102. Es distribución explícita igual de
  los tres criterios del módulo, sin calibración por retornos. RS-Ratio=
  100*ratio/SMA50(ratio); RS-Momentum=100*RS-Ratio/SMA10(RS-Ratio).
  Cuadrantes: líder (+/+), debilitando (+/-), rezagando (-/-), recuperando (-/+).
  Igualdad a 100 se informa como NEUTRAL. No replica el indicador JdK propietario.
- VCP: 50% por ATR14/Close igual al mínimo de 20 barras, 50% por volumen
  actual<0.7*SMA50(volumen). Base VCP requiere ambas condiciones en una rueda.
- Setup: 100% por EP (open/close[1]-1>8%, volumen>2*SMA50) OR por cierre que
  rompe un pivot high 5/5 conocido antes de la barra de señal, con base VCP
  observada en alguna de las 20 ruedas anteriores. No acumula dos gatillos.
- Clímax: resta 5 puntos al total si Close-SMA50>3.5*ATR14; score limitado a 0–100.

ATR es media simple de True Range, consistente con el motor actual. Los pesos
del grid no cambian ese cálculo. La sensibilidad prueba también exigir setup
como condición adicional, sin asumir que esa variante ya está aprobada para uso.

## Cartelera

RSI14>70 o <30; ETF con volumen>2*SMA50 y despegue reciente (5 barras):
EMA9/21 cross, recuperación SMA50 o ruptura del máximo de cierres de 20 barras.
Son metadatos informativos; no agregan puntos al score.

## Sensibilidad y selección

Pesos en saltos de 5: tendencia15–30, RS20–35, VCP20–40, setup10–25;
se conservan únicamente tuplas que suman100 (50 combinaciones).
Sensibilidad de umbral 65/70/75/80; FULL/PARTIAL_NO_PC; con/sin setup obligatorio.
800 configuraciones más controles. Entrada open T+1 común; salida close T+h,
h=5/10/20 ruedas SPY; coste total10pb; cooldown20 ruedas por activo/configuración.
La entrada principal requiere un cruce nuevo del score. El gate y setup opcional
aceptan/bloquean ese cruce; abrir el gate no genera una compra por sí mismo.
Una variante ELIGIBILITY_CROSS queda archivada para medir la diferencia, fuera
de la selección principal. Esa variante sí permite entradas cuando el score
continúa alto y cambia el gate: no se debe confundir con el efecto puro del filtro.
No se comprime una fecha de entrada/salida sin precio: se informa como faltante.

PF = ganancias netas acumuladas / pérdidas netas absolutas. PF de SPY usa los
mismos intervalos; PF del exceso se calcula sobre retornos acción menos SPY.
PF no calculable si no existen pérdidas; no se premian valores infinitos.

La selección walk-forward optimiza sólo pesos, manteniendo umbral65 y setup
opcional desactivado. Dos selectores visibles: máximo PF del exceso y máximo
win rate; mínimo100 eventos y12 fechas de entrada en entrenamiento, PF>1 y
exceso medio>0. Selección sólo con años anteriores y operaciones ya terminadas.
Se purgan operaciones que crucen límites de año. 2026 ya fue examinado en el
estudio anterior: es test temporal retrospectivo, no holdout virgen.

Se entregan todas las configuraciones, muestra por año, cobertura del gate,
costes de10/25/50pb para candidatos, bootstrap por mes de entrada y eventos
auditables. Los promedios de eventos no son una cartera ni independencia estadística.
