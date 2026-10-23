# Publicación privada y actualización automática

## Qué resuelve cada parte

El monitor tiene dos tareas distintas:

1. **Mostrar el dashboard**: es estático; se puede alojar en un sitio privado.
2. **Actualizar los datos**: descarga OHLCV, recalcula el snapshot y debe correr en
   un proceso de servidor programado. Un navegador no debe guardar una API key ni
   recalcular/publicar datos de mercado por sí solo al abrir una página.

Por eso, abrir una URL puede solicitar la versión más reciente, pero no sustituye la
tarea diaria de datos. Esta separación evita exponer credenciales y conserva el
último snapshot válido si el proveedor falla.

## Uso local sin PowerShell

Abrí con doble clic `scripts/Abrir-Monitor.vbs`. El monitor abre
`http://127.0.0.1:8765` inmediatamente y lanza una actualización en segundo plano.
El navegador se recarga cuando se genera el nuevo snapshot. La tarea programada de
Windows sigue siendo la capa diaria recomendada, incluso si ese día no abrís el
monitor.

Para instalarla una única vez ejecutá
`scripts/Instalar-Actualizacion-Diaria.ps1 -Hora 18:15`. Usá 18:15 de Argentina
durante el horario de verano de Nueva York y 19:15 durante el horario estándar.

## Sitio privado: arquitectura recomendada

Cuando se habilite el proveedor de hosting elegido, publicá la carpeta `dist/` y
ejecutá el actualizador en una tarea privada programada. El proceso de datos debe:

1. Ejecutar `python -m app.updater` después del cierre de mercado.
2. Verificar que `web/data/dashboard.json` tenga estado `OK` y una fecha de mercado
   esperada antes de publicar.
3. Copiar únicamente el contenido de `dist/` al sitio privado.
4. Mantener el despliegue anterior si la descarga o la validación falla.

Ejecutá `scripts/Preparar-Sitio.ps1` para crear `dist/`. Incluye el front-end y los
JSON compactos que consume el dashboard; excluye la base SQLite, los backtests
detallados y cualquier credencial.

## Credenciales y proveedor de datos

La versión actual usa Yahoo Finance Chart API y no almacena una clave. Si se migra
a Tiingo u otro proveedor, la clave debe vivir exclusivamente como secreto del
trabajo programado o del host; nunca en `web/`, `dist/`, JavaScript ni el repositorio.

## Estado de publicación

Este repositorio queda preparado para publicar, pero no se creó un sitio remoto en
esta etapa. La integración de publicación depende del proveedor y de sus credenciales
privadas. No hay datos sintéticos ni actualización desde el navegador.
