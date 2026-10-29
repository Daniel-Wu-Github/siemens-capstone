#!/usr/bin/env python3
"""Run the report's test tables by section and report pass/fail counts.

    python run_tests.py                 every section, plus a combined total
    python run_tests.py unit            one section (unit, integration, system, validation)
    python run_tests.py unit system     several sections
    python run_tests.py --no-report     counts only, no HTML report or screenshot

Writes outputs/test-report.html and outputs/test-report.png (both git-ignored).
"""
import argparse
from datetime import datetime
from html import escape
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
OUTPUT_DIR = ROOT / 'outputs'

SECTIONS = {
    'unit': ('Table A7.1 · Unit tests', 'tests/unit', 'UT-01 to UT-12'),
    'integration': ('Table A7.2 · Integration tests', 'tests/integration', 'IT-01 to IT-05'),
    'system': ('Table A7.3 · System tests', 'tests/system', 'ST-01 to ST-07'),
    'validation': ('Table A7.4 · Validation tests', 'tests/validation', 'VT-01 to VT-04'),
}

PASS, FAIL, SKIP = 'passed', 'failed', 'skipped'
TEST_ID = re.compile(r'test_([A-Z]{2})(\d{2})_')
METRIC_LINE = re.compile(r'^(?:UT|IT|ST|VT)-\d{2}\b.*$', re.MULTILINE)


def run_section(key):
    """Run one section in its own pytest process and return parsed results."""
    title, path, span = SECTIONS[key]
    with tempfile.TemporaryDirectory() as work:
        report = Path(work) / 'results.xml'
        started = time.perf_counter()
        process = subprocess.run(
            [PYTHON, '-m', 'pytest', path, '-q', f'--junitxml={report}',
             '-o', 'junit_logging=system-out', '-o', 'junit_family=xunit2'],
            cwd=ROOT, capture_output=True, text=True)
        elapsed = time.perf_counter() - started
        tests = parse_report(report) if report.exists() else []
    return dict(key=key, title=title, span=span, tests=tests, seconds=elapsed,
                returncode=process.returncode,
                stderr=process.stderr.strip(), stdout=process.stdout.strip())


def parse_report(path):
    """Flatten the JUnit XML into one row per test, newest schema or old."""
    root = ET.parse(path).getroot()
    tests = []
    for case in root.iter('testcase'):
        status, message = PASS, ''
        for child in case:
            if child.tag in ('failure', 'error'):
                status = FAIL
                # The message attribute is the assertion text; child.text is the
                # full traceback with local-variable dumps, which is noise here.
                message = child.get('message') or child.text or ''
            elif child.tag == 'skipped':
                status = SKIP
                message = child.get('message') or child.get('type') or ''
        output = ''.join(c.text or '' for c in case if c.tag == 'system-out')
        name = case.get('name', '')
        match = TEST_ID.match(name)
        tests.append(dict(
            id=f'{match.group(1)}-{match.group(2)}' if match else name,
            name=readable(name), raw=name, status=status,
            seconds=float(case.get('time', 0) or 0),
            message=message.strip(),
            metrics=[m.strip() for m in METRIC_LINE.findall(output)]))
    tests.sort(key=lambda t: t['id'])
    return tests


def readable(name):
    name = TEST_ID.sub('', name) or name
    return name.replace('test_', '').replace('_', ' ').strip().capitalize()


def tally(tests):
    return {s: sum(t['status'] == s for t in tests) for s in (PASS, FAIL, SKIP)}


BOLD, DIM, GREEN, RED, YELLOW, RESET = '\033[1m', '\033[2m', '\033[32m', '\033[31m', '\033[33m', '\033[0m'
MARK = {PASS: f'{GREEN}PASS{RESET}', FAIL: f'{RED}FAIL{RESET}', SKIP: f'{YELLOW}SKIP{RESET}'}


