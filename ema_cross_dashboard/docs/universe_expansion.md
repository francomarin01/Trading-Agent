# Expansión del universo: 2026-10-15

El universo operativo combina la base S&P 500 por GICS con cuatro lentes
adicionales. Cada activo se asigna **una sola vez**: su ETF asociado es el
benchmark que se usa para fuerza relativa, retorno de la cartelera y el track
record. No hay precios imputados: un activo sólo aparece cuando Yahoo Finance
entrega al menos 252 velas diarias OHLCV válidas.

| Lente | ETF benchmark | Cobertura |
|---|---|---|
| Semiconductores y equipamiento | `SMH` | Diseño, fabricación, equipos, EDA y componentes. Incluye, entre otros, TSM, ASML, ARM, ALAB, GFS, CRDO, AMKR y los semiconductores que antes estaban en XLK. |
| Neocloud e infraestructura de IA | `NCLD` | NBIS, CRWV, IREN, HUT, WULF, APLD, CIFR, GLXY, CORZ y CLSK. NCLD es una referencia temática focalizada; inició el 2026-08-06, por lo que su historial se marca como limitado sin completar sesiones previas. |
| Brasil | `EWZ` | ADRs/acciones brasileñas cotizadas en EE.UU.: VALE, PBR, ITUB, NU, BBD, ABEV, BSBR, CIG, XP, PAGS y STNE; AFYA se incorpora en la cesta Argentina por su exposición operativa local. |
| Argentina | `ARGT` | ADRs y compañías con exposición primaria a Argentina: MELI, YPF, GGAL, BMA, SUPV, TGS, PAM, LOMA, CEPU, CRESY, EDN, BBAR e IRS. |

Las cestas son una selección operativa, no una recomendación de inversión ni
una réplica de las carteras de los ETFs. `SMH` está contrastado contra las
posiciones publicadas por VanEck; `EWZ` sigue el índice MSCI Brazil 25/50 de
iShares; `ARGT` sigue el MSCI All Argentina 25/50 de Global X; y `NCLD` es el ETF de Neocloud de Roundhill. Las composiciones de los ETFs pueden
cambiar, por lo que las listas de esta configuración quedan versionadas y no se
actualizan silenciosamente.

## Actualización

Después de cambiar el universo, ejecutá una actualización completa:

```powershell
.\scripts\Actualizar.ps1
```

La rutina descarga sólo datos observados y deja registrado cualquier símbolo
que no haya podido validar. El monitor conserva el último snapshot válido ante
un error de proveedor. El importador S&P 500 (`python -m app.universe`) preserva
las lentes SMH, NCLD, EWZ y ARGT en futuras actualizaciones y elimina cualquier
duplicado respecto de la base GICS.
