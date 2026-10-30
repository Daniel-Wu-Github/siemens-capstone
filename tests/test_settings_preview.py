"""Preview and preset contracts; synthetic data, temporary DBs, no delivery."""
import json
import pytest

from qc_helpers import call, eml_count, serving, xlsx_bytes
from test_phase2_store_api import build


def snapshot(store):
    with store.connection() as db:
        return '\n'.join(db.iterdump())


def observed_fixture(tmp_path):
    store, ds = build(tmp_path)
    header = ['Material', 'Storage Type', 'Storage Bin', 'Available stock']
    for day, value in [('2026-10-01', 100), ('2026-10-11', 60)]:
        content = xlsx_bytes({'Snapshot': [header, ['SAFE', '401', 'BS', value]]})
        store.import_reference(ds, content, 'snapshot.xlsx', day)
    return store, ds


def test_preview_changes_counts_with_observed_and_history_weights(tmp_path):
    store, ds = observed_fixture(tmp_path)
    store.update_stock_settings(ds, {'safety_hours': 0, 'weights': {'history': 0, 'schedule': 0, 'observed': 1}})
    before = store.stock_settings(ds)
    report = store.preview_settings(ds, {'lead_hours_stocked': 48, 'weights': {'history': 1, 'observed': 0}})
    assert report['current']['settings'] == before
    assert report['current']['summary']['predicted'] == 0
    assert report['candidate']['summary']['predicted'] == 1
    assert report['candidate']['settings']['weights']['history'] == 1
    assert store.stock_settings(ds) == before
    assert report['changed'] == [dict(key='SAFE|401|BS', material='SAFE', bin='BS',
                                      **{'from': 'ok', 'to': 'within_lead'}, hours_to_min=40.0)]
    assert set(report['candidate']['summary']) == {'predicted', 'watch', 'below_min', 'no_rate', 'ok'}


@pytest.mark.parametrize('cold', [False, True])
def test_preview_writes_nothing_even_on_cold_analysis_cache(tmp_path, monkeypatch, cold):
    store, ds = build(tmp_path)
    if cold:
        with store.connection() as db:
            db.execute('DELETE FROM analysis_cache')
    before, mails = snapshot(store), eml_count(tmp_path / 'outbox')
    monkeypatch.setattr(store, '_announce', lambda *a: pytest.fail('Preview must not announce'))
    monkeypatch.setattr(store, '_sync_stock_alerts', lambda *a: pytest.fail('Preview must not sync alerts'))
    report = store.preview_settings(ds, {'weights': {'history': 2}})
    assert report['current']['settings']['weights']['history'] == 1
    assert report['candidate']['settings']['weights']['history'] == 2
    assert snapshot(store) == before
    assert eml_count(tmp_path / 'outbox') == mails


def test_preview_analyses_the_workbook_at_most_once_on_a_cold_cache(tmp_path, monkeypatch):
    from siemens_capstone import store as store_module
    store, ds = build(tmp_path)
    with store.connection() as db:
        db.execute('DELETE FROM analysis_cache')
    calls, real = [], store_module.analyze
    monkeypatch.setattr(store_module, 'analyze', lambda *a, **k: calls.append(1) or real(*a, **k))
    store.preview_settings(ds, {'weights': {'history': 2}})
    assert len(calls) == 1


def test_preview_changed_only_tier_changes_sorted_capped(tmp_path, monkeypatch):
    store, ds = build(tmp_path)
    def positions(db, dataset_id, settings=None, **kwargs):
        candidate = settings['weights']['history'] == 2
        rows = [dict(key=str(i), material=str(i), bin='B', forecast='within_lead' if candidate else 'ok',
                     hours_to_min=100 - i) for i in range(60)]
        rows.append(dict(key='same', material='same', bin='B', forecast='no_rate', hours_to_min=None))
        rows.append(dict(key='missing', material='missing', bin='B', forecast='no_rate' if candidate else 'ok', hours_to_min=None))
        return rows, None, True
    monkeypatch.setattr(store, '_stock_positions', positions)
    report = store.preview_settings(ds, {'weights': {'history': 2}})
    assert len(report['changed']) == 50
    assert [p['hours_to_min'] for p in report['changed']] == list(range(41, 91))
    assert all(p['from'] == 'ok' and p['to'] == 'within_lead' for p in report['changed'])


