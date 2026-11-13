#!/usr/bin/env python3
"""Compare 30, 45, and 60 day rate windows. Output to outputs/."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from siemens_capstone.backtest import evaluate_windows, to_markdown_windows


def default_db():
    p1 = ROOT / 'data/capstone.sqlite3'
    if p1.is_file():
        return p1
    p2 = ROOT.parent / 'siemens-capstone/data/capstone.sqlite3'
    if p2.is_file():
        return p2
    return p1


def load(db_path, dataset=None):
    """Read the workbook and references from a private copy of the database."""
    if not db_path.is_file():
        raise ValueError(f'Database not found: {db_path}')
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / 'backtest.sqlite3'
        shutil.copyfile(db_path, copy)
        if Path(f'{db_path}-wal').is_file():
            shutil.copyfile(f'{db_path}-wal', f'{copy}-wal')
        with closing(sqlite3.connect(copy)) as db:
            db.row_factory = sqlite3.Row
            datasets = db.execute('SELECT id,payload,quality FROM datasets').fetchall()
            candidates = [r for r in datasets if json.loads(r['payload']).get('kind') == 'workbook'
                          and (not dataset or r['id'] == dataset)]
            if not candidates:
                raise ValueError('Workbook dataset not found.')
            row = max(candidates, key=lambda r: json.loads(r['payload']).get('created_at', ''))
            records, refs = {}, {}
            for r in db.execute('SELECT sheet,payload FROM records WHERE dataset=? ORDER BY sheet,row_number', (row['id'],)):
                records.setdefault(r['sheet'], []).append(json.loads(r['payload']))
            for r in db.execute('SELECT kind,payload FROM "references" WHERE dataset=? ORDER BY created_at,rowid', (row['id'],)):
                if r['kind'] in ('bom', 'tolerances'):
                    refs[r['kind']] = json.loads(r['payload'])
            return records, json.loads(row['quality']).get('date_system', '1900'), refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=default_db())
    parser.add_argument('--dataset')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs')
    args = parser.parse_args()
    try:
        records, date_system, refs = load(args.db, args.dataset)
        report = evaluate_windows(records, date_system, refs)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stem = args.output_dir / 'backtest_windows'
        stem.with_suffix('.md').write_text(to_markdown_windows(report))
        stem.with_suffix('.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        print(f'Wrote {stem.with_suffix(".md")} and {stem.with_suffix(".json")}')
    except (ValueError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f'Backtest failed: {exc}\n')


if __name__ == '__main__':
    main()
