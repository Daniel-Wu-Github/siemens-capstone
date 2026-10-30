"""Replenishment insights (View 2) journeys in a real browser (temp SQLite, no SMTP)."""
from playwright.sync_api import sync_playwright, expect
import pytest

from qc_helpers import full_workbook, tolerance_bytes
from test_api import running_server
from test_phase2_store_api import BINS, PRODUCTION, TOLS, TRANSFERS


def open_insights_page(p, base, viewport=None):
    browser = p.chromium.launch()
    page = browser.new_page(viewport=viewport or {'width': 1440, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    wb = full_workbook(BINS, production=PRODUCTION, transfers=TRANSFERS)
    ds = page.request.post(base + '/api/import?filename=s.xlsx', data=wb, headers={'Content-Type': 'application/octet-stream'}).json()['id']
    page.request.post(base + f'/api/import?filename=t.xlsx&dataset={ds}', data=tolerance_bytes(TOLS), headers={'Content-Type': 'application/octet-stream'})
    page.goto(base + '/#insights')
    expect(page.get_by_role('heading', name='Replenishment insights', exact=True)).to_be_visible()
    return browser, page, ds, errors


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for k in ('SMTP_HOST', 'TEAMS_WEBHOOK_URL', 'ALERT_WEBHOOK_URL', 'ALERT_EMAIL_TO', 'LEAD_HOURS_STOCKED', 'LEAD_HOURS_FAB', 'ALERT_SAFETY_HOURS'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('ALERT_OUTBOX', str(tmp_path / 'outbox'))


@pytest.mark.browser
def test_insights_page_strip_table_lens_thresholds_and_detail(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_insights_page(p, base)

        # Strip count tile is visible
        expect(page.locator('#insights-flagged-count')).to_be_visible()

        # Check table headers
        for h in ('Material', 'Location', 'Flags', 'Refills 30 d', 'Median % of Max', 'History / schedule per day'):
            expect(page.locator('thead').get_by_text(h, exact=True)).to_be_visible()

        # Check export CSV button has thresholds in URL
        csv_link = page.locator('#export-parts-csv')
        expect(csv_link).to_be_visible()
        href = csv_link.get_attribute('href')
        assert 'refill_pct=50' in href
        assert 'refills=8' in href
        assert 'divergence=50' in href

        # Detail panel
        row = page.locator('#parts-rows tr[data-part]').first
        if row.is_visible():
            row.click()
            detail = page.locator('#part-detail')
            expect(detail).to_contain_text('Evidence & metrics')

        # Lens filter
        page.locator('#insights-lens').select_option(label='All parts')
        expect(page.locator('#parts-rows tr')).not_to_have_count(0)

        # Threshold change re-queries API
        with page.expect_response('**/api/insights/parts*') as info:
            page.locator('#th-refill-pct').fill('40')
            page.locator('#th-refill-pct').dispatch_event('change')
        assert info.value.status == 200

        csv_href_after = page.locator('#export-parts-csv').get_attribute('href')
        assert 'refill_pct=40' in csv_href_after

        assert not errors
        browser.close()


@pytest.mark.browser
@pytest.mark.parametrize('width,height', [(1440, 900), (1280, 800)])
def test_insights_page_viewports_no_horizontal_overflow(tmp_path, clean_env, width, height):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_insights_page(p, base, viewport={'width': width, 'height': height})
        # Check no horizontal overflow on window
        scroll_width = page.evaluate('() => document.documentElement.scrollWidth')
        inner_width = page.evaluate('() => window.innerWidth')
        assert scroll_width <= inner_width, f'Horizontal overflow at {width}x{height}: scrollWidth={scroll_width} > innerWidth={inner_width}'
        assert page.evaluate("[...document.querySelectorAll('.tw')].every(t => t.scrollWidth <= t.clientWidth + 1)"), 'table needs sideways scrolling'
        assert not errors
        browser.close()
