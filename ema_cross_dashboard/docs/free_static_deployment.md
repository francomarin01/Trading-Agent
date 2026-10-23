# Publicación gratuita y actualización diaria

Esta implementación publica una versión **estática y de sólo lectura**: no exige
un servidor propio, Docker ni una suscripción de hosting. El cálculo se ejecuta
en GitHub Actions y Cloudflare Pages muestra el contenido de `dist/`.

## Qué se publica

`dist/` contiene HTML, CSS, JavaScript y snapshots JSON compactos. No incluye la
base SQLite, claves, historial OHLCV completo ni backtests voluminosos. Las
acciones de watchlist y entrada paper requieren el monitor local: un sitio
estático no puede guardar cambios compartidos sin una API y una base de datos.

## Preparación única

1. Creá un repositorio privado en GitHub y subí la carpeta `Trading Agent`.
2. En **Settings → Secrets and variables → Actions**, agregá el secreto
   `SEC_USER_AGENT`, por ejemplo: `Tu Nombre tu-correo@ejemplo.com`.
   No es una API key: identifica de forma responsable las consultas a SEC.
3. En Cloudflare, creá un proyecto **Pages**, conectalo al repositorio y elegí:
   - Directorio raíz: `ema_cross_dashboard`
   - Sin comando de build de Cloudflare: la publicación la genera GitHub Actions.
   - Directorio publicado: `dist`
4. Habilitá el workflow **Refresh and publish EMA Cross dashboard**. También se
   ejecuta automáticamente de lunes a viernes a las 22:20 UTC, después del cierre
   regular de EE. UU.

Cloudflare Pages detectará el commit diario de `dist/` y actualizará la URL.
Si querés una actualización excepcional, ejecutá el workflow manualmente desde
la pestaña **Actions** de GitHub.

## Persistencia y límites

GitHub Actions restaura y guarda una caché de SQLite para evitar descargar toda
la historia cada día. La caché acelera el proceso, pero no debe ser considerada
un respaldo. Conservá el repositorio y, localmente, la carpeta `data/` como
respaldo de tu bitácora.

La URL publicada es de lectura. Antes de compartirla públicamente hay que
revisar los términos de las fuentes y configurar un control de acceso. Para una
watchlist sincronizada entre dispositivos necesitaremos una base de datos y una
API; ese será un bloque posterior y separado.

## Datos fundamentales SEC

Durante cada actualización el pipeline intenta actualizar, como máximo, los 35
activos de mayor prioridad técnica. Guarda en cada observación:

- ticker y CIK;
- formulario, fecha de presentación y período contable;
- ingresos, resultado neto, EBIT si existe, CFO, FCF calculado cuando existen
  CFO y capex, caja, deuda y acciones en circulación;
- URL exacta del Company Facts de SEC.

Estos datos son contextuales: **no cambian el score técnico, el gate ni los
backtests**. Si SEC no cubre un ticker, falta `SEC_USER_AGENT` o no existe un
hecho XBRL comparable, el dashboard muestra `DATA UNAVAILABLE`.

## Earnings y noticias

No se inventan fechas de earnings. La interfaz sólo mostrará una fecha cuando
se cargue desde una fuente verificable, junto con URL y fecha de actualización.
La siguiente fase puede añadir un importador de calendario confirmado por
Investor Relations y un feed de titulares GDELT/IR, manteniéndolos separados del
score hasta completar su validación histórica.
