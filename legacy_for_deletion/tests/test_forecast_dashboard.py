"""Part forecast page journeys in a real browser: temp SQLite, local outbox, synthetic data."""
from playwright.sync_api import sync_playwright, expect
import pytest

from qc_helpers import full_workbook, tolerance_bytes
from test_api import running_server
from test_phase2_browser import clean_env  # noqa: F401  (fixture)
from test_phase2_store_api import BINS, PRODUCTION, TOLS, TRANSFERS

# Fixture parts (30/day where transfers exist): PRED 13/min 10 -> 2.4 h Predicted, suggest 91;
# SAFE 60/min 10 -> 40 h OK; NORATE -> no rate; BM 5/min 10 -> below min.


def open_page(p, base, viewport={'width': 1440, 'height': 1000}, part=None):
    browser = p.chromium.launch()
    page = browser.new_page(viewport=viewport)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    wb = full_workbook(BINS, production=PRODUCTION, transfers=TRANSFERS)
    ds = page.request.post(base + '/api/import?filename=s.xlsx', data=wb, headers={'Content-Type': 'application/octet-stream'}).json()['id']
    page.request.post(base + f'/api/import?filename=t.xlsx&dataset={ds}', data=tolerance_bytes(TOLS), headers={'Content-Type': 'application/octet-stream'})
    page.goto(base + '/?dataset=' + ds + '&view=forecast' + (f'&part={part}' if part else ''))
    expect(page.locator('#pf-name')).to_be_visible()
    return browser, page, ds, errors


def server(page, base, ds):
    return page.request.get(base + '/api/stock?dataset=' + ds).json()


def choose(page, text):
    page.locator('#pf-search').fill(text)
    page.locator('#pf-results li[data-key]').first.click()


def set_number(page, label, value):
    page.get_by_label(label, exact=True).fill(str(value))


@pytest.mark.browser
def test_default_part_shows_big_name_status_and_numbers_matching_the_server(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base)
        pred = next(x for x in server(page, base, ds)['positions'] if x['material'] == 'PRED')
        expect(page.locator('#pf-name')).to_have_text('PRED')                 # predicted part opens first
        expect(page.locator('#pf-status')).to_have_text('Predicted')
        expect(page.locator('#pf-k-available')).to_contain_text('13')
        expect(page.locator('#pf-k-hours')).to_have_text(f"{pred['hours_to_min']} h")
        expect(page.locator('#pf-k-rate')).to_contain_text('30')
        expect(page.locator('#pf-k-order')).to_contain_text(str(pred['suggested_qty']))
        expect(page.locator('.pf-steps')).to_contain_text('(13 − 10) ÷ 30 × 24 = 2.4 h')
        expect(page.locator('.pf-chart svg')).to_have_attribute('aria-label', 'Projected stock for PRED: reaches minimum in 2.4 h')
        assert not errors
        browser.close()


@pytest.mark.browser
def test_search_selects_a_part_and_sliders_change_the_forecast_without_saving(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base)
        choose(page, 'SAFE')
        expect(page.locator('#pf-name')).to_have_text('SAFE')
        expect(page.locator('#pf-status')).to_have_text('OK')
        expect(page.locator('#pf-k-hours')).to_have_text('40 h')
        set_number(page, 'Lead time (h)', 48)                                   # breach now comes before restock
        expect(page.locator('#pf-status')).to_have_text('Predicted')
        expect(page.locator('.pf-scen')).to_be_visible()
        expect(page.locator('#pf-savebox')).to_contain_text('Stocked lead time → 48 h')
        set_number(page, 'Lead time (h)', 3)
        set_number(page, 'Consumption multiplier (×)', 2)                       # twice the consumption, half the time
        expect(page.locator('#pf-k-hours')).to_have_text('20 h')
        expect(page.locator('#pf-savebox')).to_contain_text('What-if only, not saved: consumption')
        page.get_by_role('button', name='Custom rate').click()
        set_number(page, 'Custom consumption (/day)', 100)
        expect(page.locator('#pf-k-hours')).to_have_text('12 h')
        set_number(page, 'Minimum', 50)
        expect(page.locator('#pf-k-hours')).to_have_text('2.4 h')
        expect(page.locator('#pf-status')).to_have_text('Predicted')
        page.get_by_role('button', name='Reset').click()
        expect(page.locator('#pf-k-hours')).to_have_text('40 h')
        assert server(page, base, ds)['forecast']['settings']['lead_hours_stocked'] == 3
        assert not errors
        browser.close()


@pytest.mark.browser
def test_save_lead_time_and_stock_reading_update_alerts(tmp_path, clean_env):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, part='SAFE|401|BS')
        expect(page.locator('#pf-name')).to_have_text('SAFE')
        set_number(page, 'Lead time (h)', 48)
        page.get_by_role('button', name='Save').click()
        expect(page.locator('.pf-alert')).to_contain_text('Predicted')
        view = server(page, base, ds)
        assert view['forecast']['settings']['lead_hours_stocked'] == 48
        assert {a['material']: a['kind'] for a in view['open_alerts']}['SAFE'] == 'predicted'

        choose(page, 'PRED')
        set_number(page, 'Available stock', 10)                                 # at the minimum: escalate
        expect(page.locator('#pf-status')).to_have_text('Below minimum')
        page.get_by_role('button', name='Save').click()
        expect(page.locator('.pf-alert')).to_contain_text('escalated from predicted')
        pred = next(a for a in server(page, base, ds)['open_alerts'] if a['material'] == 'PRED')
        assert pred['kind'] == 'below_min' and pred.get('escalated_at')
        page.get_by_role('button', name='Built in fab').click()
        expect(page.locator('#pf-savebox')).to_contain_text("This part's lead class → fab")
        assert not errors
        browser.close()


@pytest.mark.browser
@pytest.mark.parametrize('width', [390, 1440])
def test_page_fits_without_horizontal_scroll(tmp_path, clean_env, width):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser, page, ds, errors = open_page(p, base, {'width': width, 'height': 900})
        for part in ('PRED', 'NORATE', 'BM'):
            choose(page, part)
            expect(page.locator('#pf-name')).to_have_text(part)
            assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth'), part
        assert not errors
        browser.close()
