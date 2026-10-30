"""The suite never writes to the repository outbox or reads delivery settings from the developer's shell."""
import os

from siemens_capstone.paths import OUTBOX
from siemens_capstone.store import Store
from qc_helpers import full_workbook, tolerance_bytes


def outbox_files():
    return sorted(OUTBOX.glob('*.eml')) if OUTBOX.exists() else []


def test_default_store_notification_goes_to_the_test_outbox(tmp_path):
    before = outbox_files()
    store = Store(tmp_path / 'default.sqlite3')  # no alert_config: reads the environment like the server
    ds = store.import_workbook(full_workbook([['SYNTH', 'Synthetic', '401', 'B', 0, 'PC']]), 'synthetic.xlsx')['id']
    store.import_reference(ds, tolerance_bytes([['SYNTH', 'Synthetic', 401, 'B', 10, 100]]), 'tolerances.xlsx')
    assert list((tmp_path / 'outbox').glob('*.eml'))
    assert outbox_files() == before


def test_delivery_and_forecast_env_is_cleared():
    for name in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'ALERT_EMAIL_TO', 'TEAMS_WEBHOOK_URL', 'ALERT_WEBHOOK_URL',
                 'LEAD_HOURS_STOCKED', 'LEAD_HOURS_FAB', 'ALERT_SAFETY_HOURS', 'ALERT_WATCH_HOURS'):
        assert name not in os.environ
