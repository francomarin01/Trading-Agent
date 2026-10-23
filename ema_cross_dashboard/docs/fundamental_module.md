# Módulo Fundamental

La pestaña **Fundamentales** separa el análisis de valor del score de momentum. El score, las señales, los estados, la bitácora y los backtests no consumen datos fundamentales.

## Fuentes y fecha

- Estados, acciones en circulación, EPS y fechas de presentación: SEC EDGAR Company Facts, filtrados a formularios reportados y hechos disponibles al momento de la actualización.
- Precio: último cierre diario que el pipeline ya obtiene de Yahoo Finance Chart API.
- Las métricas se regeneran en cada snapshot con el último cierre, mientras el payload SEC se cachea por siete días. La pestaña muestra la fecha de cotización y la última fecha de filing para que no se confundan.
- El endpoint público Company Facts no necesita una API key. La aplicación requiere `SEC_USER_AGENT` con nombre y contacto, y separa llamadas a intervalos de 0,12 segundos (menos de 10 solicitudes por segundo). La SEC publica Company Facts actualizado al difundir nuevas presentaciones y limita el acceso automatizado a 10 solicitudes por segundo. Véanse [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) y [SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions).

## Definiciones

- **Ingresos TTM, resultado neto TTM, EPS diluido TTM, flujo operativo y capex:** suma de cuatro trimestres discretos contiguos. Si esos trimestres no están disponibles, se usa el último ejercicio anual completo; nunca se convierte un trimestre suelto en TTM.
- **P/E:** precio / EPS diluido TTM; si EPS no está disponible, capitalización indicativa / resultado neto TTM. Sólo se publica para ganancias positivas.
- **PEG histórico:** P/E / crecimiento interanual porcentual del EPS TTM; sólo cuando el crecimiento es positivo y ambos períodos son comparables. No es PEG de consenso ni una previsión.
- **EV:** capitalización indicativa + deuda reportada − caja reportada. Capitalización indicativa = cierre diario × acciones en circulación reportadas. Se muestra como estimación porque la cantidad de acciones puede tener retraso o diferencias por ADR y clases de acciones.
- **EV/Sales:** EV / ingresos TTM positivos.
- **EV/EBITDA:** EV / EBITDA aproximado, calculado como resultado operativo TTM + depreciación y amortización TTM. Puede diferir del EBITDA ajustado o de la definición de proveedores.
- **P/B:** capitalización indicativa / patrimonio atribuible a accionistas, sólo si es positivo.
- **FCF yield:** (flujo operativo TTM − capex TTM) / capitalización indicativa.
- **Crecimiento de ventas:** cambio interanual entre TTM comparables. Se evita mostrar cuando no se puede construir ambos períodos.

Un ratio aparece como no disponible cuando faltan componentes, los valores no son positivos donde la razón perdería sentido, o SEC no reporta conceptos comparables. No se imputan ni aproximan cifras faltantes. La tabla combina los ratios con score y estado técnico para facilitar la comparación; no produce un score combinado ni una recomendación automática.

## Cobertura y límites

Company Facts de SEC cubre hechos XBRL de emisores que presentan ante la SEC. La cobertura y las etiquetas varían por emisor; la cobertura de emisores extranjeros, ADR, fondos y estructuras con múltiples clases puede ser incompleta. Además, XBRL no normaliza todas las definiciones de cada compañía. Revisa el filing fuente antes de comparar empresas de sectores distintos. No se obtienen estimaciones de EPS, crecimiento futuro, PEG de analistas, múltiplos normalizados de proveedores, noticias ni calificaciones de inversión.

Los ratios son información contextual, no asesoramiento financiero ni una orden de compra. El dashboard sigue siendo de lectura y paper tracking.

## Actualización

La tarea diaria existente actualiza el cierre, recompone el snapshot y publica el dashboard. SEC Facts se vuelve a consultar al vencer el TTL de siete días; las razones se recalculan con el cierre diario disponible. En desarrollo local se puede reconstruir el snapshot desde los datos almacenados con `scripts/Reconstruir-Snapshot.ps1`; la reconstrucción local no consulta ni renueva SEC.
