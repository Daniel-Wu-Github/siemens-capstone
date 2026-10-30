"""End-to-end: every page control and operation in a real browser (synthetic data, temp SQLite, test outbox, no SMTP).

Fixture positions (storage type 401, PC; transfer window Oct 1-10 = 10 days):
  PRED    13 / min 10 / max 100, 30/day        -> 2.4 h   -> Predicted (lead 3 + safety 24 = 27 h)
  SAFE    60 / min 10 / max 100, 30/day        -> 40 h    -> Watch (<= 72 h)
  FREQ    95 / min 10 / max 100, 9 x 60 = 54/day -> 37.8 h -> Watch; 9 refills -> frequent
  BM       5 / min 10 / max 100, 30/day        -> Alert (below minimum)
  SHORT   80 / min 10 / max 100, 3 x 10 = 3/day -> 560 h   -> OK; refills 10% of Max -> short
  FAR    200 / min 10 / max 300, 30/day        -> 152 h   -> OK
  DIV    500 / min 10 / max 1000, history 30/day, schedule 60/day -> divergent (100%); demand uses schedule
  NORATE + 55 fillers F00..F54, no transfers   -> No rate
"""
from datetime import datetime
import csv
from io import StringIO

from playwright.sync_api import sync_playwright, expect
import pytest

from qc_helpers import bom_bytes, full_workbook, tolerance_bytes, xlsx_bytes
from test_api import running_server
from test_phase2_store_api import PRODUCTION, transfer

OCTET = {'Content-Type': 'application/octet-stream'}
NAV = ['Stock alerts', 'Predicted alerts', 'Replenishment insights', 'Data sources', 'Settings']
FILLERS = [f'F{i:02d}' for i in range(55)]
STOCK = dict(PRED=13, SAFE=60, FREQ=95, BM=5, SHORT=80, FAR=200, DIV=500, NORATE=50)
MAX = dict(FAR=300, DIV=1000)
BINS = [[m, f'Synthetic {m.lower()}', '401', f'B{m}', s, 'PC'] for m, s in STOCK.items()] + \
       [[f, 'Synthetic filler', '401', f'B{f}', 50, 'PC'] for f in FILLERS]
TOLS = [[m, 'x', 401, f'B{m}', 10, MAX.get(m, 100)] for m in STOCK] + [[f, 'x', 401, f'B{f}', 10, 100] for f in FILLERS]
_events = [(m, 150, d) for m in ('PRED', 'SAFE', 'BM', 'FAR', 'DIV') for d in (1, 10)]
_events += [('FREQ', 60, d) for d in range(1, 10)] + [('SHORT', 10, d) for d in (2, 5, 8)]
TRANSFERS = [transfer(i, m, f'B{m}', q, d) for i, (m, q, d) in enumerate(_events)]
SCHEDULE = [[f'SO{i}', 'ITEM1', datetime(2026, 11, 2)] for i in range(18)]  # 18 lines x 100 = 1800 / 30 = 60 per day
BOM = bom_bytes([['ITEM1', 'Synthetic assembly', 'DIV', 100, 'x', '']])
WORKBOOK = full_workbook(BINS, production=PRODUCTION, schedule=SCHEDULE, transfers=TRANSFERS)
DAILY_HEADER = ['Part', 'Description', 'Type', 'Location', 'Min', 'Max', 'Live Value']


def upload(name, content):
    return {'name': name, 'mimeType': 'application/octet-stream', 'buffer': content}


def seed(page, base):
    ds = page.request.post(base + '/api/import?filename=wb.xlsx', data=WORKBOOK, headers=OCTET).json()['id']
    for name, content in (('minmax.xlsx', tolerance_bytes(TOLS)), ('bom.xlsx', BOM)):
        assert page.request.post(base + f'/api/import?filename={name}&dataset={ds}', data=content, headers=OCTET).ok
    return ds


