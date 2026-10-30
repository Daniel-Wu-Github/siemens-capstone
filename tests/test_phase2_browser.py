"""Phase 2 Predicted alerts and Settings journeys in a real browser (temp SQLite, local outbox, no SMTP)."""
from playwright.sync_api import sync_playwright, expect
import pytest

from qc_helpers import full_workbook, tolerance_bytes
from test_api import running_server
from test_phase2_store_api import BINS, PRODUCTION, TOLS, TRANSFERS

LABELS = ['Stocked lead time (h)', 'Fab lead time (h)', 'Safety buffer (h)', 'Watch window (h)', 'History weight', 'Schedule weight',
          'Observed weight', 'Divergence threshold (%)']


def open_page(p, base, view, viewport=None):
    browser = p.chromium.launch()
    page = browser.new_page(viewport=viewport or {'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    wb = full_workbook(BINS, production=PRODUCTION, transfers=TRANSFERS)
    ds = page.request.post(base + '/api/import?filename=s.xlsx', data=wb, headers={'Content-Type': 'application/octet-stream'}).json()['id']
    page.request.post(base + f'/api/import?filename=t.xlsx&dataset={ds}', data=tolerance_bytes(TOLS), headers={'Content-Type': 'application/octet-stream'})
    page.goto(base + '/#' + view)
    expect(page.get_by_role('heading', name={'predicted': 'Predicted alerts', 'settings': 'Settings'}[view], exact=True)).to_be_visible()
    return browser, page, ds, errors


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for k in ('SMTP_HOST', 'TEAMS_WEBHOOK_URL', 'ALERT_WEBHOOK_URL', 'ALERT_EMAIL_TO', 'LEAD_HOURS_STOCKED', 'LEAD_HOURS_FAB', 'ALERT_SAFETY_HOURS'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('ALERT_OUTBOX', str(tmp_path / 'outbox'))


@pytest.mark.browser
def test_predicted_page_strip_table_filter_and_calculation_detail(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, 'predicted')
        expect(page.locator('#stock-predicted-count')).to_have_text('1')
        expect(page.locator('#stock-watch-count')).to_have_text('1')
        row_pred = page.locator('#pred-rows tr', has_text='PRED')
        expect(row_pred).to_contain_text('Predicted')
        row_safe = page.locator('#pred-rows tr', has_text='SAFE')
        expect(row_safe).to_contain_text('Watch')
        for header in ('Demand / day', 'Hours to minimum', 'Tier'):
            expect(page.locator('thead').get_by_text(header, exact=True)).to_be_visible()
        row_pred.click()
        detail = page.locator('#pred-detail')
        expect(detail).to_contain_text('How it was calculated')
        expect(detail).to_contain_text('Hours to minimum')
        expect(detail).to_contain_text('Suggested quantity')
        expect(page.locator('#pred-rows tr')).to_have_count(3)
        page.locator('#pred-filter').select_option(label='All positions')
        expect(page.locator('#pred-rows tr')).to_have_count(4)
        page.locator('#pred-filter').select_option(label='No demand rate')
        expect(page.locator('#pred-rows tr')).to_have_count(1)
        assert not errors
        browser.close()


@pytest.mark.browser
def test_settings_page_shows_every_field_and_saving_lead_time_updates_predicted_count(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, 'settings')
        form = page.locator('#forecast-form')
        for label in LABELS: expect(form.get_by_label(label, exact=True)).to_be_visible()
        expect(form.get_by_label('Stocked lead time (h)', exact=True)).to_have_value('3')
        expect(form.get_by_label('Watch window (h)', exact=True)).to_have_value('72')
        form.get_by_label('Stocked lead time (h)', exact=True).fill('0')
        form.get_by_label('Safety buffer (h)', exact=True).fill('0')
        with page.expect_response('**/api/stock/settings') as info:
            page.get_by_role('button', name='Save forecast settings').click()
        assert info.value.status == 200
        expect(page.locator('#toast')).not_to_be_hidden()
        assert page.request.get(base + f'/api/stock?dataset={ds}').json()['forecast']['summary']['predicted'] == 0
        form.get_by_label('Stocked lead time (h)', exact=True).fill('48')
        with page.expect_response('**/api/stock/settings') as info:
            page.get_by_role('button', name='Save forecast settings').click()
        assert info.value.status == 200
        expect(page.locator('#toast')).not_to_be_hidden()
        page.goto(base + '/#predicted'); page.reload()
        expect(page.locator('#stock-predicted-count')).to_have_text('2')
        saved = page.request.get(base + f'/api/stock?dataset={ds}').json()
        assert saved['forecast']['settings']['lead_hours_stocked'] == 48.0

        # Saving watch_hours 30 removes SAFE (40 h) from Watch
        page.goto(base + '/#settings'); page.reload()
        form = page.locator('#forecast-form')
        form.get_by_label('Stocked lead time (h)', exact=True).fill('3')
        form.get_by_label('Safety buffer (h)', exact=True).fill('24')
        form.get_by_label('Watch window (h)', exact=True).fill('30')
        with page.expect_response('**/api/stock/settings') as info:
            page.get_by_role('button', name='Save forecast settings').click()
        assert info.value.status == 200
        page.goto(base + '/#predicted'); page.reload()
        expect(page.locator('#stock-watch-count')).to_have_text('0')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_weights_slider_live_preview_and_presets(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, 'settings')
        expect(page.get_by_role('heading', name='Try weights')).to_be_visible()

        # Moving History to 2 triggers preview debounce
        with page.expect_response('**/api/stock/preview') as preview_info:
            page.evaluate('''() => {
                const el = document.querySelector('#tw-history');
                el.value = '2';
                el.dispatchEvent(new Event('input'));
            }''')
        assert preview_info.value.status == 200
        # Preview updated without changing /api/stock settings
        stock_data = page.request.get(base + f'/api/stock?dataset={ds}').json()
        assert stock_data['forecast']['settings']['weights']['history'] == 1.0

        # Apply these weights
        with page.expect_response('**/api/stock/settings') as apply_info:
            page.locator('#btn-apply-weights').click()
        assert apply_info.value.status == 200
        stock_data_after = page.request.get(base + f'/api/stock?dataset={ds}').json()
        assert stock_data_after['forecast']['settings']['weights']['history'] == 2.0

        # Save as preset
        page.locator('#preset-name').fill('TestPreset')
        with page.expect_response('**/api/stock/presets') as save_info:
            page.locator('#btn-save-preset').click()
        assert save_info.value.status == 200
        expect(page.locator('#preset-list')).to_contain_text('TestPreset')

        # Reload and verify preset persists
        page.reload()
        expect(page.locator('#preset-list')).to_contain_text('TestPreset')

        # Apply preset
        with page.expect_response('**/api/stock/settings') as apply_preset_info:
            page.locator('[data-apply-preset="TestPreset"]').click()
        assert apply_preset_info.value.status == 200

        # Delete preset (click twice for armed confirmation)
        del_btn = page.locator('[data-delete-preset="TestPreset"]')
        del_btn.click()
        expect(del_btn).to_have_text('Click again to delete')
        with page.expect_response('**/api/stock/presets/delete') as del_info:
            del_btn.click()
        assert del_info.value.status == 200
        expect(page.locator('#preset-list')).not_to_contain_text('TestPreset')

        assert not errors
        browser.close()


@pytest.mark.browser
def test_invalid_settings_show_an_inline_error_and_change_nothing(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, 'settings')
        page.get_by_label('Stocked lead time (h)', exact=True).fill('9999')
        page.get_by_role('button', name='Save forecast settings').click()
        expect(page.locator('#forecast-error')).to_contain_text('720')
        assert page.request.get(base + f'/api/stock?dataset={ds}').json()['forecast']['settings']['lead_hours_stocked'] == 3.0
        assert not errors
        browser.close()


@pytest.mark.browser
def test_run_check_button_posts_and_reloads(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, 'predicted')
        page.get_by_role('link', name='Stock alerts').click()
        with page.expect_response('**/api/stock/check') as info:
            page.get_by_role('button', name='Run check').click()
        assert info.value.status == 200 and info.value.json()['checked_at']
        assert not errors
        browser.close()


@pytest.mark.browser
def test_lead_class_select_in_detail_posts_and_updates_the_forecast(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, 'predicted')
        page.request.post(base + '/api/stock/settings', data='{"dataset":"%s","lead_hours_fab":48}' % ds, headers={'Content-Type': 'application/json'})
        page.reload()
        expect(page.locator('#stock-predicted-count')).to_have_text('1')
        page.locator('#pred-filter').select_option(label='All with a demand rate')
        page.locator('#pred-rows tr', has_text='SAFE').click()
        with page.expect_response('**/api/stock/lead-class') as info:
            page.get_by_label('Lead class for SAFE BS').select_option('fab')
        assert info.value.status == 200
        expect(page.locator('#stock-predicted-count')).to_have_text('2')
        assert not errors
        browser.close()
