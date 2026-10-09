"""Repository locations shared by the server, store, API, and tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / 'data'  # Siemens workbooks and the local database; ignored by Git.
DB_PATH = DATA_DIR / 'capstone.sqlite3'
OUTBOX = ROOT / 'outputs' / 'outbox'
INDEX_HTML = ROOT / 'index.html'
