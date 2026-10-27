# Módulo de análisis fundamental

La pestaña **Fundamentales** es una matriz de comparación informativa. Sus datos no modifican score, señales, estados, bitácora ni backtests.

## Fuente elegida y actualización

- **Fuente concreta: Finviz Screener.** Finviz documenta Sector, Industry, Forward P/E, PEG, P/B, ROE, EPS growth next 5 years, márgenes y otras métricas. Define PEG como Forward P/E respecto al crecimiento EPS proyectado a 3–5 años. [Finviz screener help](https://finviz.com/help/screener)
- **La exportación CSV requiere Finviz Elite.** Su documentación identifica export/API como prestación Elite; por eso esta integración no es una solución gratuita por sí sola. Si no tenés acceso a Elite, el importador sigue disponible, pero el dashboard conserva `DATA UNAVAILABLE` hasta que exista una fuente de archivo autorizada. [Finviz: introducción al screener](https://finviz.com/knowledge-base/screener/getting-started-with-screener/introduction-to-screener)
- No se hace webscraping ni descarga automática de fundamentales. El refresco técnico diario continúa independiente. No automatizo Yahoo Finance como reemplazo: `yfinance` usa endpoints no oficiales y Yahoo advierte que no se redistribuyan sus datos; además, la descarga CSV oficial documentada es para históricos de precios y requiere Gold. [yfinance README](https://github.com/ranaroussi/yfinance/blob/main/README.md) [Yahoo Finance data availability](https://uk.help.yahoo.com/kb/finance/article-data-availability-yahoo-finance-sln2310.html) [Yahoo: descarga de históricos](https://ca.help.yahoo.com/kb/SLN2311.html)

## Importación

En Finviz, exportá el universo con las columnas Ticker, Sector, Industry, P/E, Forward P/E, PEG, P/S, P/B, ROE, ROA, Oper. Margin, EPS growth next 5 years y Sales growth past 5 years. Agregá EV/Sales, EV/EBITDA o FCF yield sólo si la exportación que tenés realmente incluye esos campos; el importador no convierte P/S en EV/Sales.

Desde PowerShell, ejecutá:

```powershell
cd "C:\Users\MAK\Documents\ChatGPT\Trading Agent\ema_cross_dashboard"
python -m app.import_finviz --input "C:\ruta\al\finviz_export.csv" --as-of 2026-10-23
python -m app.updater
```

`--as-of` debe ser la fecha real de la exportación en formato `YYYY-MM-DD`. El script valida ticker, sector, industria, encabezados, duplicados y fecha; convierte porcentajes y escribe `data/incoming/finviz_fundamentals.json`. Si un campo no vino en el CSV, el dashboard lo deja vacío. No completa datos faltantes.

Para que GitHub Actions actualice la página pública, incorporá el JSON normalizado a la rama del repositorio; el workflow detecta cambios bajo `data/incoming/`, reconstruye el dashboard y lo publica. Ese repositorio y la página son públicos: los valores fundamentales del snapshot también quedarán públicamente accesibles. La actualización programada reutiliza la última exportación y muestra `STALE` cuando supera siete días; no obtiene un CSV nuevo por sí sola. No publiques un archivo si sus condiciones de uso no autorizan su redistribución.

## Matriz por sector e industria

El mapa agrupa por Sector y la matriz usa Industry como grupo de pares preferido. Los promedios son simples y excluyen el propio ticker; si una industria no tiene otra empresa exportada, se usa el promedio de su sector y se identifica ese grupo. Los datos faltantes no entran al promedio.

Un múltiplo inferior al promedio se marca como relativamente menor; ROE, márgenes y crecimiento superiores se marcan como relativamente mayores. Estos colores describen cada métrica por separado: no producen un puntaje combinado ni una recomendación. PEG es una expectativa del proveedor y puede cambiar; no es garantía de crecimiento.

## Limitaciones

La exportación disponible puede no contener EV/Sales, EV/EBITDA o todos los activos. Esos campos permanecen `DATA UNAVAILABLE`; P/S no se usa como sustituto de EV/Sales. El screener tiene su propia cobertura y metodología, y el archivo no incluye una serie histórica para reconstruir cómo se veían las estimaciones en fechas anteriores. La comparación es transversal a la fecha de exportación, no un backtest point-in-time.
