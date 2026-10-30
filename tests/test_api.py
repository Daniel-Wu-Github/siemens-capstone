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


def test_http_health_index_and_empty_workspace(tmp_path):
    with running_server(tmp_path) as base:
        assert request(base, '/api/health')[0] == 200
        assert b'<!DOCTYPE html>' in request(base, '/')[1]
        status, body = request(base, '/api/datasets')
        assert status == 200 and json.loads(body) == []


def test_retired_legacy_routes_are_gone(tmp_path):
    with running_server(tmp_path) as base:
        for path in ('/api/state', '/api/export'):
            assert request(base, path)[0] == 404
        for path in ('/api/forecast', '/api/evaluate', '/api/scenario', '/api/policy', '/api/alert', '/api/workbook-scenario'):
            assert request(base, path, {'dataset': 'x'})[0] == 404


def test_http_invalid_inputs_and_cross_origin_rejected(tmp_path):
    with running_server(tmp_path) as base:
        assert request(base, '/api/stock/check', {'dataset': 'x'}, {'Origin': 'https://example.com'})[0] == 403
        assert request(base, '/api/stock/check', {'dataset': 'missing'})[0] == 400
        assert request(base, '/api/no-such-route')[0] == 404
        assert request(base, '/../../.git/config')[0] == 404


def test_unknown_dataset_is_a_client_error(tmp_path):
    with running_server(tmp_path) as base:
        for path in ('/api/insights?dataset=demo', '/api/sources?dataset=demo', '/api/stock?dataset=demo'):
            status, body = request(base, path)
            assert status == 400 and 'not found' in json.loads(body)['error'].lower()


def post_bytes(base, path, content):
    req = Request(base + path, data=content, headers={'Content-Type': 'application/octet-stream'})
    try:
        with urlopen(req) as response: return response.status, json.loads(response.read())
    except HTTPError as response:
        return response.code, json.loads(response.read())


def test_import_routes_reference_files_by_dataset_param(tmp_path):
    from test_ingest import workbook_bytes, table_bytes, TOLERANCES, SNAPSHOT
    with running_server(tmp_path) as base:
        status, ds = post_bytes(base, '/api/import?filename=w.xlsx', workbook_bytes())
        assert status == 201 and ds['kind'] == 'workbook'
        status, again = post_bytes(base, f"/api/import?filename=w.xlsx&dataset={ds['id']}", workbook_bytes())
        assert status == 201 and again['id'] == ds['id']
        status, ref = post_bytes(base, f"/api/import?filename=t.xlsx&dataset={ds['id']}", table_bytes({'Sheet3': TOLERANCES}))
        assert status == 201 and ref['kind'] == 'tolerances' and ref['rows'] == 2
        status, ref = post_bytes(base, f"/api/import?filename=s.csv&dataset={ds['id']}&as_of=2026-10-01", b'Material,Storage Bin,Available stock\nPART-A,A1,3\n')
        assert status == 201 and ref['kind'] == 'snapshot' and ref['as_of'] == '2026-10-01'
        assert post_bytes(base, f"/api/import?filename=s.csv&dataset={ds['id']}", b'Material,Storage Bin,Available stock\nPART-A,A1,3\n')[0] == 400
        assert post_bytes(base, '/api/import?filename=bad.xlsx', b'junk')[0] == 400
        assert post_bytes(base, '/api/import?filename=t.xlsx&dataset=demo', table_bytes({'Sheet3': TOLERANCES}))[0] == 400
        insights = json.loads(request(base, f"/api/insights?dataset={ds['id']}")[1])
        assert insights['references']['tolerances']['rows'] == 2 and insights['references']['snapshots'][0]['rows'] == 1
