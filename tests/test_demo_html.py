"""The single-file demo: built from the real index.html, invented data, an in-browser API; parity with the Python backend."""
import csv
import json
from io import StringIO
from pathlib import Path
import re
import sys

from playwright.sync_api import expect, sync_playwright
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'scripts' / 'demo')]
import build_demo_html  # noqa: E402
import export_data  # noqa: E402

DATASET = None


@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('demo')
    data, store, ds = export_data.export(tmp)
    out = tmp / 'demo.html'
    build_demo_html.build(out, {'stock': 'data:image/png;base64,AAAA'}, data)
    return dict(html=out, url=out.as_uri(), data=data, store=store, ds=ds, text=out.read_text())


class Page:
    """A browser page on the demo file that fails on console errors, dialogs, and any request leaving the file."""
    def __init__(self, p, url, viewport=None):
        self.browser = p.chromium.launch()
        self.page = self.browser.new_page(viewport=viewport or {'width': 1440, 'height': 1000}, accept_downloads=True)
        self.errors, self.requests = [], []
        self.page.on('pageerror', lambda e: self.errors.append(f'pageerror: {e}'))
        self.page.on('console', lambda m: self.errors.append(f'console: {m.text}') if m.type == 'error' and 'status of 400' not in m.text else None)
        self.page.on('dialog', lambda d: (self.errors.append(f'dialog: {d.message}'), d.dismiss()))
        self.page.on('request', lambda r: self.requests.append(r.url) if not r.url.startswith(('file:', 'data:', 'blob:')) else None)
        self.page.goto(url)

    def close(self):
        self.browser.close()
        assert self.errors == []
        assert self.requests == [], self.requests


def call(page, path, body=None):
    return page.evaluate('''async ([path, body]) => {
        const r = await fetch(path, body === null ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        return {status: r.status, body: await r.json()}; }''', [path, body])


# ---------------------------------------------------------------- build
def test_build_is_one_self_contained_file_and_leaves_the_real_app_alone(demo):
    text = demo['text']
    assert 'PRACTICE COPY' in text and 'Testing guide' in text and 'window.fetch = async' in text
    assert re.search(r'(src|href)="https?://', text) is None and '@import' not in text      # nothing is loaded from the network
    assert 'PRACTICE COPY' not in (ROOT / 'index.html').read_text()
    assert 'DEMO-1001' in text


def test_mock_data_is_invented_and_has_every_tier(demo):
    pos = demo['data']['stock']['positions']
    assert all(p['material'].startswith('DEMO-') for p in pos)
    counts = demo['data']['stock']['forecast']['summary']
    assert min(counts.values()) >= 10 and len(pos) >= 100
    flags = demo['data']['parts']['summary']
    assert min(flags[k] for k in ('short_refills', 'frequent_refills', 'divergent')) >= 10


# ---------------------------------------------------------------- parity with the Python backend
SETTINGS_CASES = [
    {}, {'lead_hours_stocked': 40}, {'watch_hours': 10}, {'safety_hours': 0}, {'lead_hours_fab': 100, 'safety_hours': 100},
    {'weights': {'history': 1, 'schedule': 1}, 'divergence_pct': 50}, {'weights': {'history': 2, 'schedule': 0}},
    {'weights': {'history': 0, 'schedule': 2}}, {'weights': {'observed': 1}}, {'divergence_pct': 20}, {'divergence_pct': 0},
]


def close(a, b):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 0.011 + 1e-9 * abs(b))


def compare_views(js, py):
    assert js['forecast']['summary'] == py['forecast']['summary']
    assert js['forecast']['settings'] == py['forecast']['settings']
    assert js['summary'] == py['summary']
    a, b = {p['key']: p for p in js['positions']}, {p['key']: p for p in py['positions']}
    assert a.keys() == b.keys()
    for key, p in b.items():
        q = a[key]
        for field in ('status', 'forecast', 'available', 'min', 'max', 'lead_class', 'lead_hours', 'demand_basis', 'demand_dropped', 'overridden', 'suggested_qty', 'no_rate_reason'):
            assert q.get(field) == p.get(field), (key, field, q.get(field), p.get(field))
        assert close(q['hours_to_min'], p['hours_to_min']) and close(q['demand_per_day'], p['demand_per_day']), key
    alerts = lambda v: sorted((x['position'], x['kind'], x['status']) for x in v['open_alerts'])
    assert alerts(js) == alerts(py)


