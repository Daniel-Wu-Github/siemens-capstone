"""SQLite persistence for datasets, policies, runs, evaluations, and alert receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .engine import AS_OF, VERSION, demo_materials, evaluate, number, predict, synthetic_episodes
from .ingest import inspect_workbook
from .schemas import generate_synthetic_manufacturing_fixtures
from .features import aggregate_scheduled_bom_demand, compute_part_criticality, parse_iso_date
from .ml_engine import QuantileDepletionEstimator, evaluate_depletion_models
from .alerts import EarlyWarningNotificationEngine
from dataclasses import asdict
from datetime import timedelta


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':'))


class Store:
    def __init__(self, path):
        self.path = Path(path)
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
        db.execute('BEGIN IMMEDIATE')
        try:
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

    def ml_forecast(self, dataset_id, warning_buffer_days=3.0):
        buffer_days = number(warning_buffer_days, 'Warning buffer days', 0.5, 30.0)
        with self.connection() as db:
            dataset, _ = self._dataset(db, dataset_id)
            materials = self._materials(db, dataset_id)
            fixtures = generate_synthetic_manufacturing_fixtures()
            bin_masters = fixtures['bin_masters']
            bom = fixtures['bom']

            factor = dataset.get('demand_factor', 1.0)
            as_of = dataset.get('as_of') or AS_OF
            as_of_d = parse_iso_date(as_of)

            schedule_rows = []
            for day in range(14):
                dt_str = (as_of_d + timedelta(days=day)).isoformat()
                schedule_rows.append({'Line Item': '21010', 'Basic Start Date': dt_str})
                schedule_rows.append({'Line Item': '21020', 'Basic Start Date': dt_str})
                if day % 2 == 0:
                    schedule_rows.append({'Line Item': '22010', 'Basic Start Date': dt_str})
                if day % 3 == 0:
                    schedule_rows.append({'Line Item': '22020', 'Basic Start Date': dt_str})

            demand_schedules = aggregate_scheduled_bom_demand(schedule_rows, bom, as_of)
            criticality = compute_part_criticality(bom)

            ml_estimator = QuantileDepletionEstimator(scrap_contingency=0.10 * factor)
            alert_engine = EarlyWarningNotificationEngine(default_warning_buffer_days=buffer_days)

            forecasts = []
            for part_id, master in bin_masters.items():
                matching_mat = next((m for m in materials if m['id'] == part_id or part_id in m['name']), None)
                stock = float(matching_mat['stock']) if matching_mat else (master.min_stock + 15.0)
                fc = ml_estimator.predict(
                    part_id=part_id,
                    current_stock=stock,
                    bin_master=master,
                    demand_schedule=demand_schedules[part_id],
                    as_of=as_of,
                    criticality=criticality.get(part_id),
                )
                forecasts.append(fc)

            alerts = alert_engine.generate_alerts(forecasts, bin_masters, warning_buffer_days=buffer_days)
            return {
                'dataset_id': dataset_id,
                'as_of': as_of,
                'engine': 'Quantile GBDT & Early Warning Engine',
                'warning_buffer_days': buffer_days,
                'forecasts': [asdict(f) for f in forecasts],
                'alerts': [a.to_dict() for a in alerts],
            }

    def ml_evaluate(self, dataset_id, horizon_days=7.0):
        h_days = number(horizon_days, 'Horizon days', 1.0, 30.0)
        episodes = [
            {'actual_days_to_min': 1.8, 'lead_time_days': 2.0, 'baseline_days': 4.5, 'schedule_mrp_days': 2.2, 'quantile_p10_days': 1.6, 'quantile_p50_days': 2.0},
            {'actual_days_to_min': 3.2, 'lead_time_days': 2.0, 'baseline_days': 5.0, 'schedule_mrp_days': 3.5, 'quantile_p10_days': 2.9, 'quantile_p50_days': 3.3},
            {'actual_days_to_min': 5.8, 'lead_time_days': 2.0, 'baseline_days': 9.0, 'schedule_mrp_days': 6.0, 'quantile_p10_days': 5.2, 'quantile_p50_days': 5.9},
            {'actual_days_to_min': 0.8, 'lead_time_days': 2.0, 'baseline_days': 2.0, 'schedule_mrp_days': 1.0, 'quantile_p10_days': 0.7, 'quantile_p50_days': 0.9},
            {'actual_days_to_min': None, 'lead_time_days': 2.0, 'baseline_days': None, 'schedule_mrp_days': None, 'quantile_p10_days': None, 'quantile_p50_days': None},
        ]
        return evaluate_depletion_models(episodes, horizon_days=h_days)

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

    def record_count(self, dataset_id):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM records WHERE dataset=?', (dataset_id,)).fetchone()[0]

    def export(self, dataset_id):
        state = self.state(dataset_id)
        with self.connection() as db:
            state['runs'] = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM runs WHERE dataset=? ORDER BY rowid DESC LIMIT 10', (dataset_id,))]
        state['exported_at'] = now()
        state['notice'] = 'Local evaluation artifact. Synthetic results are not Siemens validation. Review imported summaries before sharing.'
        return state
