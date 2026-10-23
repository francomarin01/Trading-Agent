# EMA Cross Dashboard

Dashboard personal, local y sin Docker para seguimiento diario de oportunidades
basadas en cruces EMA, tendencia, fuerza relativa y rotación sectorial.

## Arquitectura

- `app/`: motor cuantitativo, base SQLite y servidor local.
- `config/universe.json`: universo editable de acciones y ETFs sectoriales.
- `web/`: dashboard estático y el snapshot generado.
- `scripts/`: actualización manual y programación diaria en Windows.
- `tests/`: pruebas sin red ni datos sintéticos de producción.

Los datos de producción proceden de Yahoo Finance Chart API cuando se ejecuta
la actualización. Si una fuente no responde, el dashboard conserva el último
snapshot válido y muestra el estado del error; no completa precios faltantes.

## Primer uso

Desde PowerShell:

```powershell
cd "C:\Users\MAK\Documents\ChatGPT\Trading Agent\ema_cross_dashboard"
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\Actualizar.ps1
.\scripts\Abrir-Dashboard.ps1
```

La primera actualización descarga las series necesarias y genera
`web/data/dashboard.json`. Después abrí `http://127.0.0.1:8765`.

## Automatización

Para crear la tarea de lunes a viernes:

```powershell
.\scripts\Instalar-Actualizacion-Diaria.ps1 -Hora 18:15
```

Elegí 18:15 de Argentina mientras Nueva York esté en horario de verano, o
19:15 en el horario estándar de Nueva York. La aplicación usa datos diarios
completados, nunca una vela diaria en curso.

También podés abrir `scripts/Abrir-Monitor.vbs` con doble clic: abre el monitor y
ejecuta una actualización no bloqueante. Cuando termina, el navegador recarga el
snapshot. Para dejarlo disponible desde una URL privada sin abrir PowerShell, la
arquitectura recomendada y el paquete de publicación están en
[`docs/hosting_private.md`](docs/hosting_private.md). Prepará el front-end con:

```powershell
.\scripts\Preparar-Sitio.ps1
```

Si actualizás el código y querés regenerar las métricas desde los precios ya
guardados, sin acceder a la red, ejecutá `scripts/Reconstruir-Snapshot.ps1`.

## Uso del dashboard

- **Fundamentales** compara ratios de valoración calculados con hechos SEC reportados y el cierre diario de Yahoo Finance. Los datos SEC se conservan hasta siete días y los ratios se recalculan con el precio de cada snapshot. El PEG se basa en crecimiento histórico de EPS, no en consenso futuro. Fuente, fórmulas y limitaciones: [`docs/fundamental_module.md`](docs/fundamental_module.md).
- **Técnico** mantiene score, setups, medias y volumen en una vista separada. La pestaña Fundamental no modifica scores, señales ni resultados históricos.

- **Oportunidades** inicia en score ajustado mayor o igual a 65 y excluye
  `EXTENDED / DO NOT CHASE` y `WEAKENING`. Los tres controles son visibles y
  reversibles. El score base no se modifica: el score ajustado resta 15 puntos
  por `EXTENDED / DO NOT CHASE` y 12 por `WEAKENING` sólo para priorizar la
  lista de trabajo.
- El score operativo se compone de cuatro pilares continuos: **tendencia**
  (máximo 30), **fuerza relativa** (25), **contracción** de volatilidad y
  volumen (20), y **setup** de entrada (25). El máximo se limita a 99,5 para
  evitar interpretar una pantalla técnica como certeza. El detalle muestra las
  cuatro contribuciones y conserva el score binario legado sólo para auditoría.
- **Rotación** permite elegir cualquiera de las últimas 200 ruedas. El mapa
  posiciona cada ETF por fuerza relativa contra SPY y aceleración; el tamaño
  representa el breadth sobre SMA50. Cada fecha se calcula usando únicamente
  datos disponibles a ese cierre.
- **Gate operativo:** SPY, QQQ, VIX y Put/Call determinan si se habilitan nuevas entradas. El breadth agregado se muestra con umbral 50% como advertencia contextual; no bloquea una entrada por sí solo. La investigación histórica conserva su gate original para mantener la trazabilidad de resultados ya auditados.
- **Track record** incluye una fila por ticker desde su primera inclusión y
  agrega un consolidado por ETF: cantidad de casos, retornos válidos, win rate,
  retorno medio, retorno del ETF y exceso frente al ETF.
- **Investigación** conserva los filtros históricos como análisis separado.
  No promueve ni altera automáticamente los umbrales operativos. El backtest
  compara de forma separada el modelo `LEGACY` y `FOUR_PILLAR`; cada uno genera
  sus propias señales point-in-time y usa entrada en la apertura de T+1.

## Límites actuales

- Universo: acciones líquidas del S&P 500 por sector, semiconductores/SMH, neocloud e infraestructura de IA/NCLD, Brasil/EWZ y Argentina/ARGT. La composición y los benchmarks están documentados en [`docs/universe_expansion.md`](docs/universe_expansion.md).
- No hay ejecución de órdenes ni recomendaciones automáticas.
- El score es transparente y configurable; debe validarse antes de usarlo
  como criterio de inversión.


## Publicación gratuita

Para publicar una versión estática actualizada automáticamente mediante GitHub Actions y GitHub Pages, seguí [`docs/free_static_deployment.md`](docs/free_static_deployment.md). La publicación es de sólo lectura; la watchlist local no se expone.
