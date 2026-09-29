"""Real browser journeys against a temporary SQLite database."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
import pytest

from test_api import running_server
from test_ingest import workbook_bytes


@pytest.mark.browser
def test_dashboard_policy_delivery_import_evaluation_and_export(tmp_path):
    artifacts = Path('artifacts'); artifacts.mkdir(exist_ok=True)
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1050}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        expect(page.get_by_role('heading', name='Stay ahead of the next shortage.')).to_be_visible()
        expect(page.get_by_text('SYNTHETIC DEMO', exact=True)).to_be_visible()
        page.get_by_role('combobox', name='Chart time window').select_option('6')
        expect(page.get_by_text('Recent consumption and the next 6 hours', exact=True)).to_be_visible()
        page.get_by_role('combobox', name='Chart time window').select_option('12')
        page.screenshot(path=str(artifacts / 'overview.png'), full_page=True)
        page.get_by_role('button', name='Run forecast').click()
        expect(page.locator('#toast')).to_contain_text('Forecast and recommendations saved')
        page.get_by_role('button', name='Materials & alerts', exact=True).click()
        page.get_by_role('searchbox', name='Search materials').fill('DEM-101')
        expect(page.locator('#material-rows tr')).to_have_count(1)
        page.get_by_role('button', name='Inspect', exact=True).click()
        page.get_by_role('button', name='Edit policy', exact=True).click()
        page.get_by_label('Minimum stock (pieces)').fill('25')
        page.get_by_role('button', name='Save & recalculate').click()
        expect(page.get_by_role('dialog')).not_to_be_visible()
        page.reload()
        expect(page.get_by_role('heading', name='Stay ahead of the next shortage.')).to_be_visible()
        page.get_by_role('button', name='Edit policy', exact=True).click()
        expect(page.get_by_label('Minimum stock (pieces)')).to_have_value('25')
        page.get_by_role('button', name='Cancel', exact=True).click()

        # Dispatch does not change stock. A receipt changes it and survives reload.
        state = page.request.get(base + '/api/state?dataset=demo').json()
        alert = next(a for a in state['alerts'] if a['status'] == 'Open')
        old_stock = next(m['stock'] for m in state['materials'] if m['id'] == alert['material_id'])
        page.get_by_role('button', name='Materials & alerts', exact=True).click()
        page.locator(f'button[data-alert="{alert["id"]}"][data-next="Dispatched"]').click()
        expect(page.locator(f'button[data-deliver="{alert["id"]}"]')).to_be_visible()
        after_dispatch = page.request.get(base + '/api/state?dataset=demo').json()
        assert next(m['stock'] for m in after_dispatch['materials'] if m['id'] == alert['material_id']) == old_stock
        page.locator(f'button[data-deliver="{alert["id"]}"]').click()
        page.get_by_label('Quantity received (pieces)').fill('200')
        page.get_by_role('button', name='Confirm receipt', exact=True).click()
        expect(page.get_by_role('dialog')).not_to_be_visible()
        page.reload()
        after_delivery = page.request.get(base + '/api/state?dataset=demo').json()
        assert next(m['stock'] for m in after_delivery['materials'] if m['id'] == alert['material_id']) == old_stock + 200

        page.get_by_role('button', name='Model evaluation', exact=True).click()
        page.get_by_role('button', name='Run evaluation').click()
        expect(page.locator('#toast')).to_contain_text('Evaluation completed')
        expect(page.get_by_role('heading', name='Comparison report')).to_be_visible()
        page.screenshot(path=str(artifacts / 'evaluation.png'), full_page=True)
        with page.expect_download() as info:
            page.get_by_role('button', name='Export report', exact=False).click()
        downloaded = info.value
        report = json.loads(Path(downloaded.path()).read_text())
        assert report['evaluation']['sample_count'] == 60 and report['runs']

        page.get_by_role('button', name='Data workspace', exact=True).click()
        page.get_by_label('Import workbook').set_input_files({'name': 'synthetic-test.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': workbook_bytes()})
        expect(page.get_by_text('LOCAL WORKBOOK', exact=True)).to_be_visible()
        expect(page.get_by_text('negative quantity · 1 occurrences', exact=True)).to_be_visible()
        expect(page.get_by_text('Forecast readiness: definitions needed', exact=True)).to_be_visible()
        page.get_by_role('button', name='Model evaluation', exact=True).click()
        expect(page.get_by_role('button', name='Run evaluation')).to_be_disabled()
        page.get_by_role('combobox', name='Active dataset').select_option('demo')
        expect(page.get_by_role('heading', name='Comparison report')).to_be_visible()
        assert not errors
        browser.close()


@pytest.mark.browser
def test_mobile_navigation_and_scenario(tmp_path):
    with running_server(tmp_path) as base, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        page.goto(base)
        expect(page.get_by_role('heading', name='Stay ahead of the next shortage.')).to_be_visible()
        page.locator('#demand').focus()
        for _ in range(10): page.locator('#demand').press('ArrowRight')
        page.get_by_role('button', name='Apply scenario & save').click()
        expect(page.locator('#toast')).to_contain_text('Production scenario saved')
        page.reload()
        expect(page.locator('#demand')).to_have_value('1.5')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='artifacts/mobile.png', full_page=True)
        browser.close()
