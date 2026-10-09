"""Shared helpers for the independent QC tests (synthetic fixtures only; real files are read-only)."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import atexit
import json
from pathlib import Path
import shutil
import tempfile
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openpyxl import Workbook

from siemens_capstone import alerts
from siemens_capstone.api import handler_for
from siemens_capstone.paths import DATA_DIR
from siemens_capstone.store import Store

REAL_PROD = DATA_DIR / 'Prod_Capstone_Data.xlsx'
REAL_MINMAX = DATA_DIR / 'Min_Max-SM.xlsx'
REAL_BOM = DATA_DIR / 'BOMEXAMPLE.xlsx'
REAL_FILES_PRESENT = all(p.exists() for p in (REAL_PROD, REAL_MINMAX, REAL_BOM))

BIN_HEADER = ['Material', 'Material Description', 'Storage Type', 'Storage Bin', 'Available stock', 'Base Unit of Measure']
TOL_HEADER = ['Part', 'Description', 'Type', 'Location', 'Min', 'Max']
PROD_HEADER = ['salesorderOrdNr', 'productionorderSalesOrderLineItem', 'jobtraceObjid', 'productionorderOrdNr',
               'productionorderStatus', 'TransactionTime', 'productionorderProducedTime', 'statusstringDescription', 'stationDescr']
SCHED_HEADER = ['Sales Order', 'Line Item', 'Basic Start Date']
REPL_HEADER = ['Transfer Order Number', 'Transfer order item', 'Material', 'Source Storage Type', 'Dest. Storage Type',
               'Dest.Storage Bin', 'Dest.target quantity', 'Creation Date', 'Creation time', 'Confirmation Date', 'Confirmation time']


def xlsx_bytes(sheets, write_only=False):
    wb = Workbook(write_only=write_only)
    if not write_only: wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows: ws.append(list(row))
    out = BytesIO(); wb.save(out); return out.getvalue()


def full_workbook(bins, production=None, schedule=None, transfers=None, write_only=False):
    """bins/production/... are lists of data rows (without header)."""
    return xlsx_bytes({
        'HISTORICAL_PROD_DATA': [PROD_HEADER] + list(production or []),
        'PLANNED_PROD_SCHEDULE': [SCHED_HEADER] + list(schedule or []),
        'HISTORICAL_SM_REPLENISHMENTS': [REPL_HEADER] + list(transfers or []),
        'BIN_STATUS_EXAMPLE': [BIN_HEADER] + list(bins),
    }, write_only=write_only)


def tolerance_bytes(rows):
    return xlsx_bytes({'Sheet3': [TOL_HEADER] + list(rows)})


def bom_bytes(rows, with_routings=True):
    sheets = {}
    if with_routings: sheets['Routings'] = [['Operation', 'Workcenter'], ['10', 'WC1']]
    sheets['Full BOM'] = [['Item', 'Description', 'Part', 'Qty', 'Mat Type', 'SubAssembly']] + list(rows)
    return xlsx_bytes(sheets)


def alert_cfg(outbox, **extra):
    return dict(alerts.config({}), outbox=str(outbox), **extra)


def make_store(tmp_path, name='qc.sqlite3', **extra):
    return Store(tmp_path / name, alert_config=alert_cfg(tmp_path / 'outbox', **extra))


@contextmanager
def serving(store):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store))
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}'
    finally: server.shutdown(); server.server_close(); thread.join()


def call(base, path, body=None, headers=None, raw=None):
    """Return (status, parsed JSON or bytes)."""
    hdrs = {} if raw is not None else {'Content-Type': 'application/json'}
    if raw is not None: hdrs['Content-Type'] = 'application/octet-stream'
    hdrs.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = Request(base + path, data=data, headers=hdrs)
    try:
        with urlopen(req) as r: status, content = r.status, r.read()
    except HTTPError as r:
        status, content = r.code, r.read()
    try: return status, json.loads(content)
    except ValueError: return status, content


def eml_count(outbox):
    p = Path(outbox)
    return len(list(p.glob('*.eml'))) if p.exists() else 0


class Sink:
    """Local dummy webhook/Teams receiver; records POST bodies. Never leaves loopback."""
    def __init__(self, status=200):
        self.bodies, self.status = [], status
        sink = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                n = int(self.headers.get('Content-Length', 0))
                sink.bodies.append(json.loads(self.rfile.read(n) or b'null'))
                self.send_response(sink.status); self.send_header('Content-Length', '0'); self.end_headers()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/hook?sig=TOPSECRETSIG'

    def close(self): self.server.shutdown(); self.server.server_close(); self.thread.join()


def analysis(bins, tolerances=None, transfers=(), production=(), schedule=(), bom=None, snapshots=None):
    """Run analytics.analyze on in-memory records (dict rows)."""
    from siemens_capstone.analytics import analyze
    records = {'BIN_STATUS_EXAMPLE': list(bins), 'HISTORICAL_SM_REPLENISHMENTS': list(transfers),
               'HISTORICAL_PROD_DATA': list(production), 'PLANNED_PROD_SCHEDULE': list(schedule)}
    return analyze(records, '1900', {'tolerances': tolerances, 'bom': bom, 'snapshots': snapshots or []})


def bin_row(material, bin_, stock, typ='401', unit='PC', desc='Synthetic part'):
    return {'Material': material, 'Material Description': desc, 'Storage Type': typ, 'Storage Bin': bin_,
            'Available stock': stock, 'Base Unit of Measure': unit}


def tol_row(part, loc, mn, mx, typ=401):
    return {'Part': part, 'Description': 'x', 'Type': typ, 'Location': loc, 'Min': mn, 'Max': mx}


def transfer_row(n, material, bin_, qty, day, src='001', dest='401'):
    d = f'2026-10-{day:02d}T00:00:00'
    return {'Transfer Order Number': str(n), 'Transfer order item': '1', 'Material': material, 'Source Storage Type': src,
            'Dest. Storage Type': dest, 'Dest.Storage Bin': bin_, 'Dest.target quantity': qty,
            '_created_at_local': d, '_confirmed_at_local': d}


# ---- real data (read-only; temp DB; aggregate access only) ------------------------------
_REAL = {}


def real_store():
    """Import the three real files once per process into a temp DB; returns (store, dataset_id, timings)."""
    if _REAL: return _REAL['store'], _REAL['ds'], _REAL['timings']
    tmp = Path(tempfile.mkdtemp(prefix='qc-real-')); atexit.register(shutil.rmtree, tmp, True)
    store = Store(tmp / 'real.sqlite3', alert_config=alert_cfg(tmp / 'outbox'))
    timings = {}
    t = time.perf_counter(); ds = store.import_workbook(REAL_PROD.read_bytes(), 'workbook.xlsx')['id']
    timings['import_workbook'] = time.perf_counter() - t
    for key, path in (('attach_minmax', REAL_MINMAX), ('attach_bom', REAL_BOM)):
        t = time.perf_counter(); store.import_reference(ds, path.read_bytes(), path.name); timings[key] = time.perf_counter() - t
    _REAL.update(store=store, ds=ds, timings=timings)
    return store, ds, timings


# ---- synthetic data at real size ----------------------------------------------------------
def big_workbook_parts(n_pos=2317, n_transfers=30972, n_prod=75576, n_orders=7281, n_sched=6362, n_items=135):
    import random
    rnd = random.Random(482)
    base = datetime(2026, 8, 1)
    bins = [[f'M{i:05d}', 'Synthetic', str(401 + i % 8), f'B{i:05d}', rnd.choice([0, 0, 1, 3, 10, 40, 120]), 'PC'] for i in range(n_pos)]
    tol_rows = [[f'M{i:05d}', 'x', 401 + i % 8, f'B{i:05d}', rnd.choice([0, 0, 2, 5, 20]), rnd.choice([0, 30, 100, 400])] for i in range(n_pos)]
    transfers = []
    for k in range(n_transfers):
        i = rnd.randrange(n_pos); c = base + timedelta(days=rnd.randrange(0, 60), hours=rnd.randrange(24))
        e = c + timedelta(minutes=rnd.randrange(0, 90))
        transfers.append([f'T{k}', 1, f'M{i:05d}', '001', str(401 + i % 8), f'B{i:05d}', rnd.randrange(1, 50), c, 0, e, 0])
    prod = []
    for k in range(n_prod):
        o = k % n_orders; t = base + timedelta(days=rnd.randrange(0, 60), hours=rnd.randrange(24))
        prod.append([f'SO{o}', f'{(o % n_items) + 1:04d}', f'J{k}', f'PO{o}', rnd.choice([10, 20, 70, 70]), t, t, rnd.choice(['Passed', 'On hold']), f'ST{k % 40}'])
    sched = [[f'SO{k}', f'{(k % n_items) + 1:04d}', base + timedelta(days=rnd.randrange(60, 120))] for k in range(n_sched)]
    bom_rows = [[(k % n_items) + 1, 'd', ('US2:' if k % 5 == 0 else '') + f'M{rnd.randrange(n_pos):05d}', rnd.randrange(1, 5), 'ROH', 'N'] for k in range(10159)]
    return bins, tol_rows, transfers, prod, sched, bom_rows
