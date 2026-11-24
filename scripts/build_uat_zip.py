"""Build the UAT hand-off zip: the app, run scripts, one README and the database. No docs, tests or source data.

    python scripts/build_uat_zip.py --db data/handoff/siemens_capstone.sqlite3 [--out outputs/siemens-capstone-uat.zip]
    python scripts/build_uat_zip.py --db data/handoff/siemens_capstone.sqlite3 --handoff    # final hand-off: also the guides and the demo

The zip contains Siemens data (the database): send it through a private channel, never Git. The output
folder `outputs/` is Git-ignored.
"""
import argparse
import hashlib
from pathlib import Path
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['index.html', 'server.py', 'requirements.txt', 'README.md', 'run.bat', 'run.sh']
TREES = ['src']
FORBIDDEN = ('.claude', '.agents', '.ai-workflow', 'CLAUDE.md', 'CODEX.md', 'AGENTS', 'IMPLEMENTATION_PLAN', 'handoff.md',
             'poster-prompt', '.eml', '.env', '__pycache__', '.venv', '.git', '.DS_Store')
FOLDER = 'siemens-capstone-uat'
HANDOFF_FOLDER = 'siemens-capstone-handoff'
DEMO = ROOT / 'outputs' / 'Siemens_Replenishment_Demo.html'
DB_NAME = 'siemens_capstone.sqlite3'


def members(handoff=False):
    for name in FILES + (['data/README.md'] if handoff else []):
        yield ROOT / name, name
    if handoff:
        if not DEMO.is_file(): raise SystemExit('Build the demo first: python scripts/build_demo_html.py')
        yield DEMO, DEMO.name
    for tree in TREES + (['docs'] if handoff else []):
        for path in sorted((ROOT / tree).rglob('*')):
            rel = path.relative_to(ROOT).as_posix()
            if path.is_file() and not any(bad in rel for bad in FORBIDDEN) and path.suffix != '.pyc':
                yield path, rel


def check_database(db):
    if not db.is_file(): raise SystemExit(f'Database not found: {db}')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise SystemExit('Database failed its integrity check.')
        if not c.execute('SELECT count(*) FROM datasets').fetchone()[0]: raise SystemExit('Database has no imported workbook.')
        if c.execute('SELECT count(*) FROM notifications').fetchone()[0]: raise SystemExit('Database holds saved notifications; rebuild it clean.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--handoff', action='store_true', help='Final hand-off: add docs/ (guides, images), data/README.md and the demo HTML.')
    args = parser.parse_args()
    folder = HANDOFF_FOLDER if args.handoff else FOLDER
    args.out = args.out or ROOT / 'outputs' / ('siemens-capstone-handoff.zip' if args.handoff else 'siemens-capstone-uat.zip')
    check_database(args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, rel in members(args.handoff):
            info = zipfile.ZipInfo(f'{folder}/{rel}', date_time=(2026, 11, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if rel == 'run.sh' else 0o644) << 16
            z.writestr(info, path.read_bytes())
        z.write(args.db, f'{folder}/{DB_NAME}')
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    args.out.with_name(args.out.name + '.sha256').write_text(f'{digest}  {args.out.name}\n')
    with zipfile.ZipFile(args.out) as z:
        names = z.namelist()
    print(f'Wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB, {len(names)} files)\nsha256 {digest}')


if __name__ == '__main__':
    main()
