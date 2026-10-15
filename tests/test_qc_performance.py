"""Independent QC performance tests at real-file size (2,317 positions, ~31k transfers, ~76k production rows).

Synthetic data is generated deterministically (seed 482); real-file timings run only when data/ files exist.
Timings are printed (use -s / -rP). Thresholds are deliberately generous (about 5-10x measured on a laptop)
so they catch order-of-magnitude regressions, not noise.
"""
import time

import pytest

from qc_helpers import (REAL_FILES_PRESENT, alert_cfg, big_workbook_parts, bom_bytes, call, eml_count, full_workbook, real_store,
                        serving, tolerance_bytes)
from siemens_capstone.store import Store

LIMITS = dict(import_workbook=45, attach=5, insights_cold=8, insights_cached=0.25, stock_view=0.5, set_available=0.5, http_stock=1.0)
TIMINGS = {}


def timed(name, fn):
    t = time.perf_counter(); out = fn(); TIMINGS[name] = time.perf_counter() - t
    print(f'PERF {name}: {TIMINGS[name]:.3f}s')
    return out


@pytest.fixture(scope='module')
def big(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('perf')
    bins, tol_rows, transfers, prod, sched, bom_rows = big_workbook_parts()
    t = time.perf_counter()
    workbook = full_workbook(bins, prod, sched, transfers, write_only=True)
    print(f'PERF build_fixture_workbook: {time.perf_counter() - t:.1f}s ({len(workbook) / 1e6:.1f} MB)')
    store = Store(tmp / 'perf.sqlite3', alert_config=alert_cfg(tmp / 'outbox'))
    ds = timed('synthetic_import_workbook', lambda: store.import_workbook(workbook, 'synthetic.xlsx'))['id']
    timed('synthetic_attach_tolerances', lambda: store.import_reference(ds, tolerance_bytes(tol_rows), 'mm.xlsx'))
    timed('synthetic_attach_bom', lambda: store.import_reference(ds, bom_bytes(bom_rows), 'bom.xlsx'))
    return store, ds, tmp


def test_fixture_has_real_size(big):
    store, ds, _ = big
    assert store.record_count(ds) == 2317 + 30972 + 75576 + 6362


def test_import_and_attach_time(big):
    assert TIMINGS['synthetic_import_workbook'] < LIMITS['import_workbook']
    assert TIMINGS['synthetic_attach_tolerances'] + TIMINGS['synthetic_attach_bom'] < LIMITS['attach']


def test_insights_cold_then_cached(big):
    store, ds, _ = big
    with store.connection() as db: db.execute('DELETE FROM analysis_cache WHERE dataset=?', (ds,))
    cold = timed('synthetic_insights_cold', lambda: store.insights(ds))
    cached = timed('synthetic_insights_cached', lambda: store.insights(ds))
    assert cold['inventory_summary']['positions'] == 2317
    assert cached['inventory_summary']['positions'] == 2317
    assert TIMINGS['synthetic_insights_cold'] < LIMITS['insights_cold']
    assert TIMINGS['synthetic_insights_cached'] < LIMITS['insights_cached']
    assert TIMINGS['synthetic_insights_cached'] < TIMINGS['synthetic_insights_cold'], 'cache must help'


def test_stock_view_and_single_edit(big):
    store, ds, _ = big
    view = timed('synthetic_stock_view', lambda: store.stock_view(ds))
    assert view['summary']['positions'] == 2317 and view['summary']['no_tolerance'] == 0
    key = next(p['key'] for p in view['positions'] if p['status'] == 'OK')
    res = timed('synthetic_set_available_first', lambda: store.set_available(ds, key, 0))
    assert res['new'] == 1
    timed('synthetic_set_available_repeat', lambda: store.set_available(ds, key, 0))
    timed('synthetic_digest', lambda: store.stock_digest(ds))
    timed('synthetic_reset', lambda: store.reset_stock(ds))
    assert TIMINGS['synthetic_stock_view'] < LIMITS['stock_view']
    assert TIMINGS['synthetic_set_available_first'] < LIMITS['set_available']
    assert TIMINGS['synthetic_set_available_repeat'] < LIMITS['set_available']


def test_http_stock_endpoint_and_payload_size(big):
    store, ds, _ = big
    with serving(store) as base:
        timed('synthetic_http_get_stock', lambda: call(base, f'/api/stock?dataset={ds}'))
        status, body = call(base, f'/api/stock?dataset={ds}')
        assert status == 200 and len(body['positions']) == 2317
        import json
        print(f'PERF stock payload bytes: {len(json.dumps(body))}')
        timed('synthetic_http_get_insights', lambda: call(base, f'/api/insights?dataset={ds}'))
        status, edit = timed('synthetic_http_post_edit', lambda: call(base, '/api/stock/available', {'dataset': ds, 'position': body['positions'][0]['key'], 'available': 0}))
        assert status == 200
    assert TIMINGS['synthetic_http_get_stock'] < LIMITS['http_stock']
    assert TIMINGS['synthetic_http_post_edit'] < LIMITS['set_available'] * 2


def test_repeated_page_loads_stay_fast_and_silent(big):
    store, ds, tmp = big
    n = eml_count(tmp / 'outbox')
    t = time.perf_counter()
    for _ in range(5): store.stock_view(ds)
    per = (time.perf_counter() - t) / 5
    print(f'PERF stock_view mean of 5: {per:.3f}s')
    assert per < LIMITS['stock_view'] and eml_count(tmp / 'outbox') == n


@pytest.mark.skipif(not REAL_FILES_PRESENT, reason='Siemens files not present in data/')
def test_real_file_timings(tmp_path):
    store, ds, imports = real_store()
    for k, v in imports.items(): print(f'PERF real_{k}: {v:.3f}s')
    with store.connection() as db: db.execute('DELETE FROM analysis_cache WHERE dataset=?', (ds,))
    timed('real_insights_cold', lambda: store.insights(ds))
    timed('real_insights_cached', lambda: store.insights(ds))
    view = timed('real_stock_view', lambda: store.stock_view(ds))
    key = next(p['key'] for p in view['positions'] if p['status'] == 'OK')
    timed('real_set_available', lambda: store.set_available(ds, key, 0))
    store.reset_stock(ds)
    assert imports['import_workbook'] < LIMITS['import_workbook']
    assert TIMINGS['real_insights_cold'] < LIMITS['insights_cold']
    assert TIMINGS['real_insights_cached'] < LIMITS['insights_cached']
    assert TIMINGS['real_stock_view'] < LIMITS['stock_view']
    assert TIMINGS['real_set_available'] < LIMITS['set_available']
