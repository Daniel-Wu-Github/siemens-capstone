#!/usr/bin/env python3
"""Run the local prototype: python server.py [--port 8765] [--import-workbook PATH]."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from siemens_capstone.api import handler_for
from siemens_capstone.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'capstone.sqlite3')
    parser.add_argument('--import-workbook', type=Path)
    parser.add_argument('--export-report', type=Path, help='Write the synthetic report and exit.')
    args = parser.parse_args()
    store = Store(args.db)
    if args.import_workbook:
        dataset = store.import_workbook(args.import_workbook.read_bytes(), args.import_workbook.name)
        print(f"Workbook imported locally: {dataset['id']}", flush=True)
    if args.export_report:
        args.export_report.parent.mkdir(parents=True, exist_ok=True)
        args.export_report.write_text(json.dumps(store.export('demo'), indent=2))
        print(f'Report saved: {args.export_report}'); return
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(store))
    server.daemon_threads = True
    print(f'Open http://127.0.0.1:{server.server_port} — database: {args.db}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