def print_section(result):
    counts = tally(result['tests'])
    print(f"\n{BOLD}{result['title']}{RESET}  {DIM}({result['span']}){RESET}")
    print('─' * 78)
    if not result['tests']:
        print(f"  {RED}no tests ran{RESET} — {result['stderr'][:400] or result['stdout'][-400:]}")
        return counts
    for test in result['tests']:
        print(f"  {MARK[test['status']]}  {BOLD}{test['id']:<6}{RESET} {test['name'][:52]:<52}"
              f" {DIM}{test['seconds']:6.2f}s{RESET}")
        for metric in test['metrics']:
            print(f"         {DIM}{metric[:200]}{RESET}")
        if test['status'] == FAIL:
            first = next((line for line in test['message'].splitlines() if line.strip()), '')
            print(f"         {RED}{first[:200]}{RESET}")
        if test['status'] == SKIP and test['message']:
            print(f"         {YELLOW}{test['message'][:200]}{RESET}")
    print('─' * 78)
    print(f"  {counts[PASS]} passed · {counts[FAIL]} failed · {counts[SKIP]} skipped"
          f"   {DIM}({result['seconds']:.1f}s){RESET}")
    return counts


def print_total(results):
    print(f"\n{BOLD}{'=' * 78}{RESET}")
    print(f"{BOLD}  TOTAL ACROSS ALL SECTIONS{RESET}")
    print('=' * 78)
    totals = {PASS: 0, FAIL: 0, SKIP: 0}
    print(f"  {'Section':<34}{'Pass':>6}{'Fail':>6}{'Skip':>6}{'Total':>7}{'Time':>9}")
    print('  ' + '─' * 68)
    for result in results:
        counts = tally(result['tests'])
        for key in totals:
            totals[key] += counts[key]
        print(f"  {result['title'][:34]:<34}{counts[PASS]:>6}{counts[FAIL]:>6}"
              f"{counts[SKIP]:>6}{len(result['tests']):>7}{result['seconds']:>8.1f}s")
    print('  ' + '─' * 68)
    count = sum(totals.values())
    print(f"  {BOLD}{'All tests':<34}{totals[PASS]:>6}{totals[FAIL]:>6}{totals[SKIP]:>6}{count:>7}{RESET}")
    rate = totals[PASS] / count * 100 if count else 0
    colour = GREEN if not totals[FAIL] else YELLOW
    print(f"\n  {BOLD}{colour}{totals[PASS]} of {count} tests passing ({rate:.0f}%){RESET}")
    if totals[FAIL]:
        print(f"  {RED}{totals[FAIL]} failing:{RESET}")
        for result in results:
            for test in result['tests']:
                if test['status'] == FAIL:
                    print(f"    · {test['id']} {test['name']}")
    return totals


CSS = """
:root{--ink:#14333b;--muted:#5d6f75;--bg:#f4f7f8;--line:#dfe8e9;--teal:#087f73;
--red:#c94943;--amber:#a96c12;--white:#fff;--pass:#0b7a5e;--pass-bg:#e7f6f1;
--fail-bg:#fdeceb;--skip-bg:#fdf4e3;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.page{max-width:1080px;margin:0 auto;padding:40px 32px 56px}
header{border-bottom:3px solid var(--teal);padding-bottom:20px;margin-bottom:28px}
h1{margin:0 0 6px;font-size:26px;letter-spacing:-.2px}
.sub{color:var(--muted);font-size:13px;margin:0}
.totals{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0 34px}
.tile{background:var(--white);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.tile .label{font-size:10px;letter-spacing:.7px;color:var(--muted);font-weight:650;text-transform:uppercase}
.tile .value{font-size:30px;font-weight:680;margin-top:6px;line-height:1}
.tile .value.good{color:var(--pass)}.tile .value.bad{color:var(--red)}
.tile .value.warn{color:var(--amber)}
.tile small{color:var(--muted);font-size:11px;display:block;margin-top:4px}
section{background:var(--white);border:1px solid var(--line);border-radius:10px;
margin-bottom:22px;overflow:hidden}
.head{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
padding:15px 20px;border-bottom:1px solid var(--line);background:#fbfcfd}
.head h2{margin:0;font-size:15px}
.head .span{color:var(--muted);font-size:11px}
.head .count{font-size:12px;font-weight:650;color:var(--muted)}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:9px 20px;background:#f8fafb;color:#64747b;font-size:10px;
letter-spacing:.5px;font-weight:650;border-bottom:1px solid var(--line);text-transform:uppercase}
td{padding:11px 20px;border-bottom:1px solid #eef3f4;vertical-align:top;font-size:13px}
tr:last-child td{border-bottom:none}
.id{font-weight:680;white-space:nowrap;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;
font-weight:700;letter-spacing:.4px}
.badge.passed{background:var(--pass-bg);color:var(--pass)}
.badge.failed{background:var(--fail-bg);color:var(--red)}
.badge.skipped{background:var(--skip-bg);color:var(--amber)}
.secs{color:var(--muted);font-size:11px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.metric{color:var(--muted);font-size:11.5px;margin-top:5px;line-height:1.45}
.why{background:var(--fail-bg);border-left:3px solid var(--red);padding:8px 11px;
border-radius:0 5px 5px 0;margin-top:7px;font-size:11.5px;white-space:pre-wrap;
color:#7d2f2b;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;line-height:1.5}
.note{background:var(--white);border:1px solid var(--line);border-left:3px solid var(--amber);
border-radius:0 10px 10px 0;padding:16px 20px;margin-bottom:22px}
.note h3{margin:0 0 8px;font-size:13px}
.note p{margin:0 0 8px;font-size:12.5px;color:var(--muted)}
.note p:last-child{margin-bottom:0}
footer{color:var(--muted);font-size:11px;margin-top:28px;text-align:center}
"""

