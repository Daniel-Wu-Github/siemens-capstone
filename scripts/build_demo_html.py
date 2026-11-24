"""Build the single-file demo: the real index.html + an in-browser stand-in for the API + invented data.

    python scripts/build_demo_html.py [--out outputs/Siemens_Replenishment_Demo.html]

No Siemens data is used. The result is one static HTML file with no network access and no setup.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
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


def build(out, data=None):
    data = data or export_data.export()[0]
    html = (ROOT / 'index.html').read_text()
    engine = (DEMO / 'engine.js').read_text().replace('/*__DEMO_DATA__*/null', json.dumps(data, separators=(',', ':')))
    css = (DEMO / 'demo.css').read_text()
    html = replace_once(html, "<script>\n'use strict';", f"<script>\n{engine}\n</script>\n<script>\n'use strict';")
    html = replace_once(html, "</style>", css + "</style>")
    html = replace_once(html, "<body>\n", "<body>\n<div class=\"demo-ribbon\" role=\"note\">PRACTICE COPY: invented parts, nothing is saved or sent. Refresh the page to start over.</div>\n")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=ROOT / 'outputs' / 'Siemens_Replenishment_Demo.html')
    args = parser.parse_args()
    data = export_data.export()[0]
    size = build(args.out, data)
    archive = args.out.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(args.out, args.out.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(f'{digest}  {archive.name}\n')
    print(f'Wrote {args.out} ({size / 1e6:.2f} MB) and {archive} ({archive.stat().st_size / 1e6:.2f} MB)\nsha256 {digest}')


if __name__ == '__main__':
    main()