def test_preview_matches_the_python_backend_for_many_settings(demo):
    store, ds = demo['store'], demo['ds']
    with sync_playwright() as p:
        s = Page(p, demo['url'])
        for changes in SETTINGS_CASES:
            expected = store.preview_settings(ds, changes)
            got = call(s.page, '/api/stock/preview', {'dataset': ds, **changes})['body']
            assert got['candidate']['summary'] == expected['candidate']['summary'], changes
            assert got['current']['summary'] == expected['current']['summary']
            assert got['candidate']['settings'] == expected['candidate']['settings']
            key = lambda c: (c['key'], c['from'], c['to'])
            assert sorted(map(key, got['changed'])) == sorted(map(key, expected['changed'])) or len(expected['changed']) >= 50, changes
        s.close()


def test_edits_lead_class_settings_and_alerts_match_the_python_backend(demo, tmp_path):
    ds = demo['ds']
    store, pds = export_data.build_store(tmp_path)   # a fresh store: the parity run must not share state with other tests
    pos = demo['data']['stock']['positions']
    by = lambda forecast: [p for p in pos if p['forecast'] == forecast]
    ok, watch, pred, below = by('ok')[0], by('watch')[0], by('within_lead')[0], by('below_min')[0]
    with sync_playwright() as p:
        s = Page(p, demo['url'])
        steps = [
            ('/api/stock/available', lambda: store.set_available(pds, ok['key'], ok['min']), {'dataset': ds, 'position': ok['key'], 'available': ok['min']}),
            ('/api/stock/available', lambda: store.set_available(pds, watch['key'], 1), {'dataset': ds, 'position': watch['key'], 'available': 1}),
            ('/api/stock/available', lambda: store.set_available(pds, below['key'], below['min'] + 5000), {'dataset': ds, 'position': below['key'], 'available': below['min'] + 5000}),
            ('/api/stock/lead-class', lambda: store.set_lead_class(pds, pred['key'], 'fab'), {'dataset': ds, 'position': pred['key'], 'lead_class': 'fab'}),
            ('/api/stock/settings', lambda: store.update_stock_settings(pds, {'lead_hours_stocked': 40, 'weights': {'history': 1}}),
             {'dataset': ds, 'lead_hours_stocked': 40, 'weights': {'history': 1}}),
            ('/api/stock/ack-all', lambda: store.acknowledge_all_stock_alerts(pds), {'dataset': ds}),
            ('/api/stock/available', lambda: store.set_available(pds, ok['key'], None), {'dataset': ds, 'position': ok['key'], 'available': None}),
            ('/api/stock/reset', lambda: store.reset_stock(pds), {'dataset': ds}),
        ]
        compare_views(call(s.page, f'/api/stock?dataset={ds}')['body'], store.stock_view(pds))
        for path, run_python, body in steps:
            expected = run_python()
            got = call(s.page, path, body)
            assert got['status'] == 200, (path, got)
            for k in ('new', 'resolved', 'acknowledged'):
                if k in expected: assert got['body'][k] == expected[k], (path, k, got['body'], expected)
            assert bool(got['body'].get('notification')) == bool(expected.get('notification')), (path, got['body'], expected)
            compare_views(call(s.page, f'/api/stock?dataset={ds}')['body'], store.stock_view(pds))
        s.close()


