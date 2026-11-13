"""Build the single-file demo: the real index.html + an in-browser stand-in for the API + invented data + a testing guide.

    python scripts/build_demo_html.py [--out outputs/Siemens_Replenishment_Demo.html] [--no-shots]

No Siemens data is used. The result is one static HTML file with no network access and no setup.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'scripts' / 'demo'
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(DEMO)]
import export_data  # noqa: E402


def replace_once(text, old, new):
    if text.count(old) != 1: raise SystemExit(f'build_demo_html: expected exactly one match for: {old[:70]!r} (found {text.count(old)})')
    return text.replace(old, new)


def facts(data):
    pos = data['stock']['positions']
    rated = [p for p in pos if p['forecast'] == 'ok' and p['min'] and p['available'] and p['demand_per_day'] and p['available'] <= 900]
    ok_part = sorted(rated, key=lambda p: (p['available'], p['material']))[0]
    predicted = sorted([p for p in pos if p['forecast'] == 'within_lead'], key=lambda p: (p['hours_to_min'], p['material']))
    words = {}
    for p in pos: words[p['description'].split()[0]] = words.get(p['description'].split()[0], 0) + 1
    word = sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    s = data['stock']['forecast']['settings']
    keep = lambda p, keys: {k: p[k] for k in keys}
    return dict(searchWord=word, okPart=keep(ok_part, ('material', 'description', 'available', 'min', 'bin')),
                predictedPart=keep(predicted[0], ('material', 'description', 'hours_to_min')),
                noRate=data['stock']['forecast']['summary']['no_rate'], watchHours=s['watch_hours'],
                predictWindow=s['lead_hours_stocked'] + s['safety_hours'])


def build(out, shots=None, data=None):
    data = data or export_data.export()[0]
    html = (ROOT / 'index.html').read_text()
    engine = (DEMO / 'engine.js').read_text().replace('/*__DEMO_DATA__*/null', json.dumps(data, separators=(',', ':')))
    guide = (DEMO / 'guide.js').read_text().replace('/*__GUIDE_FACTS__*/null', json.dumps(facts(data))).replace('/*__GUIDE_SHOTS__*/{}', json.dumps(shots or {}))
    css = (DEMO / 'demo.css').read_text()
    html = replace_once(html, "const TITLES = Object.fromEntries([...PAGES, ...SETUP]);", "const HELP = [['guide', 'Testing guide']];\nconst TITLES = Object.fromEntries([...PAGES, ...SETUP, ...HELP]);")
    html = replace_once(html, "${SETUP.map(link).join('')}`;", "${SETUP.map(link).join('')}<div class=\"grp\">Help</div>${HELP.map(link).join('')}`;")
    html = replace_once(html, "settings: settingsPageHtml}[view]()", "settings: settingsPageHtml, guide: guidePageHtml}[view]()")
    html = replace_once(html, "view = TITLES[h] ? h : 'stock'", "view = TITLES[h] ? h : 'guide'")
    html = replace_once(html, "/* ---------- shell ---------- */", guide + "\n/* ---------- shell ---------- */")
    html = replace_once(html, "<script>\n'use strict';", f"<script>\n{engine}\n</script>\n<script>\n'use strict';")
    html = replace_once(html, "</style>", css + "</style>")
    html = replace_once(html, "<body>\n", "<body>\n<div class=\"demo-ribbon\" role=\"note\">PRACTICE COPY: invented parts, nothing is saved or sent. Refresh the page to start over. <a href=\"#guide\">Testing guide</a></div>\n")
    html = replace_once(html, "document.addEventListener('click', e => {\n  const b = e.target.closest('[data-stock]'); if (!b) return;",
        "document.addEventListener('click', e => {\n  const csv = e.target.closest('#export-parts-csv');\n"
        "  if (csv) { e.preventDefault(); const text = window.DEMO.csv(csv.getAttribute('href').split('?')[1] || ''); const url = URL.createObjectURL(new Blob([text], {type: 'text/csv'}));\n"
        "    const a = document.createElement('a'); a.href = url; a.download = 'problem-parts.csv'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); return; }\n"
        "  const b = e.target.closest('[data-stock]'); if (!b) return;")
    html = html.replace("[fmt(cs.predicted), 'Predicted, emailed',", "[fmt(cs.predicted), 'Predicted',")
    html = html.replace('<title>', '<title>Practice copy · ', 1) if '<title>' in html else html
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html)
    return Path(out).stat().st_size


def capture(page_url, shots_dir):
    """Annotated screenshots of the demo for the guide (numbers match the badges in the steps)."""
    from playwright.sync_api import sync_playwright
    mark = '''(items) => { for (const [sel, n] of items) { const el = document.querySelector(sel); if (!el) { console.log('missing ' + sel); continue; }
        const r = el.getBoundingClientRect(), box = document.createElement('div');
        box.className = 'shot-mark'; box.style.cssText = `position:fixed;left:${r.left - 3}px;top:${r.top - 3}px;width:${r.width + 6}px;height:${r.height + 6}px;border:3px solid #c0182c;border-radius:4px;z-index:50;pointer-events:none`;
        const b = document.createElement('div'); b.textContent = n;
        b.style.cssText = 'position:absolute;left:-12px;top:-14px;width:26px;height:26px;border-radius:50%;background:#c0182c;color:#fff;font:700 14px/26px sans-serif;text-align:center';
        box.appendChild(b); document.body.appendChild(box); } }'''
    shots = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(); page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e))); page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        def snap(name, items):
            page.evaluate("document.getElementById('toast').hidden = true"); page.evaluate(mark, items); page.wait_for_timeout(150)
            shots[name] = 'data:image/png;base64,' + base64.b64encode(page.screenshot()).decode()
            if shots_dir: (Path(shots_dir) / f'{name}.png').write_bytes(page.screenshot())
            page.evaluate("document.querySelectorAll('.shot-mark').forEach(e => e.remove())")
        f = facts_cache
        page.goto(page_url + '#stock'); page.wait_for_selector('#stock-rows tr')
        snap('stock', [('#stock-search', 1), ('#stock-filter', 2), ('[data-sort="min"]', 3), ('#stock-rows tr:first-child .tag', 4)])
        page.fill('#stock-search', f['okPart']['material']); page.wait_for_selector('#stock-rows tr')
        page.fill('[data-stock-pos]', str(int(f['okPart']['min']))); page.press('[data-stock-pos]', 'Enter')
        page.wait_for_selector('#mail-preview'); page.fill('#stock-search', f['okPart']['material'])
        page.wait_for_timeout(200)
        snap('stock-edit', [('[data-stock-pos]', 1), ('#stock-rows tr:first-child .tag', 2), ('#mail-preview', 3), ('#clear-alerts', 4)])
        page.goto(page_url + '#predicted'); page.wait_for_selector('#pred-rows tr')
        page.fill('#pred-search', f['predictedPart']['material']); page.click('#pred-rows tr[data-pred]'); page.wait_for_selector('.step')
        snap('predicted', [('.strip', 1), ('#pred-filter', 2), ('#pred-rows tr.sel', 3), ('#pred-detail section:nth-of-type(2)', 4), ('[data-lead-pos]', 5)])
        page.fill('#pred-search', ''); page.select_option('#pred-filter', 'norate'); page.wait_for_selector('#pred-rows .why')
        snap('norate', [('#no-rate-why', 1), ('#pred-filter', 2), ('#pred-rows tr:first-child .why', 3)])
        page.goto(page_url + '#insights'); page.wait_for_selector('#parts-rows tr'); page.click('[data-flag="frequent_refills"]')
        page.wait_for_timeout(200)
        snap('insights', [('#flag-chips', 1), ('.legend details', 2), ('.th-inputs', 3), ('#export-parts-csv', 4)])
        page.set_viewport_size({'width': 1280, 'height': 1850})
        page.goto(page_url + '#settings'); page.wait_for_selector('#formulas-live'); page.fill('#fc-lead_hours_stocked', '40'); page.wait_for_timeout(700)
        snap('settings', [('#tier-counter', 1), ('#tw-share', 2), ('#formulas-live', 3), ('#forecast-form button[type=submit]', 4)])
        browser.close()
    if errors: raise SystemExit('Demo page errors while taking screenshots: ' + '; '.join(errors[:3]))
    return shots


facts_cache = None


def main():
    global facts_cache
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=ROOT / 'outputs' / 'Siemens_Replenishment_Demo.html')
    parser.add_argument('--no-shots', action='store_true')
    args = parser.parse_args()
    data = export_data.export()[0]
    facts_cache = facts(data)
    size = build(args.out, None, data)
    if not args.no_shots:
        shots_dir = Path(tempfile.mkdtemp())
        shots = capture(args.out.resolve().as_uri(), shots_dir)
        size = build(args.out, shots, data)
        print('screenshots:', ', '.join(shots))
    archive = args.out.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(args.out, args.out.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(f'{digest}  {archive.name}\n')
    print(f'Wrote {args.out} ({size / 1e6:.2f} MB) and {archive} ({archive.stat().st_size / 1e6:.2f} MB)\nsha256 {digest}')


if __name__ == '__main__':
    main()
