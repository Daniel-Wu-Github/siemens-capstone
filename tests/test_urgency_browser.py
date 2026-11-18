"""Stock alerts urgency cues in a real browser: left marker, nav badge, Open since, Last replenished (synthetic data, temp DB)."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from playwright.sync_api import sync_playwright, expect
import pytest

from qc_helpers import full_workbook, tolerance_bytes
from test_api import running_server
from test_phase2_browser import clean_env  # noqa: F401  (fixture)
from test_phase2_store_api import BINS, PRODUCTION, TOLS, TRANSFERS

BINS2 = BINS + [['BM2', 'Synthetic b2', '401', 'BB2', 3, 'PC']]
TOLS2 = TOLS + [['BM2', 'x', 401, 'BB2', 10, 100]]


def backdate(db_path, material, hours):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec='seconds')
    with sqlite3.connect(db_path) as db:
        for rid, payload in db.execute('SELECT id,payload FROM stock_alerts').fetchall():
            data = json.loads(payload)
            if data['material'] == material:
                data['created_at'] = stamp
                db.execute('UPDATE stock_alerts SET payload=? WHERE id=?', (json.dumps(data), rid))


def open_stock(p, base, tmp_path):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    wb = full_workbook(BINS2, production=PRODUCTION, transfers=TRANSFERS)
    ds = page.request.post(base + '/api/import?filename=s.xlsx', data=wb, headers={'Content-Type': 'application/octet-stream'}).json()['id']
    page.request.post(base + f'/api/import?filename=t.xlsx&dataset={ds}', data=tolerance_bytes(TOLS2), headers={'Content-Type': 'application/octet-stream'})
    backdate(tmp_path / 'api.sqlite3', 'BM2', 51.5)
    backdate(tmp_path / 'api.sqlite3', 'BM', 2.2)
    page.goto(base + '/#stock')
    page.wait_for_selector('#stock-rows tr.alert-row')
    return browser, page, errors


@pytest.mark.browser
def test_alert_rows_have_a_left_marker_and_no_row_fill(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, errors = open_stock(p, base, tmp_path)
        alert, other = page.locator('#stock-rows tr.alert-row').first, page.locator('#stock-rows tr:not(.alert-row)').first
        cell = alert.locator('td').first
        assert cell.evaluate('e => getComputedStyle(e).borderLeftWidth') == '4px'
        assert cell.evaluate('e => getComputedStyle(e).borderLeftColor') == 'rgb(179, 38, 30)'
        assert alert.evaluate('e => getComputedStyle(e).backgroundColor') == other.evaluate('e => getComputedStyle(e).backgroundColor')
        expect(alert).to_contain_text('Alert')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_nav_badge_counts_unacknowledged_alerts_and_hides_at_zero(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, errors = open_stock(p, base, tmp_path)
        expect(page.locator('#nav-alert-badge')).to_have_text('2')
        expect(page.locator('#nav a[href="#stock"]')).to_have_attribute('aria-label', 'Stock alerts, 2 open alerts')
        page.goto(base + '/#insights')
        expect(page.locator('#nav-alert-badge')).to_have_text('2')   # visible on another page, loaded in the background
        page.goto(base + '/#stock')
        page.locator('#clear-alerts').click(); page.locator('#clear-alerts').click()
        expect(page.locator('#nav-alert-badge')).to_have_count(0)
        expect(page.locator('#stock-rows tr.alert-row').first).to_be_visible()   # acknowledged rows keep the marker
        assert not errors
        browser.close()


@pytest.mark.browser
def test_open_since_orders_longest_open_first_and_the_header_cycles(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, errors = open_stock(p, base, tmp_path)
        first_cells = lambda: page.locator('#stock-rows tr.alert-row td:nth-child(7)').all_inner_texts()
        ages = first_cells()
        assert ages[0].startswith('2 d 3 h') and ages[1].startswith('2 h'), ages       # default: longest open first
        header = page.locator('th.sortable button[data-sort="age"]')
        header.click()
        expect(page.locator('th[aria-sort="descending"] button[data-sort="age"]')).to_have_count(1)
        assert first_cells()[0].startswith('2 d')
        page.locator('th.sortable button[data-sort="age"]').click()
        expect(page.locator('th[aria-sort="ascending"] button[data-sort="age"]')).to_have_count(1)
        assert first_cells()[0].startswith('2 h')
        page.locator('th.sortable button[data-sort="age"]').click()
        expect(page.locator('th[aria-sort="none"] button[data-sort="age"]')).to_have_count(1)
        assert first_cells()[0].startswith('2 d')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_last_replenished_column_shows_the_date_or_a_dash_and_sorts(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, errors = open_stock(p, base, tmp_path)
        rows = page.locator('#stock-rows tr')
        pred = rows.filter(has_text='PRED').first.locator('td').nth(7)
        norate = rows.filter(has_text='NORATE').first.locator('td').nth(7)
        expect(pred).to_have_text('2026-10-10')
        expect(norate).to_have_text('—')
        expect(norate).to_have_attribute('title', 'No replenishment on file')
        page.locator('button[data-sort="lastrepl"]').click()
        texts = page.locator('#stock-rows tr td:nth-child(8)').all_inner_texts()
        assert texts[-1] == '—' and texts[0] == '2026-10-10'
        assert not errors
        browser.close()


@pytest.mark.browser
def test_digest_enlarge_opens_a_grouped_dialog_that_closes_with_escape(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, errors = open_stock(p, base, tmp_path)
        digest = page.locator('button[data-stock="digest"]')
        digest.click(); digest.click()
        expect(page.locator('#mail-preview')).to_be_visible()
        enlarge = page.locator('#enlarge-digest')
        expect(page.locator('#digest-dialog')).not_to_be_visible()
        enlarge.click()
        dialog = page.locator('#digest-dialog')
        expect(dialog).to_be_visible()
        expect(dialog.locator('h3', has_text='All open alerts')).to_be_visible()
        assert dialog.locator('.digest-rows li').count() >= 2
        box = dialog.bounding_box()
        assert 700 <= box['width'] <= 900 and box['height'] <= 800
        assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        assert page.evaluate("document.activeElement && document.activeElement.id") == 'enlarge-digest'
        enlarge.click(); page.locator('#digest-close').click()
        expect(dialog).not_to_be_visible()
        assert not errors
        browser.close()