def test_presets_roundtrip_overwrite_isolated_and_does_not_apply(tmp_path):
    store, ds = build(tmp_path)
    before = store.stock_settings(ds)
    assert store.list_presets(ds) == []
    saved = store.save_preset(ds, '  History 2:1  ', {'weights': {'history': 2}})
    assert saved['name'] == 'History 2:1' and saved['created_at']
    assert saved['settings']['weights']['history'] == 2
    assert saved['settings']['weights']['schedule'] == before['weights']['schedule']
    assert store.list_presets(ds) == [saved]
    saved = store.save_preset(ds, saved['name'], {'weights': {'history': 3}})
    assert store.list_presets(ds) == [saved]
    assert store.stock_settings(ds) == before
    assert store.delete_preset(ds, saved['name']) == {'ok': True}
    assert store.list_presets(ds) == []


@pytest.mark.parametrize('name', ['', '   ', 'x' * 41, None, 3, [], {}])
def test_bad_preset_name_rejected(tmp_path, name):
    store, ds = build(tmp_path)
    with pytest.raises(ValueError, match='name'):
        store.save_preset(ds, name, {})


def test_preview_and_preset_http_contracts(tmp_path):
    store, ds = build(tmp_path)
    with serving(store) as base:
        status, report = call(base, '/api/stock/preview', {'dataset': ds, 'weights': {'history': 2}})
        assert status == 200 and report['candidate']['settings']['weights']['history'] == 2
        status, error = call(base, '/api/stock/preview', {'dataset': ds, 'divergence_pct': -1})
        assert status == 400 and 'divergence_pct' in error['error']
        status, saved = call(base, '/api/stock/presets', {'dataset': ds, 'name': 'Two', 'settings': {'weights': {'history': 2}}})
        assert status == 200 and saved['name'] == 'Two'
        assert call(base, '/api/stock/presets?dataset=' + ds) == (200, [saved])
        assert call(base, '/api/stock/presets/delete', {'dataset': ds, 'name': 'Two'}) == (200, {'ok': True})
        assert call(base, '/api/stock/presets?dataset=' + ds) == (200, [])
        assert call(base, '/api/stock/presets', {'dataset': ds, 'name': ' '})[0] == 400
        assert call(base, '/api/stock/presets', {'dataset': ds, 'name': 'bad', 'settings': {'unknown': 1}})[0] == 400
        for path, body in [('/api/stock/preview', {}), ('/api/stock/presets', {'name': 'x', 'settings': {}}),
                           ('/api/stock/presets/delete', {'name': 'x'})]:
            status, error = call(base, path, dict(body, dataset='missing'))
            assert status == 400 and 'not found' in error['error']
        assert call(base, '/api/stock/presets?dataset=missing')[0] == 400


def test_preview_watch_transitions_and_preset_watch_roundtrip(tmp_path):
    store, ds = build(tmp_path)
    store.update_stock_settings(ds, {'safety_hours': 24, 'watch_hours': 72})
    before = snapshot(store)
    report = store.preview_settings(ds, {'watch_hours': 30})
    assert report['current']['summary']['watch'] == 1
    assert report['candidate']['summary']['watch'] == 0
    assert report['changed'] == [dict(key='SAFE|401|BS', material='SAFE', bin='BS',
                                     **{'from': 'watch', 'to': 'ok'}, hours_to_min=40.0)]
    assert snapshot(store) == before
    preset = store.save_preset(ds, 'Short watch', {'watch_hours': 30})
    assert preset['settings']['watch_hours'] == 30
    assert '_version' not in preset['settings']
    assert store.list_presets(ds) == [preset]


def test_presets_are_isolated_by_dataset(tmp_path):
    from qc_helpers import full_workbook
    store, ds = build(tmp_path)
    other = store.import_workbook(full_workbook([['OTHER', 'synthetic', '401', 'B', 12, 'PC']]), 'other.xlsx')['id']
    first = store.save_preset(ds, 'Same name', {'weights': {'history': 2}})
    second = store.save_preset(other, 'Same name', {'weights': {'history': 3}})
    assert store.list_presets(ds) == [first]
    assert store.list_presets(other) == [second]
    store.delete_preset(ds, 'Same name')
    assert store.list_presets(ds) == []
    assert store.list_presets(other) == [second]