class Session:
    """One browser page that fails the test on page errors, console errors or native dialogs."""
    def __init__(self, p, viewport=None):
        self.browser = p.chromium.launch()
        self.page = self.browser.new_page(viewport=viewport or {'width': 1440, 'height': 1000}, accept_downloads=True)
        self.errors = []
        self.page.on('pageerror', lambda e: self.errors.append(f'pageerror: {e}'))
        self.page.on('console', lambda m: self.errors.append(f'console: {m.text}') if m.type == 'error' else None)
        self.page.on('dialog', lambda d: (self.errors.append(f'dialog: {d.message}'), d.dismiss()))

    def close(self):
        self.browser.close()
        # A 400 from a deliberately invalid input logs "Failed to load resource"; everything else is a defect.
        assert [e for e in self.errors if 'status of 400' not in e] == []


def settings_of(page, base, ds):
    return page.request.get(base + f'/api/stock?dataset={ds}').json()['forecast']['settings']


def rows(page, tbody):
    return page.locator(f'{tbody} tr')


# ---------------------------------------------------------------- navigation and empty state
@pytest.mark.browser
def test_navigation_routing_and_empty_workspace(tmp_path):
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p); page = s.page
        page.goto(base + '/#no-such-page')
        expect(page.get_by_role('heading', name='Stock alerts', exact=True)).to_be_visible()  # unknown hash -> Stock alerts
        assert page.locator('#nav a').all_inner_texts() == NAV
        for label, key in zip(NAV, ('stock', 'predicted', 'insights', 'data', 'settings')):
            page.locator('#nav').get_by_role('link', name=label).click()
            expect(page).to_have_url(f'{base}/#{key}')
            expect(page.get_by_role('heading', name=label, exact=True)).to_be_visible()
            expect(page.locator('#nav a[aria-current="page"]')).to_have_text(label)
            assert page.title() == f'{label} · Stock Alerts'
            if key != 'data':
                expect(page.get_by_text('Import a workbook first')).to_be_visible()
        page.goto(base + '/#insights')
        page.get_by_role('link', name='Go to Data sources').click()
        expect(page).to_have_url(f'{base}/#data')
        expect(page.locator('#file-input')).to_be_visible()
        page.go_back()
        expect(page.get_by_role('heading', name='Replenishment insights', exact=True)).to_be_visible()
        s.close()


# ---------------------------------------------------------------- data sources
@pytest.mark.browser
def test_data_sources_every_import_path_and_error(tmp_path):
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p); page = s.page
        page.goto(base + '/#data')
        page.locator('#file-input').set_input_files(upload('notes.txt', b'not a workbook'))
        expect(page.locator('#toast')).to_have_text('Choose an XLSX file under 32 MB.')
        expect(page.locator('#toast')).to_have_class('error')

        page.locator('#file-input').set_input_files(upload('wb.xlsx', WORKBOOK))
        expect(page.locator('#toast')).to_have_text('Workbook imported.')
        expect(page.get_by_role('heading', name='Source inventory')).to_be_visible()
        expect(page.get_by_text('Not imported: add the Min/Max file')).to_be_visible()

        page.locator('#ref-input').set_input_files(upload('notes.pdf', b'%PDF'))
        expect(page.locator('#toast')).to_have_text('Choose an XLSX or CSV file under 32 MB.')

        page.locator('#ref-input').set_input_files(upload('minmax.xlsx', tolerance_bytes(TOLS)))
        expect(page.locator('#toast')).to_contain_text('Reference file imported (tolerances).')
        expect(page.locator('.step', has_text='Min/Max tolerances')).to_contain_text(f'{len(TOLS)} rows')

        page.locator('#ref-input').set_input_files(upload('bom.xlsx', BOM))
        expect(page.locator('#toast')).to_contain_text('Reference file imported (bom).')
        expect(page.locator('.step', has_text='Bill of materials')).to_contain_text('Imported')

        unknown = xlsx_bytes({'Sheet1': [['Colour', 'Shape'], ['red', 'square']]})
        page.locator('#ref-input').set_input_files(upload('unknown.xlsx', unknown))
        expect(page.locator('#ref-status')).to_contain_text('Unrecognized reference file')
        expect(page.locator('#toast')).to_have_class('error')

        snapshot = xlsx_bytes({'Snap': [['Material', 'Storage Type', 'Storage Bin', 'Available stock'], ['PRED', '401', 'BPRED', 40]]})
        page.locator('#ref-input').set_input_files(upload('snapshot.xlsx', snapshot))  # no date picked
        expect(page.locator('#ref-status')).to_contain_text('date')
        page.locator('#ref-asof').fill('2026-10-20')
        page.locator('#ref-input').set_input_files(upload('snapshot.xlsx', snapshot))
        expect(page.locator('#toast')).to_contain_text('Reference file imported (snapshot).')
        expect(page.locator('.step', has_text='Stock snapshots')).to_contain_text('2026-10-20 (1 row)')

        daily = xlsx_bytes({'Sheet1': [DAILY_HEADER, ['BM', 'x', 401, 'BBM', 12, 100, 3], ['PRED', 'x', 401, 'BPRED', 10, 100, 13]]})
        page.locator('#ref-input').set_input_files(upload('Bin_Status_2026-10-22.xlsx', daily))
        expect(page.locator('#toast')).to_contain_text('daily bin status, saved as a snapshot')
        expect(page.locator('#ref-status')).to_contain_text('Imported Bin_Status_2026-10-22.xlsx (daily bin status, saved as a snapshot, 2026-10-22).')
        expect(page.locator('#ref-status')).to_contain_text('1 row: Min/Max differ from the tolerance file; the tolerance file is used.')
        expect(page.locator('.step', has_text='Stock snapshots')).to_contain_text('2026-10-22 (2 rows)')
        expect(page.locator('.strip div', has_text='Stock snapshots').locator('b')).to_have_text('2')
        expect(page.locator('.step', has_text='Reference file attached (snapshot').first).to_be_visible()  # recent activity

        page.locator('#nav').get_by_role('link', name='Stock alerts').click()
        expect(page.locator('.asof')).to_contain_text('snapshot 2026-10-22')  # the latest daily file drives stock
        s.close()