CAVEATS = [
    ('VT-01 cannot reach its 25% target with this extract',
     'Replenishment volume in the supplied data falls from 17,602 events in May-July to '
     '5,284 in August-September, and two of the twenty parts stop moving entirely. A rate '
     'fitted on the training window therefore over-predicts the test window. The model does '
     'beat the moving-average baseline, which is the half of the target the model controls.'),
    ('VT-02 and VT-03 replay replenishments, not daily snapshots',
     'The extract contains one undated bin snapshot, so there are no daily stock snapshots to '
     'replay. Each confirmed replenishment is treated as a low-stock event instead. '
     'Replenishment dates carry one day of resolution, so the report\'s 3-hour lead time '
     'cannot be resolved; the figures below are at the warning window set in the config file '
     'and every test prints the full sweep from same-day to seven days.'),
    ('VT-04 shows the sponsor table is not a top-2 ranking',
     'Every part Christian listed does appear in our replenishment data, but the counts differ '
     'by an order of magnitude and the listed parts rank between 4th and 769th in our extract. '
     'The table is measuring a different period or a different definition of a replenishment. '
     'This is a question to put back to the sponsor, not a defect in the pipeline.'),
]


def build_html(results, totals, path):
    count = sum(totals.values())
    rate = totals[PASS] / count * 100 if count else 0
    stamp = datetime.now().astimezone().strftime('%d %B %Y, %H:%M %Z')
    parts = [f'<title>Capstone Test Results</title><style>{CSS}</style>', '<div class="page">',
             '<header><h1>Proactive Material Replenishment — Test Results</h1>',
             f'<p class="sub">Texas A&amp;M CSCE 482 capstone · Siemens Digitalization and Data · '
             f'{escape(stamp)} · Python {platform.python_version()} on {platform.system()}</p></header>']

    tiles = [('Tests passing', f'{totals[PASS]} / {count}', 'good' if not totals[FAIL] else 'warn',
              f'{rate:.0f}% of all tests'),
             ('Failing', str(totals[FAIL]), 'bad' if totals[FAIL] else 'good',
              'validation targets' if totals[FAIL] else 'none'),
             ('Skipped', str(totals[SKIP]), 'warn' if totals[SKIP] else 'good',
              'unmet prerequisites' if totals[SKIP] else 'none'),
             ('Sections', str(len(results)), '', 'unit, integration, system, validation')]
    parts.append('<div class="totals">')
    for label, value, tone, note in tiles:
        parts.append(f'<div class="tile"><div class="label">{escape(label)}</div>'
                     f'<div class="value {tone}">{escape(value)}</div>'
                     f'<small>{escape(note)}</small></div>')
    parts.append('</div>')

    for result in results:
        counts = tally(result['tests'])
        parts.append(
            f'<section><div class="head"><div><h2>{escape(result["title"])}</h2>'
            f'<div class="span">{escape(result["span"])} · {result["seconds"]:.1f}s</div></div>'
            f'<div class="count">{counts[PASS]} passed · {counts[FAIL]} failed · '
            f'{counts[SKIP]} skipped</div></div>'
            '<table><thead><tr><th style="width:68px">ID</th><th>Test</th>'
            '<th style="width:74px">Result</th><th style="width:62px">Time</th></tr></thead><tbody>')
        for test in result['tests']:
            detail = ''.join(f'<div class="metric">{escape(m)}</div>' for m in test['metrics'])
            if test['status'] == FAIL:
                detail += f'<div class="why">{escape(trim(test["message"]))}</div>'
            elif test['status'] == SKIP and test['message']:
                detail += f'<div class="metric">Skipped: {escape(test["message"][:300])}</div>'
            parts.append(
                f'<tr><td class="id">{escape(test["id"])}</td>'
                f'<td>{escape(test["name"])}{detail}</td>'
                f'<td><span class="badge {test["status"]}">{test["status"].upper()}</span></td>'
                f'<td class="secs">{test["seconds"]:.2f}s</td></tr>')
        parts.append('</tbody></table></section>')

    if totals[FAIL]:
        parts.append('<div class="note"><h3>What the failing tests tell us</h3>')
        parts.extend(f'<p><b>{escape(t)}.</b> {escape(b)}</p>' for t, b in CAVEATS)
        parts.append('</div>')

    parts.append('<footer>Generated by run_tests.py. Siemens data stays in the git-ignored '
                 'data/ folder and never appears in this report.</footer></div>')
    path.write_text('\n'.join(parts))
    return path


