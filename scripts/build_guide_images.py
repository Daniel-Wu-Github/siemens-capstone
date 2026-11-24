"""Build the annotated screenshots used by docs/USER_GUIDE.md and docs/INSTALL_GUIDE.md.

    python scripts/build_guide_images.py [--out docs/images]

Runs the real backend on the invented demo workbook (no Siemens data) in a temporary database, opens each page in
headless Chromium, draws numbered circles and arrows over the page and saves a PNG. Needs requirements-dev.txt and Chromium.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'scripts' / 'demo')]
import export_data  # noqa: E402
from qc_helpers import make_store, serving  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

COLOUR = '#e6007e'
OVERLAY = """(items) => {
  const old = document.getElementById('guide-overlay'); if (old) old.remove();
  const ns = 'http://www.w3.org/2000/svg', C = '%s';
  const svg = document.createElementNS(ns, 'svg'); svg.id = 'guide-overlay';
  svg.setAttribute('style', 'position:fixed;left:0;top:0;width:100vw;height:100vh;z-index:99999;pointer-events:none');
  const mk = (t, a) => { const e = document.createElementNS(ns, t); for (const k in a) e.setAttribute(k, a[k]); svg.appendChild(e); return e; };
  const defs = mk('defs', {}); const m = document.createElementNS(ns, 'marker');
  m.setAttribute('id', 'ah'); m.setAttribute('markerWidth', '10'); m.setAttribute('markerHeight', '8'); m.setAttribute('refX', '9'); m.setAttribute('refY', '4'); m.setAttribute('orient', 'auto');
  const pa = document.createElementNS(ns, 'path'); pa.setAttribute('d', 'M0,0 L10,4 L0,8 z'); pa.setAttribute('fill', C); m.appendChild(pa); defs.appendChild(m);
  for (const it of items) {
    const [x, y, w, h] = it.box, pad = it.pad ?? 6, n = it.n;
    let bx, by;                       // badge centre
    if (it.arrow) {                   // badge sits away from the target and an arrow points at it
      const d = 46, cx = x + w / 2, cy = y + h / 2;
      const off = {l: [-w / 2 - d, 0], r: [w / 2 + d, 0], u: [0, -h / 2 - d], d: [0, h / 2 + d]}[it.arrow];
      bx = cx + off[0]; by = cy + off[1];
      const tx = it.arrow === 'l' ? x - 3 : it.arrow === 'r' ? x + w + 3 : cx, ty = it.arrow === 'u' ? y - 3 : it.arrow === 'd' ? y + h + 3 : cy;
      const ux = tx - bx, uy = ty - by, len = Math.hypot(ux, uy) || 1;
      mk('line', {x1: bx + ux / len * 15, y1: by + uy / len * 15, x2: tx, y2: ty, stroke: C, 'stroke-width': 3, 'marker-end': 'url(#ah)'});
    } else {
      if (it.round) mk('ellipse', {cx: x + w / 2, cy: y + h / 2, rx: w / 2 + pad + 4, ry: h / 2 + pad + 4, fill: 'none', stroke: C, 'stroke-width': 3});
      else mk('rect', {x: x - pad, y: y - pad, width: w + 2 * pad, height: h + 2 * pad, rx: 6, fill: 'none', stroke: C, 'stroke-width': 3});
      bx = x - pad; by = y - pad;
      if (it.badge) { bx = x + (it.badge[0] < 0 ? w + it.badge[0] : it.badge[0]); by = y + (it.badge[1] < 0 ? h + it.badge[1] : it.badge[1]); }
    }
    mk('circle', {cx: bx, cy: by, r: 13, fill: C, stroke: '#fff', 'stroke-width': 2});
    const t = mk('text', {x: bx, y: by + 5, 'text-anchor': 'middle', style: 'fill:#fff;font:700 15px Helvetica,Arial,sans-serif'}); t.textContent = n;
  }
  (document.querySelector('dialog[open]') || document.body).appendChild(svg);   // a modal dialog sits above everything else
}""" % COLOUR


def box(locs):
    """Union bounding box of one or more locators, in viewport pixels."""
    locs = locs if isinstance(locs, (list, tuple)) else [locs]
    bs = [l.first.bounding_box() for l in locs]
    assert all(bs), 'element not visible for annotation'
    x0, y0 = min(b['x'] for b in bs), min(b['y'] for b in bs)
    x1, y1 = max(b['x'] + b['width'] for b in bs), max(b['y'] + b['height'] for b in bs)
    return [x0, y0, x1 - x0, y1 - y0]


def shot(page, path, notes, clip=None):
    page.evaluate("window.scrollTo(0, 0); document.querySelector('main')?.scrollTo(0, 0)")
    page.wait_for_timeout(150)
    items = [dict(n=n, box=box(loc), **opts) for n, loc, opts in notes]
    page.evaluate(OVERLAY, items)
    page.screenshot(path=str(path), clip=clip) if clip else page.screenshot(path=str(path))
    page.evaluate("document.getElementById('guide-overlay')?.remove()")
    print('wrote', path.name)


def age_alerts(store):
    """Back-date the open alerts so the Open since column shows a realistic spread."""
    now = datetime.now(timezone.utc)
    with store.connection() as db:
        for i, (rid, payload) in enumerate(db.execute('SELECT id, payload FROM stock_alerts').fetchall()):
            p = json.loads(payload)
            stamp = (now - timedelta(hours=i * 9 + 2.5, minutes=i * 7)).isoformat()
            p['created_at'] = stamp
            if p.get('escalated_at'): p['escalated_at'] = stamp
            db.execute('UPDATE stock_alerts SET payload=? WHERE id=?', (json.dumps(p), rid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'images'))
    out = Path(ap.parse_args().out); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    store, ds = export_data.build_store(tmp)
    store.stock_view(ds); age_alerts(store)
    empty = make_store(tmp, 'empty.sqlite3')
    with sync_playwright() as p, serving(store) as base, serving(empty) as empty_base:
        b = p.chromium.launch(); page = b.new_page(viewport={'width': 1440, 'height': 1000})
        go = lambda name: (page.goto(f'{base}/#{name}'), page.reload(), page.wait_for_timeout(1200))
        L = page.locator

        # 1 Stock alerts
        go('stock')
        first = L('tr.alert-row').first
        shot(page, out / 'stock-alerts.png', [
            (1, L('aside a', has_text='Stock alerts'), dict(arrow='r')),
            (2, first.locator('td').first, dict(pad=2, badge=(-4, 6))),
            (3, first.locator('.tag').first, dict()),
            (4, L('thead th', has_text='Open since'), dict(pad=2)),
            (5, L('thead th', has_text='Last replenished'), dict(pad=2)),
            (6, first.locator('input').first, dict()),
            (7, first.get_by_role('button', name='Acknowledge'), dict()),
            (8, [L('button', has_text='Run check'), L('button', has_text='Acknowledge all below-minimum')], dict()),
            (9, [L('input[type=search], input[placeholder^="Search"]'), L('select').first], dict()),
            (10, L('.panel', has_text='Delivery').first, dict(pad=2)),
            (11, L('button', has_text='Enlarge'), dict()),
        ], clip=dict(x=0, y=0, width=1440, height=760))

        # 2 Digest dialog
        L('button', has_text='Enlarge').click(); page.wait_for_timeout(500)
        shot(page, out / 'digest-dialog.png', [
            (1, L('#digest-dialog h2'), dict(pad=2)),
            (2, L('#digest-close'), dict(arrow='l')),
        ], clip=dict(x=250, y=300, width=940, height=400))
        page.keyboard.press('Escape')

        # 3 Predicted alerts, a bin that will reach its minimum
        go('predicted')
        L('#pred-rows tr[data-pred]', has_text='Predicted').nth(5).click(); page.wait_for_timeout(500)
        svg = L('#pred-chart svg')
        dots = svg.locator('circle')
        shot(page, out / 'predicted-chart.png', [
            (1, L('button[data-window="45"]').locator('xpath=..'), dict(pad=3)),
            (2, dots.nth(dots.count() - 1), dict(arrow='r', pad=0)),
            (3, dots.nth(0), dict(arrow='u', pad=0)),
            (4, dots.nth(1), dict(arrow='u', pad=0)) if dots.count() > 2 else (4, L('#pred-chart .chart-legend'), dict()),
            (5, L('#pred-chart .chart-legend'), dict(pad=0)),
            (6, L('#pred-chart .pred-info'), dict(pad=0, badge=(-14, 4))),
            (7, L('#pred-chart').locator('xpath=following::section[1]'), dict(pad=2)),
            (8, L('thead th.hrs'), dict(pad=2)),
        ], clip=dict(x=0, y=0, width=1440, height=1000))

        # 4 Predicted alerts, a bin already at or below minimum
        L('#pred-rows tr[data-pred]', has_text='Alert').first.click(); page.wait_for_timeout(400)
        shot(page, out / 'predicted-alert-tier.png', [
            (1, L('#pred-chart .alert-act b'), dict()),
            (2, L('#go-stock'), dict(arrow='r')),
        ], clip=dict(x=200, y=250, width=1240, height=360))

        # 5 Replenishment insights
        go('insights')
        shot(page, out / 'insights.png', [
            (1, L('button.chip').first.locator('xpath=..'), dict(pad=4)),
            (2, L('button[data-window="45"]').locator('xpath=..'), dict(pad=3)),
            (3, L('input').first.locator('xpath=../..'), dict(pad=2)),
            (4, L('table').first, dict(pad=0, badge=(8, 8))),
            (5, L('text=Export CSV'), dict(arrow='d')),
        ], clip=dict(x=0, y=0, width=1440, height=1000))

        # 6 Data sources
        go('data')
        shot(page, out / 'data-sources.png', [
            (1, L('input[type=file]').nth(0), dict(pad=3)),
            (2, L('input[type=file]').nth(1), dict(pad=3)),
            (3, L('input[type=date]'), dict(pad=3)),
            (4, L('.panel', has_text='Source inventory').first, dict(pad=2)),
        ], clip=dict(x=0, y=0, width=1440, height=820))

        # 7 Settings
        go('settings')
        shot(page, out / 'settings.png', [
            (1, [L('input').nth(0), L('input').nth(2)], dict(pad=4)),
            (2, L('input').nth(4), dict(pad=3)),
            (3, L('button', has_text='Save forecast settings'), dict()),
            (4, L('.panel', has_text='Try weights').first, dict(pad=2)),
            (5, L('.panel', has_text='Formulas in use').first, dict(pad=2)),
        ], clip=dict(x=0, y=0, width=1440, height=1000))

        # 8 Feedback form
        go('stock'); L('#open-feedback').click(); page.wait_for_timeout(500)
        shot(page, out / 'feedback.png', [
            (1, L('#open-feedback'), dict(arrow='r')),
            (2, L('#feedback-overlay'), dict(pad=-2, badge=(16, 16))),
            (3, L('#fb-submit'), dict(arrow='l')),
        ], clip=dict(x=0, y=0, width=1440, height=1000))

        # 9 First start: empty database
        page.goto(f'{empty_base}/'); page.wait_for_timeout(1500)
        shot(page, out / 'first-start.png', [(1, L('button, a', has_text='Go to Data sources').first, dict(arrow='r'))],
             clip=dict(x=0, y=0, width=1440, height=420))
        b.close()


if __name__ == '__main__':
    main()
