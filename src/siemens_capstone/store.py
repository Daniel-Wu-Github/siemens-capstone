"""SQLite persistence for imported workbooks, reference files, stock alerts, settings, and notifications."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import uuid

from .ingest import inspect_reference, inspect_workbook
from .analytics import ANALYSIS_VERSION, analyze
from . import alerts as stock_alerts
from . import proactive
from .paths import OUTBOX


def number(value, name, minimum=0, maximum=1e9):
    if isinstance(value, bool): raise ValueError(f'{name} must be a number.')
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number.') from None
    if not math.isfinite(n) or n < minimum or n > maximum: raise ValueError(f'{name} must be between {minimum} and {maximum}.')
    return n


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':'))


class Store:
    def __init__(self, path, alert_config=None):
        self.path = Path(path)
        self.alert_config = alert_config or stock_alerts.config()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, payload TEXT NOT NULL, quality TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records(dataset TEXT, sheet TEXT, row_number INTEGER, payload TEXT NOT NULL, PRIMARY KEY(dataset,sheet,row_number));
                CREATE TABLE IF NOT EXISTS activity(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, message TEXT);
                CREATE TABLE IF NOT EXISTS analysis_cache(dataset TEXT PRIMARY KEY, version TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS "references"(id TEXT PRIMARY KEY, dataset TEXT, kind TEXT, checksum TEXT, as_of TEXT, created_at TEXT, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS reference_dedupe ON "references"(dataset,kind,checksum,as_of);
                CREATE TABLE IF NOT EXISTS stock_overrides(dataset TEXT, position TEXT, available REAL NOT NULL, updated_at TEXT, PRIMARY KEY(dataset,position));
                CREATE TABLE IF NOT EXISTS stock_alerts(id TEXT PRIMARY KEY, dataset TEXT, position TEXT, status TEXT, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS open_stock_alert ON stock_alerts(dataset,position) WHERE status != 'Resolved';
                CREATE TABLE IF NOT EXISTS stock_settings(dataset TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stock_lead_classes(dataset TEXT, position TEXT, lead_class TEXT NOT NULL, PRIMARY KEY(dataset, position));
                CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def _dataset(self, db, dataset_id):
        row = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if not row: raise ValueError('Dataset not found.')
        return json.loads(row['payload']), json.loads(row['quality'])

    def _event(self, db, dataset_id, message):
        db.execute('INSERT INTO activity VALUES(?,?,?,?)', (uuid.uuid4().hex, dataset_id, now(), message))

    def datasets(self):
        """Imported workbooks, newest last. Rows of other kinds (the retired synthetic sandbox) are ignored."""
        with self.connection() as db:
            rows = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM datasets ORDER BY rowid')]
        return [d for d in rows if d.get('kind') == 'workbook']

    def sources(self, dataset_id):
        """Data sources page: import quality, attached reference files, and recent import activity."""
        with self.connection() as db:
            dataset, quality = self._dataset(db, dataset_id)
            if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook.')
            references = self._insights(db, dataset_id)['references']
            activity = [dict(r) for r in db.execute('SELECT created_at,message FROM activity WHERE dataset=? ORDER BY rowid DESC LIMIT 12', (dataset_id,))]
        return dict(dataset=dataset, quality=quality, references=references, activity=activity)

    def import_workbook(self, content, filename):
        report = inspect_workbook(content, Path(filename).name[:120])
        records = report.pop('records')
        dataset_id = 'xlsx-' + report['checksum'][:16]
        dataset = dict(id=dataset_id, name=report['filename'], kind='workbook', created_at=now(),
                       as_of=None, demand_factor=1, forecast_ready=False, checksum=report['checksum'])
        with self.connection() as db:
            existing = db.execute('SELECT payload FROM datasets WHERE id=?', (dataset_id,)).fetchone()
            if existing: return json.loads(existing['payload'])
            db.execute('INSERT INTO datasets VALUES(?,?,?)', (dataset_id, encoded(dataset), encoded(report)))
            for sheet, rows in records.items():
                db.executemany('INSERT INTO records VALUES(?,?,?,?)', ((dataset_id, sheet, i+2, encoded(row)) for i, row in enumerate(rows)))
            self._event(db, dataset_id, f"Imported {report['total_rows']:,} rows from four expected source sheets. Original values retained in local SQLite.")
        return dataset

    def import_reference(self, dataset_id, content, filename, as_of=None):
        report = inspect_reference(content, Path(filename).name[:120], as_of)
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            if dataset['kind'] != 'workbook': raise ValueError('Reference files attach to an imported workbook dataset.')
            db.execute('INSERT INTO "references" VALUES(?,?,?,?,?,?,?) ON CONFLICT(dataset,kind,checksum,as_of) DO UPDATE SET created_at=excluded.created_at',
                       (uuid.uuid4().hex, dataset_id, report['kind'], report['checksum'], report['as_of'] or '', now(), encoded(report['records'])))
            db.execute('DELETE FROM analysis_cache WHERE dataset=?', (dataset_id,))
            self._event(db, dataset_id, f"Reference file attached ({report['kind']}, {report['rows']:,} rows). Analytics will be recomputed.")
            # New tolerances or a newer snapshot can open or resolve alerts; announce those now, not on the next edit.
            changes = self._sync_changes(db, dataset_id)
        self._announce(dataset_id, changes)
        return dict(kind=report['kind'], dataset_id=dataset_id, rows=report['rows'], as_of=report['as_of'], findings=report['findings'])

    def _references(self, db, dataset_id):
        refs = dict(tolerances=None, bom=None, snapshots=[])
        for kind in ('tolerances', 'bom'):
            row = db.execute('SELECT payload FROM "references" WHERE dataset=? AND kind=? ORDER BY created_at DESC, rowid DESC LIMIT 1', (dataset_id, kind)).fetchone()
            if row: refs[kind] = json.loads(row['payload'])
        refs['snapshots'] = [dict(as_of=r['as_of'], rows=json.loads(r['payload'])) for r in
                             db.execute('SELECT as_of,payload FROM "references" WHERE dataset=? AND kind=\'snapshot\' ORDER BY as_of, created_at', (dataset_id,))]
        return refs

    def record_count(self, dataset_id):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM records WHERE dataset=?', (dataset_id,)).fetchone()[0]

    def _insights(self, db, dataset_id):
        """Cached workbook analysis on an open connection (no scenarios attached)."""
        dataset, quality = self._dataset(db, dataset_id)
        if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook to view source analytics.')
        cached = db.execute('SELECT * FROM analysis_cache WHERE dataset=?', (dataset_id,)).fetchone()
        if cached and cached['version'] == ANALYSIS_VERSION: return json.loads(cached['payload'])
        records = {}
        for row in db.execute('SELECT sheet,payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (dataset_id,)):
            records.setdefault(row['sheet'], []).append(json.loads(row['payload']))
        result = analyze(records, quality.get('date_system', '1900'), self._references(db, dataset_id))
        result.update(dataset_id=dataset_id, source_checksum=dataset['checksum'], created_at=now())
        db.execute('INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?)', (dataset_id, ANALYSIS_VERSION, encoded(result)))
        self._event(db, dataset_id, 'Workbook analytics saved: stock snapshot, transfer cadence, production events, and planned lines.')
        return result

    def insights(self, dataset_id):
        with self.connection() as db:
            return self._insights(db, dataset_id)

    # ---- Phase 1 reactive stock alerts ------------------------------------------------
    def _stock_positions(self, db, dataset_id):
        dataset, _ = self._dataset(db, dataset_id)
        if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook to use stock alerts.')
        refs = self._references(db, dataset_id)
        if refs['snapshots']:
            latest = refs['snapshots'][-1]; rows, as_of = latest['rows'], latest['as_of']
        else:
            rows = [json.loads(r['payload']) for r in db.execute("SELECT payload FROM records WHERE dataset=? AND sheet='BIN_STATUS_EXAMPLE' ORDER BY row_number", (dataset_id,))]
            as_of = None
        overrides = {r['position']: r['available'] for r in db.execute('SELECT position,available FROM stock_overrides WHERE dataset=?', (dataset_id,))}
        items = stock_alerts.evaluate(stock_alerts.positions(rows, refs['tolerances'], overrides), self.alert_config['rule'])
        lead_classes = {r['position']: r['lead_class'] for r in db.execute('SELECT position,lead_class FROM stock_lead_classes WHERE dataset=?', (dataset_id,))}
        proactive.project(items, self._insights(db, dataset_id)['inventory'], self._settings(db, dataset_id), lead_classes)
        return items, as_of, refs['tolerances'] is not None

    def _settings(self, db, dataset_id):
        row = db.execute('SELECT payload FROM stock_settings WHERE dataset=?', (dataset_id,)).fetchone()
        base = proactive.default_settings()
        return proactive.validate_settings(base, json.loads(row['payload'])) if row else base

    RESOLUTIONS = {'OK': 'recovered', 'No tolerance': 'minimum no longer set', 'No stock': 'no stock reading'}

    def _sync_stock_alerts(self, db, dataset_id, items):
        """Open one alert per position in breach; resolve every open alert whose position is no longer in breach."""
        open_rows = {r['position']: r for r in db.execute("SELECT * FROM stock_alerts WHERE dataset=? AND status != 'Resolved'", (dataset_id,))}
        new, resolved = [], []
        for p in items:
            row = open_rows.pop(p['key'], None)
            snapshot = {k: p[k] for k in ('material', 'description', 'supermarket', 'storage_type', 'bin', 'unit', 'available', 'min', 'max', 'note', 'overridden',
                                          'hours_to_min', 'lead_hours', 'suggested_qty', 'demand_per_day')}
            if p['status'] in ('Alert', 'Predicted'):
                kind = 'below_min' if p['status'] == 'Alert' else 'predicted'
                if row:
                    payload = json.loads(row['payload']); old = payload.get('kind', 'below_min')
                    payload.update(snapshot, kind=kind, updated_at=now())
                    if old == 'predicted' and kind == 'below_min':  # escalation: the forecast came true
                        payload.update(status='Open', escalated_at=now())
                        db.execute("UPDATE stock_alerts SET status='Open', payload=? WHERE id=?", (encoded(payload), row['id']))
                        new.append(dict(payload, escalated=True)); continue
                    db.execute('UPDATE stock_alerts SET payload=? WHERE id=?', (encoded(payload), row['id']))
                else:
                    alert = dict(snapshot, id=uuid.uuid4().hex, position=p['key'], status='Open', kind=kind, created_at=now())
                    db.execute('INSERT INTO stock_alerts VALUES(?,?,?,?,?)', (alert['id'], dataset_id, p['key'], 'Open', encoded(alert)))
                    new.append(alert)
            elif row:
                payload = json.loads(row['payload'])
                resolution = 'forecast cleared' if payload.get('kind') == 'predicted' and p['status'] == 'OK' else self.RESOLUTIONS[p['status']]
                payload.update(snapshot, status='Resolved', resolved_at=now(), resolution=resolution)
                db.execute("UPDATE stock_alerts SET status='Resolved', payload=? WHERE id=?", (encoded(payload), row['id']))
                resolved.append(payload)
        for row in open_rows.values():  # positions that left the snapshot
            payload = json.loads(row['payload']); payload.update(status='Resolved', resolved_at=now(), resolution='position not in current snapshot')
            db.execute("UPDATE stock_alerts SET status='Resolved', payload=? WHERE id=?", (encoded(payload), row['id']))
            resolved.append(payload)
        return new, resolved

    def _sync_changes(self, db, dataset_id):
        """Sync alerts with the current positions; return what changed for _announce (None if nothing did)."""
        dataset, _ = self._dataset(db, dataset_id)
        if dataset['kind'] != 'workbook': return None
        items, as_of, _ = self._stock_positions(db, dataset_id)
        new, resolved = self._sync_stock_alerts(db, dataset_id, items)
        if not (new or resolved): return None
        return dict(as_of=as_of, new=new, resolved=resolved, open_alerts=self._open_stock_alerts(db, dataset_id))

    def _announce(self, dataset_id, changes):
        """Every alert change is notified, whatever triggered it. Called after the transaction commits."""
        if not changes: return None
        return self._notify(dataset_id, changes['as_of'], changes['new'], changes['resolved'], changes['open_alerts'])

    def _open_stock_alerts(self, db, dataset_id):
        return [dict(json.loads(r['payload']), status=r['status']) for r in
                db.execute("SELECT status,payload FROM stock_alerts WHERE dataset=? AND status != 'Resolved' ORDER BY rowid", (dataset_id,))]

    def _notify(self, dataset_id, as_of, new, resolved, open_alerts, digest=False):
        cfg = self.alert_config
        p = stock_alerts.payload(dataset_id, cfg['rule'], as_of, new, resolved, open_alerts, cfg['dashboard_url'] or None)
        sent = stock_alerts.deliver(p, cfg, OUTBOX, digest=open_alerts if digest else None)
        record = dict(id=p['run_id'], created_at=p['created_at'], summary=p['summary'], digest=digest, **sent)
        with self.connection() as db:
            db.execute('INSERT INTO notifications VALUES(?,?,?,?)', (record['id'], dataset_id, record['created_at'], encoded(record)))
            self._event(db, dataset_id, f"Stock alert notification: {record['subject']} ({', '.join(r['channel'] + ' ' + r['status'] for r in record['results'])}).")
        return record

    def stock_view(self, dataset_id):
        with self.connection() as db:
            changes = self._sync_changes(db, dataset_id)
            items, as_of, has_tol = self._stock_positions(db, dataset_id)
            open_alerts = self._open_stock_alerts(db, dataset_id)
            notes = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM notifications WHERE dataset=? ORDER BY rowid DESC LIMIT 5', (dataset_id,))]
            settings = self._settings(db, dataset_id)
        counts = dict(predicted=0, below_min=0, no_rate=0, ok=0)
        for p in items:
            key = {'within_lead': 'predicted'}.get(p['forecast'], p['forecast'])
            if key in counts: counts[key] += 1
        return dict(dataset_id=dataset_id, snapshot_as_of=as_of, tolerances_loaded=has_tol, positions=items,
                    summary=dict(positions=len(items), alerts=sum(p['status'] == 'Alert' for p in items),
                                 ok=sum(p['status'] == 'OK' for p in items), no_tolerance=sum(p['status'] == 'No tolerance' for p in items),
                                 overridden=sum(p['overridden'] for p in items)),
                    forecast=dict(settings=settings, summary=counts),
                    open_alerts=open_alerts, notifications=notes, channels=stock_alerts.public_config(self.alert_config),
                    notification=self._announce(dataset_id, changes))

    def set_available(self, dataset_id, position, available):
        """Simulate a stock reading for one position. Source files and imported rows are never changed."""
        with self.connection() as db:
            baseline = self._sync_changes(db, dataset_id)  # changes that predate this edit get their own notification
            items, as_of, _ = self._stock_positions(db, dataset_id)
            if not any(p['key'] == position for p in items): raise ValueError('Stock position not found.')
            if available is None:
                db.execute('DELETE FROM stock_overrides WHERE dataset=? AND position=?', (dataset_id, position))
            else:
                value = number(available, 'Available stock', 0, 1e6)
                db.execute('INSERT INTO stock_overrides VALUES(?,?,?,?) ON CONFLICT(dataset,position) DO UPDATE SET available=excluded.available, updated_at=excluded.updated_at',
                           (dataset_id, position, value, now()))
            items, as_of, _ = self._stock_positions(db, dataset_id)
            new, resolved = self._sync_stock_alerts(db, dataset_id, items)
            open_alerts = self._open_stock_alerts(db, dataset_id)
            self._event(db, dataset_id, f"Simulated available stock {'reset' if available is None else 'set to ' + stock_alerts.fmt(float(available))} for {position.split('|')[0]}.")
        self._announce(dataset_id, baseline)
        notification = self._notify(dataset_id, as_of, new, resolved, open_alerts) if new or resolved else None
        return dict(new=len(new), resolved=len(resolved), notification=notification)

    def reset_stock(self, dataset_id):
        with self.connection() as db:
            baseline = self._sync_changes(db, dataset_id)
            db.execute('DELETE FROM stock_overrides WHERE dataset=?', (dataset_id,))
            changes = self._sync_changes(db, dataset_id)
            self._event(db, dataset_id, 'Simulated stock edits cleared; source snapshot values restored.')
        self._announce(dataset_id, baseline)
        notification = self._announce(dataset_id, changes)
        return dict(new=len(changes['new']) if changes else 0, resolved=len(changes['resolved']) if changes else 0, notification=notification)

    def stock_digest(self, dataset_id):
        with self.connection() as db:
            changes = self._sync_changes(db, dataset_id)
            items, as_of, _ = self._stock_positions(db, dataset_id)
            open_alerts = self._open_stock_alerts(db, dataset_id)
        self._announce(dataset_id, changes)
        return dict(notification=self._notify(dataset_id, as_of, [], [], open_alerts, digest=True))

    def _changed(self, dataset_id, write):
        """Announce pending changes, apply `write(db)`, sync, and announce what it caused. Returns (extra, counts)."""
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook to use stock alerts.')
            baseline = self._sync_changes(db, dataset_id)
            extra = write(db)
            changes = self._sync_changes(db, dataset_id)
        self._announce(dataset_id, baseline)
        notification = self._announce(dataset_id, changes)
        return extra, dict(new=len(changes['new']) if changes else 0, resolved=len(changes['resolved']) if changes else 0, notification=notification)

    def stock_settings(self, dataset_id):
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook to use stock alerts.')
            return self._settings(db, dataset_id)

    def update_stock_settings(self, dataset_id, changes):
        def write(db):
            settings = proactive.validate_settings(self._settings(db, dataset_id), changes)
            db.execute('INSERT INTO stock_settings VALUES(?,?) ON CONFLICT(dataset) DO UPDATE SET payload=excluded.payload', (dataset_id, encoded(settings)))
            self._event(db, dataset_id, 'Forecast settings saved.')
            return settings
        settings, result = self._changed(dataset_id, write)
        return dict(settings=settings, **result)

    def set_lead_class(self, dataset_id, position, lead_class):
        if lead_class not in ('stocked', 'fab', None): raise ValueError('Lead class must be stocked or fab.')
        def write(db):
            if not any(p['key'] == position for p in self._stock_positions(db, dataset_id)[0]): raise ValueError('Stock position not found.')
            if lead_class is None: db.execute('DELETE FROM stock_lead_classes WHERE dataset=? AND position=?', (dataset_id, position))
            else: db.execute('INSERT INTO stock_lead_classes VALUES(?,?,?) ON CONFLICT(dataset,position) DO UPDATE SET lead_class=excluded.lead_class', (dataset_id, position, lead_class))
            self._event(db, dataset_id, f"Lead class {lead_class or 'reset'} for {position.split('|')[0]}.")
        return self._changed(dataset_id, write)[1]

    def run_stock_check(self, dataset_id):
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook to use stock alerts.')
            changes = self._sync_changes(db, dataset_id)
        return dict(new=len(changes['new']) if changes else 0, resolved=len(changes['resolved']) if changes else 0,
                    notification=self._announce(dataset_id, changes), checked_at=now())

    def acknowledge_stock_alert(self, alert_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM stock_alerts WHERE id=?', (alert_id,)).fetchone()
            if not row or row['status'] != 'Open': raise ValueError('Only an open alert can be acknowledged.')
            payload = json.loads(row['payload']); payload.update(status='Acknowledged', acknowledged_at=now())
            db.execute("UPDATE stock_alerts SET status='Acknowledged', payload=? WHERE id=?", (encoded(payload), alert_id))
            self._event(db, row['dataset'], f"Stock alert acknowledged for {payload['material']} / {payload['bin']}.")
        return dict(ok=True)
