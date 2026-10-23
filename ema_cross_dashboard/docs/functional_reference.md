# Referencia funcional: tablero de cuatro pilares

Esta especificación traduce el texto de referencia aportado por el usuario a
requisitos propios, verificables y sin dependencia de una plataforma externa.
No reproduce fórmulas propietarias ni usa información que no esté disponible al
cierre de cada rueda.

## Principios que se adoptan

1. El ranking no es una recomendación automática. Resume cuatro dimensiones
   visibles y auditables: tendencia, fuerza relativa, contracción y setup.
2. El score se muestra como un número continuo, junto con sus cuatro aportes;
   nunca como una probabilidad de éxito ni con certeza de 100.
3. Rotación se interpreta por nivel de fuerza relativa y variación de esa
   fuerza. Las acciones y los ETF sectoriales son universos distintos.
4. Señales especiales (episodic pivot, contacto con EMA200 y VCP) se muestran
   como detectores independientes del score, con sus propias definiciones y
   validación histórica.
5. El régimen de mercado es contexto y guía de exposición. No bloquea por sí
   solo una señal individual.

## Restricciones ya aprobadas que se conservan

- Régimen global limitado a QQQ/SPY y VIX. No se agrega put/call ratio.
- Breadth sectorial permanece separado del termómetro global.
- Sólo se emplean velas completadas y nunca se imputan datos ausentes.
- Los filtros operativos se comparan por backtest point-in-time y walk-forward
  antes de modificar el ranking por defecto.

## Bloques de desarrollo siguientes

### A. KPIs globales y filtros rápidos

Contadores clickeables para activos visibles, precio sobre EMA200, precio sobre
SMA50, RS alto y volumen inusual. El filtro debe aplicarse a screener,
rotación, track record y detalle, sin ocultar el universo base ni cambiar datos.

### B. Rotación de acciones y ETF

Separar el mapa actual de ETF del mapa de acciones. Ambos usarán RS normalizado
contra SPY en el eje X y variación semanal de RS en el eje Y. Se añadirán listas
de entradas recientes a `LEADING` y aceleraciones inusuales. La línea temporal
actual de 200 ruedas se conserva.

### C. Ficha de activo

Completar el panel con estadio técnico basado en precio/pendiente de EMA200,
retorno diario, distancia a medias, volatilidad y desglose del score. El gráfico
de 60 ruedas ya existente será el punto de partida.

### D. Detectores independientes

- Episodic Pivot: subida diaria >= 4% y volumen >= 2.0x promedio, con etiqueta
  de contexto; no compra automática.
- EMA200 rebote/cruce: contacto reciente, dirección del cruce y confirmación de
  cierre/volumen.
- VCP: requiere una definición documentada de pivotes, contracciones y volumen
  decreciente; se desarrollará como modelo separado del score general.

### E. Eventos corporativos

Earnings requiere una fuente reproducible de calendario. Se mantiene como
`DATA UNAVAILABLE` hasta disponer de un proveedor autorizado y verificable.

## Diseño de score actual

El proyecto usa Tendencia 30, Fuerza RS 25, Contracción 20 y Setup 25. La guía
de referencia propone 25/30/30/15. Esa diferencia se tratará como hipótesis de
investigación: se contrastarán ambas variantes en series de señales separadas
antes de promover un cambio de pesos.

## Estado de componentes de referencia

- **Cuatro pilares:** tendencia, fuerza relativa, contracción y setup, visibles
  por separado en cada ficha.
- **Contexto de mercado:** QQQ/SPY y VIX como contexto, sin bloquear señales.
- **Screener y KPIs:** filtros rápidos clickeables para EMA200, SMA50, RS alto y
  volumen inusual; no alteran el score ni los datos históricos.
- **Rotación:** ETF sectoriales y acciones como universos separados; ambos se
  consultan por rueda en la línea temporal. El archivo de acciones se carga
  sólo al seleccionarlo para conservar una apertura rápida.
- **Ficha técnica:** precio y retorno diario, distancias a SMA50/EMA200, ATR,
  estadio, volumen, gráfico de 60 ruedas y contribuciones de los pilares.
- **Detectores independientes:** Episodic Pivot, eventos EMA200 y candidato
  VCP. Son etiquetas informativas y no cambian el score.
- **Earnings:** `DATA UNAVAILABLE` hasta integrar una fuente de calendario
  fechada y verificable.

## Bloques de análisis implementados

- **Spotlight Top 3:** los tres mayores scores ajustados del universo visible,
  con desglose compacto de los cuatro pilares.
- **Heatmap:** variación diaria por ticker y ETF. No simula tamaños por market
  cap porque esa serie no existe en la fuente actual.
- **Tendencia y comparador RS:** gráficos de RS Score point-in-time a cuatro y
  ocho semanas, alimentados por la historia de rotación de acciones.
- **Sesiones y volumen:** días alcistas en diez ruedas, ponderados por volumen
  medio de 10 días contra SMA50; es observación y no parte del score.
- **Volumen inusual:** ranking de desviación sobre SMA50, con lectura de
  acumulación, distribución o neutral basada en la dirección diaria.

Estos bloques no modifican el régimen QQQ/SPY-VIX, los pesos del score, los
estados de señal ni los backtests.
