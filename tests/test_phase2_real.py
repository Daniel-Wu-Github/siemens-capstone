"""Optional Phase 2 checks on the real Siemens files: temp DB, read-only, aggregate counts only."""
import time

import pytest

from qc_helpers import REAL_FILES_PRESENT, real_store

pytestmark = pytest.mark.skipif(not REAL_FILES_PRESENT, reason='real Siemens files not present in data/')


def test_real_data_forecast_counts_are_consistent_and_fast():
    store, ds, _ = real_store()
    started = time.perf_counter()
    view = store.stock_view(ds)
    elapsed = time.perf_counter() - started
    print(f'\n[phase2-real] stock_view seconds={elapsed:.2f} positions={len(view["positions"])}')
    positions = view['positions']
    status = lambda s: sum(p['status'] == s for p in positions)
    f = view['forecast']['summary']
    assert set(f) == {'predicted', 'watch', 'below_min', 'no_rate', 'ok'}
    assert f['predicted'] + f['watch'] + f['below_min'] + f['no_rate'] + f['ok'] == len(positions) - status('No tolerance') - status('No stock')
    assert f['below_min'] == status('Alert') == view['summary']['alerts']
    assert f['predicted'] == status('Predicted')
    assert f['watch'] == sum(p['forecast'] == 'watch' for p in positions)
    assert f['ok'] == sum(p['forecast'] == 'ok' for p in positions)
    assert view['summary']['ok'] == status('OK') == f['ok'] + f['watch'] + f['no_rate']
    assert sum(a['kind'] == 'below_min' for a in view['open_alerts']) == f['below_min']
    assert sum(a['kind'] == 'predicted' for a in view['open_alerts']) == f['predicted']
    assert set(view['summary']) == {'positions', 'alerts', 'ok', 'no_tolerance', 'overridden'}
    assert all(p['lead_hours'] == 3.0 and p['lead_class'] == 'stocked' for p in positions)
    assert elapsed < 30, elapsed


def test_real_data_repeat_view_is_quiet():
    store, ds, _ = real_store()
    store.stock_view(ds)
    again = store.stock_view(ds)
    assert again['notification'] is None
