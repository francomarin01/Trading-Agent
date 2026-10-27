from __future__ import annotations

import argparse

from .fundamentals import FUNDAMENTALS_FILE, FinvizImportError, import_finviz_csv


def main() -> int:
    parser = argparse.ArgumentParser(description='Importa un CSV exportado manualmente desde Finviz.')
    parser.add_argument('--input', required=True, help='Ruta al archivo CSV exportado desde Finviz.')
    parser.add_argument('--as-of', required=True, help='Fecha de la exportación, YYYY-MM-DD.')
    parser.add_argument('--output', default=str(FUNDAMENTALS_FILE), help='Ruta destino del snapshot normalizado.')
    args = parser.parse_args()
    try:
        payload = import_finviz_csv(args.input, args.as_of, args.output)
    except (FinvizImportError, OSError) as exc:
        parser.error(str(exc))
    print(f"FINVIZ importado: {payload['row_count']} tickers · fecha {payload['as_of']} · destino {args.output}")
    print('Actualizá el dashboard con: python -m app.updater')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