# ---------------------------------------------------------------- Phase 1 stock alerts
@pytest.mark.browser
def test_stock_alerts_every_control(tmp_path):
    mails = lambda: len(list((tmp_path / 'outbox').glob('*.eml')))
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p); page = s.page
        seed(page, base)
        page.goto(base + '/#stock')
        expect(page.locator('#stock-alert-count')).to_have_text('1')
        expect(page.locator('#stock-count')).to_contain_text('63 matching positions · page 1 of 2')
        expect(rows(page, '#stock-rows')).to_have_count(50)

        expect(page.locator('#stock-prev')).to_be_disabled()
        page.locator('#stock-next').click()
        expect(page.locator('#stock-count')).to_contain_text('page 2 of 2')
        expect(rows(page, '#stock-rows')).to_have_count(13)
        expect(page.locator('#stock-next')).to_be_disabled()
        page.locator('#stock-prev').click()
        expect(page.locator('#stock-count')).to_contain_text('page 1 of 2')

        page.locator('#stock-filter').select_option('Alert')
        expect(rows(page, '#stock-rows')).to_have_count(1)
        expect(rows(page, '#stock-rows').first).to_contain_text('BM')
        page.locator('#stock-filter').select_option('OK')
        expect(page.locator('#stock-count')).to_contain_text('62 matching')
        page.locator('#stock-filter').select_option('edited')
        expect(rows(page, '#stock-rows').first).to_have_text('No positions match.')
        page.locator('#stock-filter').select_option('all')
        page.locator('#stock-search').fill('synthetic far')
        expect(rows(page, '#stock-rows')).to_have_count(1)
        page.locator('#stock-search').fill('')

        # Acknowledge the open alert.
        bm = rows(page, '#stock-rows').filter(has_text='BBM')
        bm.get_by_role('button', name='Acknowledge').click()
        expect(bm).to_contain_text('Acknowledged since')
        expect(bm.get_by_role('button', name='Acknowledge')).to_have_count(0)

        # Invalid entry is rejected locally. FAR and SAFE sort onto page 2, so narrow the list first.
        page.locator('#stock-search').fill('BFAR')
        far = page.get_by_label('Available stock for FAR BFAR')
        far.fill('-1'); page.locator('[data-stock-set="FAR|401|BFAR"]').click()
        expect(page.locator('#toast')).to_have_text('Enter a whole number of zero or more.')

        # Set with the button: FAR drops to its minimum -> new alert, email saved to the outbox.
        before = mails()
        far = page.get_by_label('Available stock for FAR BFAR')
        far.fill('8'); page.locator('[data-stock-set="FAR|401|BFAR"]').click()
        expect(page.locator('#stock-alert-count')).to_have_text('2')
        expect(page.locator('#toast')).to_contain_text('email saved')
        expect(page.locator('#mail-preview')).to_contain_text('FAR')
        assert mails() == before + 1
        expect(page.locator('.strip div', has_text='Simulated edits').locator('b')).to_have_text('1')
        expect(page.get_by_label('Available stock for FAR BFAR')).to_have_class('cell edited')

        # Set with Enter on another row, then the "Simulated edits" filter.
        page.locator('#stock-search').fill('BSAFE')
        safe = page.get_by_label('Available stock for SAFE BSAFE')
        safe.fill('70'); safe.press('Enter')
        expect(page.locator('#toast')).to_have_text('Saved. No alert change.')
        page.locator('#stock-search').fill('')
        page.locator('#stock-filter').select_option('edited')
        expect(rows(page, '#stock-rows')).to_have_count(2)

        # Reset one override with its row button.
        page.locator('[data-stock-clear="SAFE|401|BSAFE"]').click()
        expect(rows(page, '#stock-rows')).to_have_count(1)
        page.locator('#stock-filter').select_option('all')

        # Run check.
        page.get_by_role('button', name='Run check').click()
        expect(page.locator('#toast')).to_be_visible()

        # Send digest: one click arms, the arm expires, then two clicks send.
        before = mails()
        page.get_by_role('button', name='Send digest').click()
        expect(page.get_by_role('button', name='Click again to send')).to_be_visible()
        page.wait_for_timeout(4300)
        expect(page.get_by_role('button', name='Send digest')).to_be_visible()
        assert mails() == before
        page.get_by_role('button', name='Send digest').click()
        page.get_by_role('button', name='Click again to send').click()
        expect(page.locator('#toast')).to_contain_text('Digest')
        assert mails() == before + 1

        # Reset simulated stock needs confirmation and restores the source value.
        page.get_by_role('button', name='Reset simulated stock').click()
        expect(page.locator('#stock-alert-count')).to_have_text('2')
        page.get_by_role('button', name='Click again to reset').click()
        expect(page.locator('#stock-alert-count')).to_have_text('1')
        expect(page.locator('.strip div', has_text='Simulated edits').locator('b')).to_have_text('0')
        s.close()


