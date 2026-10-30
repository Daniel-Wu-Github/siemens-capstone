#!/usr/bin/env python3
"""Run the local prototype: python server.py [--port 8765] [--import-workbook PATH [--attach PATH ...] [--as-of DATE]]."""
import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from siemens_capstone.api import handler_for
from siemens_capstone.paths import DB_PATH
from siemens_capstone import proactive
from siemens_capstone.store import Store


def check_loop(store, minutes):
    while True:
        time.sleep(minutes * 60)
        try:
            for ds in store.datasets():
                if ds['kind'] == 'workbook': store.run_stock_check(ds['id'])
        except Exception as exc:
            print(f'[server] scheduled stock check failed: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--import-workbook', type=Path)
    target.add_argument('--dataset', help='Existing workbook dataset ID for attached reference files.')
    parser.add_argument('--quiet', action='store_true', help='Sync attached-file alerts without sending notifications.')
    parser.add_argument('--no-serve', action='store_true', help='Exit after imports without starting the server.')
    parser.add_argument('--attach', type=Path, action='append', default=[], help='Reference file (tolerances, BOM, snapshot) attached to the selected workbook; repeatable.')
    parser.add_argument('--as-of', help='Snapshot date YYYY-MM-DD for attached stock snapshot files.')
    parser.add_argument('--check-minutes', type=float, default=0, help='Re-check stock alerts for every workbook every N minutes (0 = off).')
    args = parser.parse_args()
    proactive.default_settings()  # fail at startup on an invalid LEAD_HOURS_* / ALERT_SAFETY_HOURS
    store = Store(args.db)
    dataset_id = args.dataset
    if args.attach and not (args.import_workbook or dataset_id):
        parser.error('--attach requires --import-workbook or --dataset')
    if args.import_workbook:
        dataset = store.import_workbook(args.import_workbook.read_bytes(), args.import_workbook.name)
        print(f"Workbook imported locally: {dataset['id']}", flush=True)
        dataset_id = dataset['id']
    for path in args.attach:
        ref = store.import_reference(dataset_id, path.read_bytes(), path.name, args.as_of, announce=not args.quiet)
        print(f"Reference attached ({ref['kind']}): {ref['rows']:,} rows", flush=True)
    if args.no_serve: return
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(store))
    server.daemon_threads = True
    print(f'Open http://127.0.0.1:{server.server_port} — database: {args.db}', flush=True)
    if args.check_minutes > 0:
        threading.Thread(target=check_loop, args=(store, args.check_minutes), daemon=True).start()
        print(f'Checking stock alerts every {args.check_minutes:g} min', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
