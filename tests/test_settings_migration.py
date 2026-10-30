"""Settings defaults, watch_hours validation, and migration to SETTINGS_VERSION 2."""
import json
import sqlite3
import pytest

from siemens_capstone import proactive
from siemens_capstone.store import Store
from test_phase2_store_api import build


def test_fresh_store_defaults(tmp_path):
    store, ds = build(tmp_path)
    s = store.stock_settings(ds)
    assert s['safety_hours'] == 24.0
    assert s['watch_hours'] == 72.0
    assert s['lead_hours_stocked'] == 3.0
    assert s['lead_hours_fab'] == 3.0
    assert s['weights'] == {'history': 1.0, 'schedule': 1.0, 'observed': 0.0}
    assert s['divergence_pct'] == 50.0
    assert '_version' not in s


def test_env_watch_hours_override(monkeypatch):
    monkeypatch.setenv('ALERT_WATCH_HOURS', '48')
    s = proactive.default_settings()
    assert s['watch_hours'] == 48.0


def test_watch_hours_out_of_range():
    cur = proactive.default_settings()
    with pytest.raises(ValueError) as exc:
        proactive.validate_settings(cur, {'watch_hours': 9999})
    assert '720' in str(exc.value)


def test_migration_old_defaults_migrated(tmp_path):
    db_path = tmp_path / 'migrate1.sqlite3'
    # Create DB and insert an old stock_settings row without _version
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE stock_settings(dataset TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        old_payload = {
            'lead_hours_stocked': 5.0,
            'lead_hours_fab': 3.0,
            'safety_hours': 0.0,
            'weights': {'history': 1.0, 'schedule': 1.0, 'observed': 1.0},
            'divergence_pct': 50.0
        }
        conn.execute('INSERT INTO stock_settings VALUES (?, ?)', ('ds1', json.dumps(old_payload)))

    # Opening Store should run migration
    store = Store(db_path)
    with store.connection() as db:
        row = db.execute('SELECT payload FROM stock_settings WHERE dataset=?', ('ds1',)).fetchone()
        migrated = json.loads(row['payload'])
        assert migrated.get('_version') == 2
        assert migrated['safety_hours'] == 24.0
        assert migrated['weights']['observed'] == 0.0
        assert migrated['lead_hours_stocked'] == 5.0
        assert migrated['weights']['history'] == 1.0
        assert migrated['divergence_pct'] == 50.0


def test_migration_custom_values_preserved(tmp_path):
    db_path = tmp_path / 'migrate2.sqlite3'
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE stock_settings(dataset TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        custom_payload = {
            'lead_hours_stocked': 3.0,
            'lead_hours_fab': 3.0,
            'safety_hours': 6.0,
            'weights': {'history': 1.0, 'schedule': 1.0, 'observed': 0.5},
            'divergence_pct': 50.0
        }
        conn.execute('INSERT INTO stock_settings VALUES (?, ?)', ('ds2', json.dumps(custom_payload)))

    store = Store(db_path)
    with store.connection() as db:
        row = db.execute('SELECT payload FROM stock_settings WHERE dataset=?', ('ds2',)).fetchone()
        migrated = json.loads(row['payload'])
        assert migrated.get('_version') == 2
        assert migrated['safety_hours'] == 6.0
        assert migrated['weights']['observed'] == 0.5


def test_migration_idempotent_and_user_zero_preserved(tmp_path):
    store, ds = build(tmp_path)
    res = store.update_stock_settings(ds, {'safety_hours': 0.0})
    assert res['settings']['safety_hours'] == 0.0

    # Reopening Store(path) must NOT set safety_hours back to 24.0
    store2 = Store(store.path)
    s2 = store2.stock_settings(ds)
    assert s2['safety_hours'] == 0.0


def test_update_stock_settings_keeps_version_internal(tmp_path):
    store, ds = build(tmp_path)
    res = store.update_stock_settings(ds, {'safety_hours': 12.0})
    assert '_version' not in res['settings']
    assert '_version' not in store.stock_settings(ds)
