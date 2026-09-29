from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from siemens_capstone.api import handler_for
from siemens_capstone.store import Store


@contextmanager
def running_server(tmp_path):
    store = Store(tmp_path / 'api.sqlite3')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store))
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}'
    finally: server.shutdown(); server.server_close(); thread.join()


def request(base, path, body=None, headers=None):
    hdrs = {'Content-Type': 'application/json'}
    hdrs.update(headers or {})
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=hdrs)
    try:
        with urlopen(req) as response: return response.status, response.read()
    except HTTPError as response:
        return response.code, response.read()


def test_http_roundtrip_persistence_and_export(tmp_path):
    with running_server(tmp_path) as base:
        assert request(base, '/api/health')[0] == 200
        assert b'<!DOCTYPE html>' in request(base, '/')[1]
        status, body = request(base, '/api/state?dataset=demo')
        state = json.loads(body); assert status == 200 and state['materials']
        assert request(base, '/api/scenario', {'dataset': 'demo', 'factor': 1.5})[0] == 200
        assert json.loads(request(base, '/api/state?dataset=demo')[1])['dataset']['demand_factor'] == 1.5
        assert json.loads(request(base, '/api/export?dataset=demo')[1])['runs']


def test_http_invalid_inputs_and_cross_origin_rejected(tmp_path):
    with running_server(tmp_path) as base:
        assert request(base, '/api/scenario', {'dataset': 'demo', 'factor': -1})[0] == 400
        assert request(base, '/api/forecast', {'dataset': 'demo'}, {'Origin': 'https://example.com'})[0] == 403
        assert request(base, '/api/forecast', {'dataset': 'missing'})[0] == 400
        assert request(base, '/api/no-such-route')[0] == 404
        assert request(base, '/../../.git/config')[0] == 404