# ---------------------------------------------------------------- Phase 2 predicted alerts
@pytest.mark.browser
def test_predicted_alerts_every_control(tmp_path):
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p); page = s.page
        ds = seed(page, base)
        page.goto(base + '/#predicted')
        expect(page.locator('#stock-predicted-count')).to_have_text('1')
        expect(page.locator('#stock-watch-count')).to_have_text('2')
        expect(page.locator('.strip div', has_text='Already below minimum').locator('b')).to_have_text('1')
        expect(page.locator('.strip div', has_text='No rate').locator('b')).to_have_text('56')
        expect(page.locator('.asof')).to_contain_text('7 positions with a demand rate')

        body = '#pred-rows'
        expect(rows(page, body)).to_have_count(4)  # Predicted, Watch and below minimum, sorted by hours
        assert [r.split('\n')[0] for r in rows(page, body).all_inner_texts()] == \
            ['Synthetic bm', 'Synthetic pred', 'Synthetic freq', 'Synthetic safe']
        expect(rows(page, body).filter(has_text='BPRED')).to_contain_text('Predicted')
        expect(rows(page, body).filter(has_text='BSAFE')).to_contain_text('Watch')
        expect(rows(page, body).filter(has_text='BBM')).to_contain_text('Alert')
        expect(rows(page, body).filter(has_text='BSAFE').locator('svg.hb')).to_have_count(1)

        page.locator('#pred-filter').select_option('rate')
        expect(rows(page, body)).to_have_count(7)
        expect(rows(page, body).filter(has_text='BDIV')).to_contain_text('OK')
        page.locator('#pred-filter').select_option('norate')
        expect(page.locator('.foot')).to_contain_text('56 matching positions · page 1 of 2')
        page.locator('#pred-next').click()
        expect(rows(page, body)).to_have_count(6)
        expect(page.locator('#pred-next')).to_be_disabled()
        page.locator('#pred-prev').click()
        expect(rows(page, body)).to_have_count(50)
        page.locator('#pred-filter').select_option('all')
        expect(page.locator('.foot')).to_contain_text('63 matching positions')
        page.locator('#pred-search').fill('BDIV')
        expect(rows(page, body)).to_have_count(1)
        expect(page.locator('#pred-search')).to_be_focused()  # re-render keeps typing focus

        # Click a row: the detail explains the schedule-only demand (history dropped by the divergence rule).
        rows(page, body).first.click()
        detail = page.locator('#pred-detail')
        expect(detail).to_contain_text('Synthetic div')
        expect(detail).to_contain_text('history dropped')
        expect(detail).to_contain_text('Demand per day60 (schedule)')
        page.locator('#pred-search').fill('')
        page.locator('#pred-filter').select_option('act')

        # Keyboard: Enter on a focused row selects it.
        safe_row = rows(page, body).filter(has_text='BSAFE')
        safe_row.focus(); safe_row.press('Enter')
        expect(detail).to_contain_text('Synthetic safe')
        expect(detail).to_contain_text('(60 − 10) / 30 × 24 = 40 h')
        expect(detail.locator('svg[role="img"]')).to_have_count(1)  # projection chart
        expect(detail).to_contain_text('Suggested quantity44')

        # Lead class change saves and re-renders.
        with page.expect_response('**/api/stock/lead-class') as info:
            detail.locator('select[data-lead-pos]').select_option('fab')
        assert info.value.ok
        expect(detail.locator('select[data-lead-pos]')).to_have_value('fab')

        # Settings summary link.
        detail.get_by_role('link', name='Edit').click()
        expect(page).to_have_url(f'{base}/#settings')
        s.close()