def test_insights_flags_and_csv_match_the_python_backend(demo):
    store, ds = demo['store'], demo['ds']
    cases = [{}, {'refill_size_pct': 30}, {'refills_30d': 5}, {'divergence_pct': 100}, {'min_refills': 1}, {'min_refills': 5, 'refill_size_pct': 80}]
    names = dict(refill_size_pct='refill_pct', refills_30d='refills', divergence_pct='divergence', min_refills='min_refills')
    with sync_playwright() as p:
        s = Page(p, demo['url'])
        for th in cases:
            expected = store.parts_insights(ds, th)
            query = '&'.join(f'{names[k]}={v}' for k, v in th.items())
            got = call(s.page, f'/api/insights/parts?dataset={ds}&{query}')['body']
            assert got['summary'] == expected['summary'] and got['thresholds'] == expected['thresholds'], th
            assert {p['key']: p['flags'] for p in got['parts']} == {p['key']: p['flags'] for p in expected['parts']}
            js_csv = list(csv.DictReader(StringIO(s.page.evaluate('q => window.DEMO.csv(q)', query))))
            from siemens_capstone.insights_parts import to_csv
            py_csv = list(csv.DictReader(StringIO(to_csv(expected))))
            assert len(js_csv) == len(py_csv)
            for a, b in zip(js_csv, py_csv):
                for field in ('key', 'material', 'refills_30d', 'flags'): assert a[field] == b[field]
                for field in ('min', 'max', 'median_refill_pct', 'history_per_day', 'schedule_per_day'):
                    assert (a[field] == '') == (b[field] == '') and (a[field] == '' or abs(float(a[field]) - float(b[field])) < 1e-6), (field, a, b)
        assert call(s.page, f'/api/insights/parts?dataset={ds}&min_refills=0')['status'] == 400
        s.close()


def test_validation_errors_match(demo):
    ds = demo['ds']
    with sync_playwright() as p:
        s = Page(p, demo['url'])
        for path, body in (('/api/stock/settings', {'dataset': ds, 'watch_hours': 9999}), ('/api/stock/settings', {'dataset': ds, 'bogus': 1}),
                           ('/api/stock/settings', {'dataset': ds, 'weights': {'history': -1}}), ('/api/stock/available', {'dataset': ds, 'position': 'x', 'available': 1}),
                           ('/api/stock/available', {'dataset': ds, 'position': demo['data']['stock']['positions'][0]['key'], 'available': -5}),
                           ('/api/stock/presets', {'dataset': ds, 'name': '', 'settings': {}}), ('/api/import?filename=a.xlsx', {})):
            r = call(s.page, path, body)
            assert r['status'] == 400 and r['body']['error'], (path, r)
        assert 'between 0 and 720' in call(s.page, '/api/stock/settings', {'dataset': ds, 'watch_hours': 9999})['body']['error']
        assert 'turned off' in call(s.page, '/api/import?filename=a.xlsx', {})['body']['error']
        assert call(s.page, '/api/unknown')['status'] == 404
        s.close()


# ---------------------------------------------------------------- the demo in a browser
def rows(page, tbody):
    return page.locator(f'{tbody} tr')


@pytest.mark.browser
def test_opens_on_the_guide_with_screenshots_and_every_task(demo):
    with sync_playwright() as p:
        s = Page(p, demo['url']); page = s.page
        expect(page.get_by_role('heading', name='Testing guide', exact=True)).to_be_visible()
        expect(page).to_have_url(re.compile(r'demo\.html$'))
        expect(page.locator('.demo-ribbon')).to_contain_text('nothing is saved or sent')
        assert page.locator('#nav a').all_inner_texts() == ['Stock alerts', 'Predicted alerts', 'Replenishment insights', 'Data sources', 'Settings', 'Testing guide']
        expect(page.locator('#nav a[aria-current="page"]')).to_have_text('Testing guide')
        assert page.locator('.guide-task').count() == 8
        for n in range(1, 9):
            expect(page.locator(f'#task-{n} h2')).to_be_visible()
            for label in ('Goal:', 'You should see:', 'Tell us if:'):
                expect(page.locator(f'#task-{n}')).to_contain_text(label)
        expect(page.locator('figure.shot img')).to_have_count(1)        # the test build embeds one placeholder image
        f = demo['data']['stock']['positions']
        expect(page.locator('#task-2')).to_contain_text('DEMO-')
        page.locator('.guide-toc [data-task="5"]').click()
        expect(page.locator('#task-5 h2')).to_be_in_viewport()
        for target in ('stock', 'predicted', 'insights', 'data', 'settings'):
            page.goto(f'{demo["url"]}#{target}'); expect(page.locator('#main h1')).to_be_visible()
        page.goto(demo['url'] + '#guide')
        page.locator('[data-demo-reset]').click()
        expect(page.locator('#toast')).to_have_text('Demo data reset.')
        s.close()