LOCALS_DUMP = re.compile(r'^\s*(?:[a-z_]+ = |assert |\+ *where |@|def |\"\"\")')


def trim(message, limit=2000):
    """Keep the assertion text, drop pytest's echoed source and local variables."""
    lines = []
    for line in message.splitlines():
        if not line.strip():
            continue
        if LOCALS_DUMP.match(line):
            break
        lines.append(re.sub(r'^E\s{0,4}', '', line))
    text = '\n'.join(lines).strip()
    return text[:limit] + ('…' if len(text) > limit else '')


def screenshot(html_path, png_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, 'playwright is not installed (pip install -r requirements-dev.txt)'
    try:
        with sync_playwright() as runner:
            browser = runner.chromium.launch()
            page = browser.new_page(viewport={'width': 1180, 'height': 1400},
                                    device_scale_factor=2)
            page.goto(html_path.resolve().as_uri(), wait_until='networkidle')
            page.screenshot(path=str(png_path), full_page=True)
            browser.close()
        return png_path, None
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # choices= is not used here: argparse validates an empty default against it.
    parser.add_argument('sections', nargs='*', default=[], metavar='SECTION',
                        help=f"sections to run, any of: {', '.join(SECTIONS)} (default: all)")
    parser.add_argument('--no-report', action='store_true', help='skip the HTML report and screenshot')
    args = parser.parse_args()

    unknown = [s for s in args.sections if s not in SECTIONS]
    if unknown:
        parser.error(f"unknown section(s): {', '.join(unknown)}. Choose from: {', '.join(SECTIONS)}")
    chosen = args.sections or list(SECTIONS)
    print(f"\n{BOLD}Running {len(chosen)} section(s): {', '.join(chosen)}{RESET}")

    results = [run_section(key) for key in chosen]
    for result in results:
        print_section(result)
    totals = print_total(results)

    if not args.no_report:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        html = build_html(results, totals, OUTPUT_DIR / 'test-report.html')
        print(f'\n  Report:     {html.relative_to(ROOT)}')
        png, error = screenshot(html, OUTPUT_DIR / 'test-report.png')
        if png:
            print(f'  Screenshot: {png.relative_to(ROOT)}')
        else:
            print(f'  {YELLOW}Screenshot skipped: {error}{RESET}')

    print()
    return 1 if totals[FAIL] else 0


if __name__ == '__main__':
    sys.exit(main())
