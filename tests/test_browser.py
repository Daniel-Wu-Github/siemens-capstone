"""Frontend journeys in a real browser: three pages plus Data sources and Settings (temp SQLite, local outbox, no SMTP)."""
from playwright.sync_api import sync_playwright, expect
import pytest

from test_api import running_server
from test_ingest import table_bytes

NAV = ['Stock alerts', 'Predicted alerts', 'Replenishment insights', 'Data sources', 'Settings']
OCTET = {'Content-Type': 'application/octet-stream'}


def stock_workbook():
    from test_alerts import BIN
    return table_bytes({'HISTORICAL_PROD_DATA': [['salesorderOrdNr', 'productionorderSalesOrderLineItem', 'jobtraceObjid']],
                        'PLANNED_PROD_SCHEDULE': [['Sales Order', 'Line Item', 'Basic Start Date']],
                        'HISTORICAL_SM_REPLENISHMENTS': [['Transfer Order Number', 'Transfer order item', 'Material', 'Dest.Storage Bin', 'Dest.target quantity']],
                        'BIN_STATUS_EXAMPLE': BIN})


def prepared(page, base):
    from test_alerts import TOL
    ds = page.request.post(base + '/api/import?filename=s.xlsx', data=stock_workbook(), headers=OCTET).json()['id']
    page.request.post(base + f'/api/import?filename=t.xlsx&dataset={ds}', data=table_bytes({'Sheet3': TOL}), headers=OCTET)
    return ds


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for k in ('SMTP_HOST', 'TEAMS_WEBHOOK_URL', 'ALERT_WEBHOOK_URL', 'ALERT_EMAIL_TO'): monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('ALERT_OUTBOX', str(tmp_path / 'outbox'))


@pytest.mark.browser
def test_empty_workspace_shows_three_pages_setup_and_points_to_data_sources(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch(); page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base + '/')
        assert page.locator('#nav a').all_inner_texts() == NAV  # no legacy pages in the navigation
        expect(page.get_by_role('heading', name='Stock alerts')).to_be_visible()
        expect(page.get_by_text('Import a workbook first')).to_be_visible()
        page.get_by_role('link', name='Predicted alerts').click()
        expect(page.get_by_text('Import a workbook first')).to_be_visible()
        page.get_by_role('link', name='Replenishment insights').click()
        expect(page.get_by_text('Not built yet')).to_be_visible()
        assert not errors
        browser.close()


@pytest.mark.browser
def test_data_sources_imports_workbook_and_reference_files(tmp_path, clean_env):
    from test_alerts import TOL
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch(); page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base + '/#data')
        page.locator('#file-input').set_input_files({'name': 'w.xlsx', 'mimeType': 'application/octet-stream', 'buffer': stock_workbook()})
        expect(page.locator('#toast')).to_contain_text('Workbook imported')
        expect(page.get_by_role('heading', name='Source inventory')).to_be_visible()
        expect(page.get_by_text('Min/Max tolerances')).to_be_visible()
        expect(page.get_by_text('Not imported: add the Min/Max file')).to_be_visible()
        page.locator('#ref-input').set_input_files({'name': 't.xlsx', 'mimeType': 'application/octet-stream', 'buffer': table_bytes({'Sheet3': TOL})})
        expect(page.locator('#toast')).to_contain_text('Reference file imported')
        expect(page.get_by_text('Imported:').first).to_be_visible()
        expect(page.get_by_text('Min/Max tolerances').locator('xpath=..')).to_contain_text('rows')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_stock_alerts_edit_sends_email_and_reset_restores(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch(); page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        prepared(page, base)
        page.goto(base + '/#stock')
        expect(page.locator('#stock-alert-count')).to_have_text('3')
        box = page.get_by_label('Available stock for PART-A A1')
        box.fill('2'); box.press('Enter')
        expect(page.locator('#toast')).to_contain_text('email saved')
        expect(page.locator('#stock-alert-count')).to_have_text('4')
        expect(page.locator('#mail-preview')).to_contain_text('PART-A')
        assert len(list((tmp_path / 'outbox').glob('*.eml'))) == 2  # baseline announcement + this edit
        page.get_by_role('button', name='Reset simulated stock').click()
        expect(page.locator('#stock-alert-count')).to_have_text('4')  # first click only arms the button
        page.get_by_role('button', name='Click again to reset').click()
        expect(page.locator('#stock-alert-count')).to_have_text('3')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_digest_needs_confirmation_and_reference_upload_refreshes_alerts(tmp_path, clean_env):
    from test_alerts import TOL
    looser = table_bytes({'Sheet3': [TOL[0]] + [[r[0], r[1], r[2], r[3], 0, 50] for r in TOL[1:]]})  # every min 0 -> only the empty bin alerts
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch(); page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        prepared(page, base)
        page.goto(base + '/#stock')
        expect(page.locator('#stock-alert-count')).to_have_text('3')
        mails = lambda: len(list((tmp_path / 'outbox').glob('*.eml')))
        before = mails()
        page.get_by_role('button', name='Send digest').click()
        page.wait_for_timeout(300)
        assert mails() == before  # first click only arms the button
        page.get_by_role('button', name='Click again to send').click()
        expect(page.locator('#toast')).to_contain_text('Digest')
        assert mails() == before + 1
        page.get_by_role('link', name='Data sources').click()
        page.locator('#ref-input').set_input_files({'name': 'looser.xlsx', 'mimeType': 'application/octet-stream', 'buffer': looser})
        expect(page.locator('#toast')).to_contain_text('Reference file imported')
        page.get_by_role('link', name='Stock alerts').click()
        expect(page.locator('#stock-alert-count')).to_have_text('1')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_every_page_fits_the_viewport_without_horizontal_scroll(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch()
        errors = []
        for size in ({'width': 1440, 'height': 900}, {'width': 1280, 'height': 800}):
            page = browser.new_page(viewport=size); page.on('pageerror', lambda e: errors.append(str(e)))
            prepared(page, base)
            for name in ('stock', 'predicted', 'insights', 'data', 'settings'):
                page.goto(base + '/#' + name); page.wait_for_timeout(250)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (name, size)
        assert not errors
        browser.close()