@pytest.mark.browser
def test_guide_screenshots_are_in_the_real_build_and_steps_use_real_values():
    out = ROOT / 'outputs' / 'Siemens_Replenishment_Demo.html'
    if not out.exists(): pytest.skip('run scripts/build_demo_html.py first')
    text = out.read_text()
    for name in ('stock', 'stock-edit', 'predicted', 'norate', 'insights', 'settings'):
        assert re.search(rf'"{name}": "data:image/png;base64,[A-Za-z0-9+/=]{{1000,}}"', text), name


@pytest.mark.browser
def test_stock_alerts_in_the_demo(demo):
    facts = build_demo_html.facts(demo['data'])
    ok = facts['okPart']
    with sync_playwright() as p:
        s = Page(p, demo['url'] + '#stock'); page = s.page
        expect(rows(page, '#stock-rows').first).to_be_visible()
        expect(page.locator('#stock-count')).to_contain_text('114 matching positions · page 1 of 3')
        page.locator('#stock-search').fill(facts['searchWord'])
        assert 0 < rows(page, '#stock-rows').count() < 50
        page.locator('#stock-search').fill('')
        page.locator('#stock-filter').select_option('Alert')
        expect(rows(page, '#stock-rows')).to_have_count(demo['data']['stock']['summary']['alerts'])
        page.locator('#stock-filter').select_option('all')
        page.locator('[data-sort="min"]').click()
        expect(page.locator('th:has([data-sort="min"])')).to_have_attribute('aria-sort', 'ascending')
        mins = page.evaluate("[...document.querySelectorAll('#stock-rows tr')].map(r => Number(r.children[3].innerText))")
        assert mins == sorted(mins)
        page.locator('[data-sort="min"]').click(); page.locator('[data-sort="min"]').click()

        # Task 2 and 3: simulate a breach, read the notification, acknowledge, reset.
        page.locator('#stock-search').fill(ok['material'])
        expect(rows(page, '#stock-rows')).to_have_count(1)
        page.locator('[data-stock-pos]').fill(str(int(ok['min']))); page.locator('[data-stock-pos]').press('Enter')
        expect(rows(page, '#stock-rows').first).to_contain_text('Alert')
        expect(page.locator('#mail-preview')).to_contain_text(ok['material'])
        expect(page.locator('#mail-preview')).to_contain_text('not sent')
        expect(page.locator('#toast')).to_contain_text('[Stock alert]')
        expect(page.locator('.strip')).to_contain_text('Simulated edits')
        button = page.locator('#clear-alerts')
        button.click(); expect(button).to_contain_text('Click again to acknowledge')
        button.click(); expect(page.locator('#toast')).to_contain_text('acknowledged. Stock levels unchanged')
        expect(rows(page, '#stock-rows').first).to_contain_text('Acknowledged')
        page.locator('[data-stock-clear]').click()
        expect(rows(page, '#stock-rows').first).to_contain_text('OK')
        page.locator('#stock-search').fill('')
        page.locator('[data-stock="check"]').click(); expect(page.locator('#toast')).to_be_visible()
        digest = page.locator('[data-stock="digest"]')
        digest.click(); expect(digest).to_have_text('Click again to send'); digest.click()
        expect(page.locator('#mail-preview')).to_contain_text('All open alerts')
        reset = page.locator('[data-stock="reset"]')
        reset.click(); reset.click()
        expect(page.locator('.strip')).to_contain_text('Simulated edits')
        page.locator('#stock-next').click(); expect(page.locator('#stock-count')).to_contain_text('page 2 of 3')
        s.close()


