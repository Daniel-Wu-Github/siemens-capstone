"""Loopback HTTP API. No remote services, authentication, or production dispatch."""
from http.server import BaseHTTPRequestHandler
import json
import sys
import traceback
from urllib.parse import parse_qs, urlsplit

from .ingest import is_full_workbook
from .paths import INDEX_HTML as HTML
MAX_UPLOAD = 32 * 1024 * 1024


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # no per-request access log; unexpected errors are logged by fail()

        def fail(self):
            print(f'[server] {self.command} {urlsplit(self.path).path} failed:', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return self.respond(500, {'error': 'The operation could not be completed. Existing saved data was preserved. See the server log.'})

        def respond(self, status, value, content_type='application/json; charset=utf-8', download=False):
            body = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download: self.send_header('Content-Disposition', 'attachment; filename="replenishment-report.json"')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.valid_host(): return self.respond(403, {'error': 'Use the local server address.'})
            url = urlsplit(self.path)
            dataset = parse_qs(url.query).get('dataset', ['demo'])[0]
            try:
                if url.path in ('/', '/index.html'):
                    return self.respond(200, HTML.read_bytes(), 'text/html; charset=utf-8')
                if url.path == '/api/health': return self.respond(200, {'status': 'ok', 'storage': 'sqlite', 'mode': 'local prototype'})
                if url.path == '/api/datasets': return self.respond(200, store.datasets())
                if url.path == '/api/state': return self.respond(200, store.state(dataset))
                if url.path == '/api/insights': return self.respond(200, store.insights(dataset))
                if url.path == '/api/stock': return self.respond(200, store.stock_view(dataset))
                if url.path == '/api/export': return self.respond(200, store.export(dataset), download=True)
                return self.respond(404, {'error': 'Not found.'})
            except ValueError as exc:
                return self.respond(400, {'error': str(exc)})
            except Exception:
                return self.fail()

        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.valid_host() or (origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')):
                return self.respond(403, {'error': 'Requests must originate from this local app.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > MAX_UPLOAD:
                    return self.respond(413, {'error': 'Request must be nonempty and at most 32 MB.'})
                url = urlsplit(self.path)
                if url.path == '/api/import':
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/octet-stream':
                        return self.respond(415, {'error': 'Send the XLSX as a binary file.'})
                    content = self.rfile.read(size)
                    query = parse_qs(url.query)
                    filename = query.get('filename', ['workbook.xlsx'])[0]
                    dataset = query.get('dataset', [None])[0]
                    if dataset and not is_full_workbook(content):
                        return self.respond(201, store.import_reference(dataset, content, filename, query.get('as_of', [None])[0]))
                    return self.respond(201, store.import_workbook(content, filename))
                if size > 16384: return self.respond(413, {'error': 'JSON request exceeds 16 KB.'})
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    return self.respond(415, {'error': 'Use application/json.'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict): raise ValueError('Expected a JSON object.')
                dataset = body.get('dataset', 'demo')
                if url.path == '/api/forecast': result = store.forecast(dataset)
                elif url.path == '/api/evaluate': result = store.evaluate(dataset)
                elif url.path == '/api/scenario': result = store.scenario(dataset, body.get('factor'))
                elif url.path == '/api/policy': result = store.update_policy(dataset, body.get('material'), body.get('policy', {}))
                elif url.path == '/api/alert': result = store.alert_action(body.get('id'), body.get('action'), body.get('quantity'))
                elif url.path == '/api/stock/available': result = store.set_available(dataset, body.get('position'), body.get('available'))
                elif url.path == '/api/stock/reset': result = store.reset_stock(dataset)
                elif url.path == '/api/stock/digest': result = store.stock_digest(dataset)
                elif url.path == '/api/stock/ack': result = store.acknowledge_stock_alert(body.get('id'))
                elif url.path == '/api/workbook-scenario': result = store.workbook_scenario(dataset, body.get('position_id'), body.get('usage_multiplier'), body.get('minimum_stock'), body.get('lead_hours'))
                else: return self.respond(404, {'error': 'Not found.'})
                return self.respond(200, result if result is not None else {'ok': True})
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                return self.respond(400, {'error': str(exc) if isinstance(exc, ValueError) else 'Invalid request fields.'})
            except Exception:
                return self.fail()
    return Handler
