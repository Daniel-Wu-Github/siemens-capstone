"""SQLite persistence for datasets, policies, runs, evaluations, and alert receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .engine import AS_OF, VERSION, demo_materials, evaluate, number, predict, synthetic_episodes
from .ingest import inspect_reference, inspect_workbook
from .analytics import ANALYSIS_VERSION, analyze, scenario
from . import alerts as stock_alerts
from .paths import OUTBOX


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
                CREATE TABLE IF NOT EXISTS materials(dataset TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(dataset,id));
                CREATE TABLE IF NOT EXISTS records(dataset TEXT, sheet TEXT, row_number INTEGER, payload TEXT NOT NULL, PRIMARY KEY(dataset,sheet,row_number));
                CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, dataset TEXT, material_id TEXT, status TEXT, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS active_alert ON alerts(dataset,material_id) WHERE status != 'Delivered';
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activity(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, message TEXT);
                CREATE TABLE IF NOT EXISTS analysis_cache(dataset TEXT PRIMARY KEY, version TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS "references"(id TEXT PRIMARY KEY, dataset TEXT, kind TEXT, checksum TEXT, as_of TEXT, created_at TEXT, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS reference_dedupe ON "references"(dataset,kind,checksum,as_of);
                CREATE TABLE IF NOT EXISTS workbook_scenarios(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stock_overrides(dataset TEXT, position TEXT, available REAL NOT NULL, updated_at TEXT, PRIMARY KEY(dataset,position));
                CREATE TABLE IF NOT EXISTS stock_alerts(id TEXT PRIMARY KEY, dataset TEXT, position TEXT, status TEXT, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS open_stock_alert ON stock_alerts(dataset,position) WHERE status != 'Resolved';
                CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, dataset TEXT, created_at TEXT, payload TEXT NOT NULL);
            ''')
            if not db.execute("SELECT id FROM datasets WHERE id='demo'").fetchone():
                dataset = dict(id='demo', name='Factory sandbox', kind='synthetic', as_of=AS_OF,
                               demand_factor=1, created_at=now(), forecast_ready=True)
                quality = dict(total_rows=288, findings=[], joins=[], blockers=[], forecast_ready=True,
                               sheets=[dict(name='Generated hourly consumption', rows=288, columns=4,
                                            fields=['at', 'used', 'load', 'stock'], grain='material + hour')],
                               description='Deterministic synthetic data; no Siemens records. Seed 482.')
                db.execute('INSERT INTO datasets VALUES(?,?,?)', ('demo', encoded(dataset), encoded(quality)))
                for m in demo_materials():
                    db.execute('INSERT INTO materials VALUES(?,?,?)', ('demo', m['id'], encoded(m)))
                self._event(db, 'demo', 'Synthetic factory sandbox created. No Siemens records are included.')
        # Initial outputs exist on first launch. Reopening preserves existing results.
        with self.connection() as db:
            has_runs = db.execute("SELECT 1 FROM runs WHERE dataset='demo'").fetchone()
            has_eval = db.execute("SELECT 1 FROM evaluations WHERE dataset='demo'").fetchone()
        if not has_runs: self.forecast('demo')
        if not has_eval: self.evaluate('demo')

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

    def _materials(self, db, dataset_id):
        return [json.loads(r['payload']) for r in db.execute('SELECT payload FROM materials WHERE dataset=? ORDER BY id', (dataset_id,))]

    def _event(self, db, dataset_id, message):
        db.execute('INSERT INTO activity VALUES(?,?,?,?)', (uuid.uuid4().hex, dataset_id, now(), message))

    def _forecast(self, db, dataset_id):
        dataset, _ = self._dataset(db, dataset_id)
        if not dataset['forecast_ready']:
            raise ValueError('This workbook lacks the verified consumption and stock history required for forecasting.')
        rows = []
        for material in self._materials(db, dataset_id):
            material['demand_factor'] = dataset['demand_factor']
            p = predict(material, material['history'], dataset['as_of'])
            baseline = predict(material, material['history'], dataset['as_of'], 'baseline')
            rows.append(dict(id=material['id'], forecast=p, baseline=baseline))
            active = db.execute("SELECT * FROM alerts WHERE dataset=? AND material_id=? AND status!='Delivered'", (dataset_id, material['id'])).fetchone()
            if p['risk'] in ('Critical', 'Watch'):
                payload = dict(material_id=material['id'], material_name=material['name'], bin=material['bin'],
                               area=material['area'], risk=p['risk'], reason=p['reason'], crossing_at=p['crossing_at'],
                               lead_hours=material['lead_hours'], created_at=now(), forecast_as_of=dataset['as_of'])
                if active:
                    previous = json.loads(active['payload']); payload['created_at'] = previous['created_at']
                    db.execute('UPDATE alerts SET payload=? WHERE id=?', (encoded(payload), active['id']))
                else:
                    db.execute('INSERT INTO alerts VALUES(?,?,?,?,?)', (uuid.uuid4().hex, dataset_id, material['id'], 'Open', encoded(payload)))
                    self._event(db, dataset_id, f"Replenishment recommendation opened for {material['id']}.")
            elif active:
                previous = json.loads(active['payload'])
                previous.update(risk=p['risk'], reason='Risk changed after the latest forecast. Review the existing recommendation before delivery.', crossing_at=p['crossing_at'])
                db.execute('UPDATE alerts SET payload=? WHERE id=?', (encoded(previous), active['id']))
        run = dict(id=uuid.uuid4().hex, created_at=now(), as_of=dataset['as_of'], engine_version=VERSION,
                   data_kind=dataset['kind'], demand_factor=dataset['demand_factor'], predictions=rows,
                   policies=[{k:m[k] for k in ('id', 'stock', 'threshold', 'lead_hours', 'buffer_hours', 'scheduled_load', 'snapshot_at')} for m in self._materials(db, dataset_id)])
        db.execute('INSERT INTO runs VALUES(?,?,?,?)', (run['id'], dataset_id, run['created_at'], encoded(run)))
        return run

    def datasets(self):
        with self.connection() as db:
            return [json.loads(r['payload']) for r in db.execute('SELECT payload FROM datasets ORDER BY rowid')]

    def state(self, dataset_id):
        with self.connection() as db:
            dataset, quality = self._dataset(db, dataset_id)
            materials = self._materials(db, dataset_id)
            for m in materials:
                m['demand_factor'] = dataset['demand_factor']
                m['forecast'] = predict(m, m['history'], dataset['as_of'])
                m['baseline'] = predict(m, m['history'], dataset['as_of'], 'baseline')
            alerts = [dict(json.loads(r['payload']), id=r['id'], status=r['status']) for r in db.execute('SELECT * FROM alerts WHERE dataset=? ORDER BY rowid DESC', (dataset_id,))]
            alerts.sort(key=lambda a: (a['status'] == 'Delivered', a.get('crossing_at') or '9999', a['material_id']))
            evaluation = db.execute('SELECT payload FROM evaluations WHERE dataset=? ORDER BY rowid DESC LIMIT 1', (dataset_id,)).fetchone()
            activity = [dict(r) for r in db.execute('SELECT created_at,message FROM activity WHERE dataset=? ORDER BY rowid DESC LIMIT 30', (dataset_id,))]
            run_count = db.execute('SELECT COUNT(*) FROM runs WHERE dataset=?', (dataset_id,)).fetchone()[0]
            return dict(dataset=dataset, quality=quality, materials=materials, alerts=alerts, activity=activity,
                        evaluation=json.loads(evaluation['payload']) if evaluation else None, run_count=run_count)

    def forecast(self, dataset_id):
        with self.connection() as db:
            run = self._forecast(db, dataset_id)
            self._event(db, dataset_id, 'Forecast run saved with policies and scenario load.')
            return run

    def update_policy(self, dataset_id, material_id, policy):
        clean = dict(threshold=number(policy.get('threshold'), 'Threshold', 0, 1e6),
                     lead_hours=number(policy.get('lead_hours'), 'Lead time', 0.1, 168),
                     buffer_hours=number(policy.get('buffer_hours'), 'Buffer', 0, 168))
        with self.connection() as db:
            row = db.execute('SELECT payload FROM materials WHERE dataset=? AND id=?', (dataset_id, material_id)).fetchone()
            if not row: raise ValueError('Material not found.')
            m = json.loads(row['payload']); m.update(clean)
            db.execute('UPDATE materials SET payload=? WHERE dataset=? AND id=?', (encoded(m), dataset_id, material_id))
            self._event(db, dataset_id, f'Policy saved for {material_id}.')
            self._forecast(db, dataset_id)
        return clean

    def scenario(self, dataset_id, factor):
        factor = number(factor, 'Demand factor', 0.25, 3)
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            if dataset['kind'] != 'synthetic': raise ValueError('Scenario controls apply only to the synthetic sandbox.')
            dataset['demand_factor'] = factor
            db.execute('UPDATE datasets SET payload=? WHERE id=?', (encoded(dataset), dataset_id))
            self._forecast(db, dataset_id)
            self._event(db, dataset_id, f'Scenario production load set to {factor:g}×.')

    def alert_action(self, alert_id, action, quantity=None):
        transitions = {'Open': ('Acknowledged', 'Dispatched'), 'Acknowledged': ('Dispatched',), 'Dispatched': ('Delivered',)}
        with self.connection() as db:
            row = db.execute('SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone()
            if not row: raise ValueError('Alert not found.')
            if action not in transitions.get(row['status'], ()):
                raise ValueError('Invalid transition. A delivery requires a dispatched recommendation and can be recorded once.')
            payload = json.loads(row['payload'])
            if action == 'Delivered':
                qty = number(quantity, 'Delivered quantity', 1, 1e6)
                if not qty.is_integer(): raise ValueError('Count units require a whole delivered quantity.')
                m = json.loads(db.execute('SELECT payload FROM materials WHERE dataset=? AND id=?', (row['dataset'], row['material_id'])).fetchone()['payload'])
                m['stock'] += qty
                dataset, _ = self._dataset(db, row['dataset'])
                m['snapshot_at'] = dataset['as_of']
                payload['delivered_quantity'] = qty; payload['delivered_at'] = now()
                db.execute('UPDATE materials SET payload=? WHERE dataset=? AND id=?', (encoded(m), row['dataset'], row['material_id']))
            db.execute('UPDATE alerts SET status=?,payload=? WHERE id=?', (action, encoded(payload), alert_id))
            self._event(db, row['dataset'], f"{row['material_id']}: {action}" + (f' · +{quantity} pieces received in the simulation.' if action == 'Delivered' else '.'))
            if action == 'Delivered': self._forecast(db, row['dataset'])

    def evaluate(self, dataset_id):
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            if dataset['kind'] != 'synthetic':
                raise ValueError('No verified historical depletion labels exist for this workbook. Evaluation is available on the synthetic sandbox only.')
            result = evaluate(synthetic_episodes())
            result.update(id=uuid.uuid4().hex, created_at=now())
            db.execute('INSERT INTO evaluations VALUES(?,?,?,?)', (result['id'], dataset_id, result['created_at'], encoded(result)))
            self._event(db, dataset_id, 'Both estimators evaluated on 60 fixed synthetic holdout episodes.')
            return result

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

    def insights(self, dataset_id):
        with self.connection() as db:
            dataset, quality = self._dataset(db, dataset_id)
            if dataset['kind'] != 'workbook': raise ValueError('Select an imported workbook to view source analytics.')
            cached = db.execute('SELECT * FROM analysis_cache WHERE dataset=?', (dataset_id,)).fetchone()
            if cached and cached['version'] == ANALYSIS_VERSION:
                result = json.loads(cached['payload'])
            else:
                records = {}
                for row in db.execute('SELECT sheet,payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (dataset_id,)):
                    records.setdefault(row['sheet'], []).append(json.loads(row['payload']))
                result = analyze(records, quality.get('date_system', '1900'), self._references(db, dataset_id))
                result.update(dataset_id=dataset_id, source_checksum=dataset['checksum'], created_at=now())
                db.execute('INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?)', (dataset_id, ANALYSIS_VERSION, encoded(result)))
                self._event(db, dataset_id, 'Workbook analytics saved: stock snapshot, transfer cadence, production events, and planned lines.')
            result['scenarios'] = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM workbook_scenarios WHERE dataset=? ORDER BY rowid DESC LIMIT 10', (dataset_id,))]
            return result

    def workbook_scenario(self, dataset_id, position_id, usage_multiplier, minimum_stock, lead_hours):
        analysis = self.insights(dataset_id)
        result = scenario(analysis, position_id, usage_multiplier, minimum_stock, lead_hours)
        result.update(id=uuid.uuid4().hex, dataset_id=dataset_id, created_at=now(),
                      source_checksum=analysis['source_checksum'], analysis_version=ANALYSIS_VERSION)
        with self.connection() as db:
            db.execute('INSERT INTO workbook_scenarios VALUES(?,?,?,?)', (result['id'], dataset_id, result['created_at'], encoded(result)))
            self._event(db, dataset_id, f"Assumption-based scenario saved for {result['material']} / {result['bin']}. Source stock is unchanged.")
        return result

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
        return items, as_of, refs['tolerances'] is not None

    RESOLUTIONS = {'OK': 'recovered', 'No tolerance': 'minimum no longer set', 'No stock': 'no stock reading'}

    def _sync_stock_alerts(self, db, dataset_id, items):
        """Open one alert per position in breach; resolve every open alert whose position is no longer in breach."""
        open_rows = {r['position']: r for r in db.execute("SELECT * FROM stock_alerts WHERE dataset=? AND status != 'Resolved'", (dataset_id,))}
        new, resolved = [], []
        for p in items:
            row = open_rows.pop(p['key'], None)
            snapshot = {k: p[k] for k in ('material', 'description', 'supermarket', 'storage_type', 'bin', 'unit', 'available', 'min', 'max', 'note', 'overridden')}
            if p['status'] == 'Alert':
                if row:
                    payload = json.loads(row['payload']); payload.update(snapshot, updated_at=now())
                    db.execute('UPDATE stock_alerts SET payload=? WHERE id=?', (encoded(payload), row['id']))
                else:
                    alert = dict(snapshot, id=uuid.uuid4().hex, position=p['key'], status='Open', created_at=now())
                    db.execute('INSERT INTO stock_alerts VALUES(?,?,?,?,?)', (alert['id'], dataset_id, p['key'], 'Open', encoded(alert)))
                    new.append(alert)
            elif row:
                payload = json.loads(row['payload']); payload.update(snapshot, status='Resolved', resolved_at=now(), resolution=self.RESOLUTIONS[p['status']])
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
        return dict(dataset_id=dataset_id, snapshot_as_of=as_of, tolerances_loaded=has_tol, positions=items,
                    summary=dict(positions=len(items), alerts=sum(p['status'] == 'Alert' for p in items),
                                 ok=sum(p['status'] == 'OK' for p in items), no_tolerance=sum(p['status'] == 'No tolerance' for p in items),
                                 overridden=sum(p['overridden'] for p in items)),
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

    def acknowledge_stock_alert(self, alert_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM stock_alerts WHERE id=?', (alert_id,)).fetchone()
            if not row or row['status'] != 'Open': raise ValueError('Only an open alert can be acknowledged.')
            payload = json.loads(row['payload']); payload.update(status='Acknowledged', acknowledged_at=now())
            db.execute("UPDATE stock_alerts SET status='Acknowledged', payload=? WHERE id=?", (encoded(payload), alert_id))
            self._event(db, row['dataset'], f"Stock alert acknowledged for {payload['material']} / {payload['bin']}.")
        return dict(ok=True)

    def export(self, dataset_id):
        state = self.state(dataset_id)
        if state['dataset']['kind'] == 'workbook': state['insights'] = self.insights(dataset_id)
        with self.connection() as db:
            state['runs'] = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM runs WHERE dataset=? ORDER BY rowid DESC LIMIT 10', (dataset_id,))]
        state['exported_at'] = now()
        state['notice'] = 'Local evaluation artifact. Synthetic results are not Siemens validation. Review imported summaries before sharing.'
        return state