# ---------------------------------------------------------------- View 2 replenishment insights
@pytest.mark.browser
def test_replenishment_insights_every_control(tmp_path):
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p); page = s.page
        ds = seed(page, base)
        page.goto(base + '/#insights')
        expect(page.locator('#insights-flagged-count')).to_have_text('3')
        expect(page.locator('.strip')).to_contain_text('Short refills (< 50%, ≥ 3 refills)')
        expect(page.locator('.asof')).to_contain_text('2026-10-01 to 2026-10-10')
        body = '#parts-rows'
        expect(rows(page, body)).to_have_count(3)
        expect(rows(page, body).filter(has_text='BSHORT')).to_contain_text('Short refill')
        expect(rows(page, body).filter(has_text='BFREQ')).to_contain_text('Frequent')
        expect(rows(page, body).filter(has_text='BDIV')).to_contain_text('Divergent')
        expect(rows(page, body).filter(has_text='BDIV')).to_contain_text('30 / 60')

        for lens, material in (('short_refills', 'BSHORT'), ('frequent_refills', 'BFREQ'), ('divergent', 'BDIV')):
            page.locator('#insights-lens').select_option(lens)
            expect(rows(page, body)).to_have_count(1)
            expect(rows(page, body).first).to_contain_text(material)
        page.locator('#insights-lens').select_option('all')
        expect(page.locator('.foot')).to_contain_text('63 matching parts · page 1 of 2')
        page.locator('#parts-next').click()
        expect(rows(page, body)).to_have_count(13)
        page.locator('#parts-prev').click()
        expect(rows(page, body)).to_have_count(50)
        page.locator('#insights-search').fill('synthetic short')
        expect(rows(page, body)).to_have_count(1)
        expect(page.locator('#insights-search')).to_be_focused()

        # Row click and keyboard selection drive the detail panel.
        rows(page, body).first.click()
        detail = page.locator('#part-detail')
        expect(detail).to_contain_text('Synthetic short')
        expect(detail.locator('svg rect')).to_have_count(3)  # one bar per refill
        expect(detail).to_contain_text('Median refill % of Max10%')
        page.locator('#insights-search').fill('')
        page.locator('#insights-lens').select_option('flagged')
        freq = rows(page, body).filter(has_text='BFREQ')
        freq.focus(); freq.press('Enter')
        expect(detail).to_contain_text('Refills in 30 days9')

        # Every threshold re-queries the API and updates the CSV link.
        for field, value, flagged in (('#th-min-refills', '4', '2'), ('#th-refills', '9', '1'), ('#th-divergence', '150', '0'),
                                      ('#th-refill-pct', '100', '1')):
            with page.expect_response('**/api/insights/parts?*') as info:
                page.locator(field).fill(value); page.locator(field).dispatch_event('change')
            assert info.value.ok
            expect(page.locator('#insights-flagged-count')).to_have_text(flagged)
        href = page.locator('#export-parts-csv').get_attribute('href')
        for part in ('refill_pct=100', 'refills=9', 'divergence=150', 'min_refills=4'):
            assert part in href
        expect(rows(page, body).first).to_contain_text('BFREQ')  # 9 refills at 60% are short under 100%; SHORT has only 3 refills

        # An invalid threshold keeps the table and restores the last valid value.
        with page.expect_response('**/api/insights/parts?*') as info:
            page.locator('#th-min-refills').fill('0'); page.locator('#th-min-refills').dispatch_event('change')
        assert info.value.status == 400
        expect(page.locator('#toast')).to_contain_text('min_refills must be a number between 1 and 100')
        expect(page.locator('#th-min-refills')).to_have_value('4')
        expect(page.locator('#insights-flagged-count')).to_have_text('1')

        # Export CSV downloads the same report.
        with page.expect_download() as info:
            page.locator('#export-parts-csv').click()
        download = info.value
        assert download.suggested_filename == 'problem-parts.csv'
        table = list(csv.DictReader(StringIO(open(download.path(), encoding='utf-8').read())))
        assert len(table) == 63
        assert {r['material']: r['flags'] for r in table if r['flags']} == {'FREQ': 'short_refills'}
        s.close()