@pytest.mark.browser
def test_predicted_insights_settings_and_data_in_the_demo(demo, tmp_path):
    facts = build_demo_html.facts(demo['data'])
    cs = demo['data']['stock']['forecast']['summary']
    with sync_playwright() as p:
        s = Page(p, demo['url'] + '#predicted'); page = s.page
        expect(page.locator('#stock-predicted-count')).to_have_text(str(cs['predicted']))
        expect(page.locator('.strip')).to_contain_text('Predicted')
        expect(page.locator('.strip')).not_to_contain_text('emailed')
        page.locator('#pred-search').fill(facts['predictedPart']['material'])
        expect(rows(page, '#pred-rows')).to_have_count(1)
        page.locator('#pred-rows tr').click()
        expect(page.locator('#pred-detail')).to_contain_text('How it was calculated')
        expect(page.locator('#pred-detail')).to_contain_text('max(0,')
        with_fab = page.locator('[data-lead-pos]'); with_fab.select_option('fab')
        expect(with_fab).to_have_value('fab')
        with_fab.select_option('stocked')
        page.locator('#pred-search').fill('')
        page.locator('#pred-filter').select_option('norate')
        expect(page.locator('#no-rate-why')).to_contain_text(f"Why {facts['noRate']} positions have no rate")
        expect(rows(page, '#pred-rows').first.locator('.why')).to_be_visible()

        page.goto(demo['url'] + '#insights')
        expect(page.locator('#insights-flagged-count')).to_have_text(str(demo['data']['parts']['summary']['flagged']))
        page.locator('[data-flag="frequent_refills"]').click()
        expect(rows(page, '#parts-rows')).to_have_count(demo['data']['parts']['summary']['frequent_refills'])
        page.locator('[data-flag="divergent"]').click()
        assert rows(page, '#parts-rows').count() > demo['data']['parts']['summary']['frequent_refills'] - 1
        page.locator('#flag-all').click()
        expect(page.locator('#flag-hint')).to_contain_text('every part')
        page.locator('#flag-all').click()
        page.locator('#th-refills').fill('5'); page.locator('#th-refills').dispatch_event('change')
        expect(page.locator('.strip')).to_contain_text('Frequent refills (> 5)')
        with page.expect_download() as info: page.locator('#export-parts-csv').click()
        path = tmp_path / 'parts.csv'; info.value.save_as(path)
        text = path.read_text()
        assert text.splitlines()[0].startswith('key,material') and text.count('\n') > 100 and 'DEMO-' in text and info.value.suggested_filename == 'problem-parts.csv'

        page.goto(demo['url'] + '#settings')
        count = lambda t: page.locator(f'#tier-counter [data-tier="{t}"] b')
        expect(count('predicted')).to_have_text(str(cs['predicted']))
        page.locator('#tw-share').fill('0')
        expect(page.locator('#formulas-live')).to_contain_text('100% × schedule')
        page.locator('#tw-share').fill('67')
        page.locator('#fc-lead_hours_stocked').fill('100')
        expect(page.locator('#formulas-live')).to_contain_text('stocked 100 + 24')
        expect(page.locator('#tier-counter [data-tier="predicted"]')).to_contain_text('+')
        page.locator('#preset-name').fill('Longer lead')
        page.get_by_role('button', name='Save as preset').click()
        expect(page.locator('#preset-list')).to_contain_text('Longer lead')
        page.get_by_role('button', name='Save forecast settings').click()
        expect(page.locator('#toast')).to_be_visible()
        page.goto(demo['url'] + '#stock'); page.goto(demo['url'] + '#settings')
        expect(page.locator('#fc-lead_hours_stocked')).to_have_value('100')
        delete = page.locator('[data-delete-preset="Longer lead"]'); delete.click(); delete.click()
        expect(page.locator('#preset-list')).to_contain_text('No saved presets yet.')
        page.locator('#fc-watch_hours').fill('9999'); page.get_by_role('button', name='Save forecast settings').click()
        expect(page.locator('#forecast-error')).to_contain_text('720')

        page.goto(demo['url'] + '#data')
        expect(page.get_by_role('heading', name='Source inventory')).to_be_visible()
        page.locator('#file-input').set_input_files({'name': 'x.xlsx', 'mimeType': 'application/octet-stream', 'buffer': b'abc'})
        expect(page.locator('#toast')).to_contain_text('Importing files is turned off in this demo')

        # A refresh restores the starting data.
        page.reload(); page.goto(demo['url'] + '#settings')
        expect(page.locator('#fc-lead_hours_stocked')).to_have_value('3')
        s.close()


@pytest.mark.browser
@pytest.mark.parametrize('width,height', [(1440, 900), (1280, 800)])
def test_demo_pages_fit_at_desktop_widths(demo, width, height):
    with sync_playwright() as p:
        s = Page(p, demo['url'], {'width': width, 'height': height}); page = s.page
        for name in ('guide', 'stock', 'predicted', 'insights', 'data', 'settings'):
            page.goto(f'{demo["url"]}#{name}'); expect(page.locator('#main h1')).to_be_visible(); page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
            assert page.evaluate("[...document.querySelectorAll('.tw')].filter(t => t.scrollWidth > t.clientWidth + 1).length") == 0, name
        s.close()
