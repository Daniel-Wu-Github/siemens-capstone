"""Build the UAT hand-off zip: the app, run scripts, one README and the database. No docs, tests or source data.

    python scripts/build_uat_zip.py --db data/handoff/siemens_uat.sqlite3 [--out outputs/siemens-capstone-uat.zip]

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
DB_NAME = 'siemens_uat.sqlite3'


def members():
    for name in FILES:
        yield ROOT / name, name
    for tree in TREES:
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
    parser.add_argument('--out', type=Path, default=ROOT / 'outputs' / 'siemens-capstone-uat.zip')
    args = parser.parse_args()
    check_database(args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, rel in members():
            info = zipfile.ZipInfo(f'{FOLDER}/{rel}', date_time=(2026, 11, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if rel == 'run.sh' else 0o644) << 16
            z.writestr(info, path.read_bytes())
        z.write(args.db, f'{FOLDER}/{DB_NAME}')
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    args.out.with_name(args.out.name + '.sha256').write_text(f'{digest}  {args.out.name}\n')
    with zipfile.ZipFile(args.out) as z:
        names = z.namelist()
    print(f'Wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB, {len(names)} files)\nsha256 {digest}')


if __name__ == '__main__':
    main()
