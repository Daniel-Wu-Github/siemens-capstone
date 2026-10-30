"""Suite-wide isolation: no test reaches the repository outbox, real SMTP/Teams/webhooks, or shell forecast settings."""
import pytest

ISOLATED_ENV = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_STARTTLS', 'SMTP_SSL', 'ALERT_EMAIL_FROM',
                'ALERT_EMAIL_TO', 'TEAMS_WEBHOOK_URL', 'ALERT_WEBHOOK_URL', 'ALERT_DASHBOARD_URL', 'ALERT_RULE',
                'LEAD_HOURS_STOCKED', 'LEAD_HOURS_FAB', 'ALERT_SAFETY_HOURS', 'ALERT_WATCH_HOURS')


@pytest.fixture(autouse=True)
def isolated_delivery(tmp_path, monkeypatch):
    for name in ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('ALERT_OUTBOX', str(tmp_path / 'outbox'))