# ---------------------------------------------------------------- settings, weights preview and presets
@pytest.mark.browser
def test_settings_form_weights_preview_and_presets(tmp_path):
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p); page = s.page
        ds = seed(page, base)
        page.goto(base + '/#settings')
        expected = {'lead_hours_stocked': '3', 'lead_hours_fab': '3', 'safety_hours': '24', 'watch_hours': '72',
                    'history': '1', 'schedule': '1', 'observed': '0', 'divergence_pct': '50'}
        for key, value in expected.items():
            expect(page.locator(f'#fc-{key}')).to_have_value(value)

        # Invalid value: inline error, nothing saved.
        page.locator('#fc-watch_hours').fill('9999')
        page.get_by_role('button', name='Save forecast settings').click()
        expect(page.locator('#forecast-error')).to_contain_text('720')
        assert settings_of(page, base, ds)['watch_hours'] == 72

        # Valid save: Watch window 30 h removes SAFE (40 h) and FREQ (37.8 h) from Watch.
        page.locator('#fc-watch_hours').fill('30')
        with page.expect_response('**/api/stock/settings') as info:
            page.get_by_role('button', name='Save forecast settings').click()
        assert info.value.ok
        expect(page.locator('#forecast-error')).to_have_text('')
        assert settings_of(page, base, ds)['watch_hours'] == 30
        page.locator('#nav').get_by_role('link', name='Predicted alerts').click()
        expect(page.locator('#stock-watch-count')).to_have_text('0')
        expect(page.locator('.strip')).to_contain_text('Watch within 30 h')
        page.locator('#nav').get_by_role('link', name='Settings').click()

        # Sliders preview without saving.
        expect(page.locator('#preview-box')).to_contain_text('Move a slider to preview alert counts.')
        with page.expect_response('**/api/stock/preview') as info:
            page.locator('#tw-history').fill('2')
        assert info.value.ok and info.value.request.post_data_json['weights']['history'] == 2
        expect(page.locator('#val-history')).to_have_text('2')
        expect(page.locator('#preview-box table').first).to_contain_text('Predicted')
        assert settings_of(page, base, ds)['weights']['history'] == 1
        for slider, value, label, shown in (('#tw-schedule', '0', '#val-schedule', '0'), ('#tw-observed', '1.5', '#val-observed', '1.5'),
                                            ('#tw-divergence', '100', '#val-divergence', '100%')):
            with page.expect_response('**/api/stock/preview'):
                page.locator(slider).fill(value)
            expect(page.locator(label)).to_have_text(shown)
        expect(page.locator('#preview-box')).to_contain_text('No tier changes with these weights.')
        # History 0 with schedule 0 leaves PRED without a rate: the tier change is listed in plain words.
        with page.expect_response('**/api/stock/preview'):
            page.locator('#tw-history').fill('0')
        expect(page.locator('#preview-box')).to_contain_text('Tier changes')
        expect(page.locator('#preview-box tr', has_text='BPRED')).to_contain_text('Predicted → No rate')
        with page.expect_response('**/api/stock/preview'):
            page.locator('#tw-history').fill('2')

        # Override off disables the divergence slider and previews with 1000 %.
        with page.expect_response('**/api/stock/preview') as info:
            page.locator('#tw-override-off').check()
        assert info.value.request.post_data_json['divergence_pct'] == 1000
        expect(page.locator('#tw-divergence')).to_be_disabled()
        expect(page.locator('#val-divergence')).to_have_text('off')
        with page.expect_response('**/api/stock/preview'):
            page.locator('#tw-override-off').uncheck()
        expect(page.locator('#tw-divergence')).to_be_enabled()
        expect(page.locator('#val-divergence')).to_have_text('100%')

        # Presets: empty name rejected, save, survives reload, apply, delete with confirmation.
        page.get_by_role('button', name='Save as preset').click()
        expect(page.locator('#toast')).to_have_text('Enter a preset name (1–40 characters).')
        page.locator('#preset-name').fill('History 2:1')
        page.get_by_role('button', name='Save as preset').click()
        expect(page.locator('#preset-list')).to_contain_text('History 2:1')
        expect(page.locator('#preset-list')).to_contain_text('H 2 · S 0 · O 1.5')
        expect(page.locator('#preset-name')).to_have_value('')
        assert settings_of(page, base, ds)['weights']['history'] == 1  # saving a preset does not apply it

        # Apply these weights.
        with page.expect_response('**/api/stock/settings') as info:
            page.get_by_role('button', name='Apply these weights').click()
        assert info.value.ok
        applied = settings_of(page, base, ds)
        assert applied['weights'] == {'history': 2.0, 'schedule': 0.0, 'observed': 1.5} and applied['divergence_pct'] == 100
        expect(page.locator('#fc-history')).to_have_value('2')
        expect(page.locator('#preview-box')).to_contain_text('Move a slider')  # preview cleared after apply

        # Restore defaults through the form, then apply the preset after a reload.
        for key, value in (('history', '1'), ('schedule', '1'), ('observed', '0'), ('divergence_pct', '50')):
            page.locator(f'#fc-{key}').fill(value)
        with page.expect_response('**/api/stock/settings'):
            page.get_by_role('button', name='Save forecast settings').click()
        page.reload()
        expect(page.locator('#preset-list')).to_contain_text('History 2:1')
        with page.expect_response('**/api/stock/settings'):
            page.locator('[data-apply-preset="History 2:1"]').click()
        assert settings_of(page, base, ds)['weights']['history'] == 2
        delete = page.locator('[data-delete-preset="History 2:1"]')
        delete.click()
        expect(delete).to_have_text('Click again to delete')
        expect(page.locator('#preset-list')).to_contain_text('History 2:1')
        delete.click()
        expect(page.locator('#preset-list')).to_contain_text('No saved presets yet.')
        assert page.request.get(base + f'/api/stock/presets?dataset={ds}').json() == []
        s.close()


# ---------------------------------------------------------------- layout on every page
@pytest.mark.browser
@pytest.mark.parametrize('width,height', [(1440, 900), (1280, 800)])
def test_every_page_and_table_fits_at_desktop_widths(tmp_path, width, height):
    with running_server(tmp_path) as base, sync_playwright() as p:
        s = Session(p, {'width': width, 'height': height}); page = s.page
        seed(page, base)
        for name in ('stock', 'predicted', 'insights', 'data', 'settings'):
            page.goto(f'{base}/#{name}')
            expect(page.locator('#main h1')).to_be_visible()
            page.wait_for_load_state('networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
            clipped = page.evaluate("[...document.querySelectorAll('.tw')].filter(t => t.scrollWidth > t.clientWidth + 1).length")
            assert clipped == 0, f'{name}: a table needs sideways scrolling at {width}px'
        s.close()
