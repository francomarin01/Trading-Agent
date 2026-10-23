from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Lock, Thread
from typing import Any
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
_refresh_lock = Lock()
_refresh_state: dict[str, Any] = {'running': False, 'started_at': None, 'finished_at': None, 'status': 'IDLE', 'error': None, 'as_of': None}


def refresh_status() -> dict[str, Any]:
    """Return a copy so the HTTP handler never exposes mutable state."""
    with _refresh_lock:
        return dict(_refresh_state)


def begin_refresh() -> bool:
    """Start one non-blocking update; opening the dashboard remains immediate."""
    with _refresh_lock:
        if _refresh_state['running']:
            return False
        _refresh_state.update({'running': True, 'started_at': datetime.now(timezone.utc).isoformat(), 'finished_at': None, 'status': 'RUNNING', 'error': None, 'as_of': None})

    def worker() -> None:
        try:
            from .updater import refresh
            result = refresh()
            state = {'status': result.get('status', 'UNKNOWN'), 'error': '; '.join(result.get('refresh', {}).get('errors', [])) or None, 'as_of': result.get('as_of')}
        except Exception as exc:  # The local monitor must stay usable on update failure.
            state = {'status': 'FAILED', 'error': str(exc), 'as_of': None}
        with _refresh_lock:
            _refresh_state.update({'running': False, 'finished_at': datetime.now(timezone.utc).isoformat(), **state})

    Thread(target=worker, name='ema-data-refresh', daemon=True).start()
    return True


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serve static files and a small local-only health endpoint."""

    def do_GET(self) -> None:  # noqa: N802 - inherited API
        if self.path.split('?', 1)[0] == '/api/refresh-status':
            body = json.dumps(refresh_status(), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict:
        size = int(self.headers.get('Content-Length', '0'))
        if size <= 0 or size > 4096:
            raise ValueError('Solicitud inválida')
        value = json.loads(self.rfile.read(size).decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Solicitud inválida')
        return value

    def _snapshot(self) -> dict:
        return json.loads((ROOT / 'web' / 'data' / 'dashboard.json').read_text(encoding='utf-8'))

    def _rebuild_after_watchlist_change(self) -> dict:
        from .updater import rebuild_from_local
        return rebuild_from_local()

    def do_POST(self) -> None:  # noqa: N802 - inherited API
        path = self.path.split('?', 1)[0]
        if path not in ('/api/watchlist', '/api/watchlist/open'):
            self._send_json(404, {'error': 'Ruta no encontrada'})
            return
        try:
            from .storage import connect
            from .watchlist import add_to_watchlist, open_watchlist_position
            payload = self._payload(); snapshot = self._snapshot(); con = connect()
            try:
                changed = add_to_watchlist(con, snapshot, str(payload.get('ticker', ''))) if path == '/api/watchlist' else open_watchlist_position(con, snapshot, str(payload.get('ticker', '')), payload.get('entry_price'), payload.get('entry_date'))
            finally:
                con.close()
            refreshed = self._rebuild_after_watchlist_change()
            self._send_json(200, {'changed': changed, 'watchlist': refreshed.get('watchlist', [])})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {'error': str(exc)})
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802 - inherited API
        prefix = '/api/watchlist/'
        path = self.path.split('?', 1)[0]
        if not path.startswith(prefix):
            self._send_json(404, {'error': 'Ruta no encontrada'})
            return
        ticker = urllib.parse.unquote(path[len(prefix):]).strip().upper()
        try:
            from .storage import connect
            from .watchlist import remove_from_watchlist
            con = connect()
            try:
                changed = remove_from_watchlist(con, ticker)
            finally:
                con.close()
            refreshed = self._rebuild_after_watchlist_change()
            self._send_json(200, {'changed': changed, 'watchlist': refreshed.get('watchlist', [])})
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})


def serve(port: int = 8765, open_browser: bool = True, refresh_on_open: bool = True) -> None:
    handler = lambda *args, **kwargs: DashboardHandler(*args, directory=str(ROOT / 'web'), **kwargs)
    url = f'http://127.0.0.1:{port}'
    if refresh_on_open:
        begin_refresh()
    if open_browser:
        webbrowser.open(url)
    print(f'EMA Cross Dashboard: {url} (Ctrl+C para cerrar)')
    print('La actualización se ejecuta en segundo plano al abrir el monitor.')
    ThreadingHTTPServer(('127.0.0.1', port), handler).serve_forever()


if __name__ == '__main__': serve()
