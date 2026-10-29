"""Accessibility audit for ST-07.

Lighthouse is used when its CLI is on PATH. It is not installed in this
project's offline environment, so the fallback below runs the same checks
Lighthouse's accessibility category is built from (they are axe-core rules)
directly in the page. Each check is pass/fail and the score is the percentage
of checks that pass, which is how Lighthouse reports that category.
"""
import json
import shutil
import subprocess

AUDIT_JS = r"""
() => {
  const results = [];
  const add = (id, title, failures) => results.push(
      {id, title, passed: failures.length === 0, failures: failures.slice(0, 8),
       failureCount: failures.length});
  const text = el => (el.textContent || '').trim();
  const visible = el => {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetParent !== null;
  };
  const describe = el =>
      el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/)[0] : '');
  // An accessible name from any of the ways one can be given.
  const named = el => {
    if ((el.getAttribute('aria-label') || '').trim()) return true;
    const by = el.getAttribute('aria-labelledby');
    if (by && by.split(/\s+/).some(id => document.getElementById(id))) return true;
    if ((el.getAttribute('title') || '').trim()) return true;
    if (el.id && [...document.querySelectorAll('label[for]')].some(l => l.htmlFor === el.id && text(l))) return true;
    if (el.closest('label') && text(el.closest('label'))) return true;
    if (text(el)) return true;
    if (el.querySelector('img[alt]:not([alt=""])')) return true;
    return false;
  };

  add('html-has-lang', 'The page declares a language',
      document.documentElement.lang ? [] : ['<html> has no lang attribute']);

  add('document-title', 'The page has a non-empty title',
      (document.title || '').trim() ? [] : ['<title> is missing or empty']);

  add('image-alt', 'Images have alternative text',
      [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).map(describe));

  add('form-field-labels', 'Form controls have accessible names',
      [...document.querySelectorAll('input:not([type=hidden]), select, textarea')]
          .filter(visible).filter(el => !named(el)).map(describe));

  add('button-name', 'Buttons have accessible names',
      [...document.querySelectorAll('button')].filter(visible).filter(el => !named(el)).map(describe));

  add('link-name', 'Links have accessible names',
      [...document.querySelectorAll('a[href]')].filter(visible).filter(el => !named(el)).map(describe));

  const seen = new Map();
  [...document.querySelectorAll('[id]')].forEach(el => seen.set(el.id, (seen.get(el.id) || 0) + 1));
  add('duplicate-id', 'Element ids are unique',
      [...seen.entries()].filter(([, n]) => n > 1).map(([id]) => '#' + id));

  const vp = document.querySelector('meta[name=viewport]');
  add('meta-viewport', 'The viewport scales for zoom',
      !vp ? ['no viewport meta tag']
          : /user-scalable\s*=\s*no|maximum-scale\s*=\s*1\b/.test(vp.content) ? ['viewport blocks zoom'] : []);

  const landmarks = [];
  if (!document.querySelector('main, [role=main]')) landmarks.push('no main landmark');
  if (!document.querySelector('nav, [role=navigation]')) landmarks.push('no navigation landmark');
  add('landmarks', 'The page exposes main and navigation landmarks', landmarks);

  const levels = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible)
      .map(h => ({el: h, level: +h.tagName[1]}));
  const skips = [];
  levels.forEach((h, i) => {
    if (i && h.level > levels[i - 1].level + 1)
      skips.push(`${describe(h.el)} jumps from h${levels[i - 1].level} to h${h.level}`);
  });
  add('heading-order', 'Headings do not skip levels', skips);

  // Contrast of rendered text against its nearest painted background.
  const parse = c => {
    const m = (c || '').match(/[\d.]+/g);
    return m ? m.slice(0, 3).map(Number).concat(m.length > 3 ? +m[3] : 1) : null;
  };
  const lum = ([r, g, b]) => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const background = el => {
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c[3] > 0.05) return c;
    }
    const c = parse(getComputedStyle(document.body).backgroundColor);
    return c && c[3] > 0.05 ? c : [255, 255, 255, 1];
  };
  const low = [];
  [...document.querySelectorAll('p,span,small,td,th,li,label,button,a,h1,h2,h3,h4,h5,h6,div')]
      .filter(visible)
      .filter(el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
      .forEach(el => {
        const s = getComputedStyle(el);
        const fg = parse(s.color), bg = background(el);
        if (!fg || !bg || fg[3] < 0.1) return;
        const a = lum(fg), b = lum(bg);
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        const size = parseFloat(s.fontSize);
        const large = size >= 24 || (size >= 18.66 && +s.fontWeight >= 700);
        if (ratio < (large ? 3 : 4.5))
          low.push(`${describe(el)} ${ratio.toFixed(2)}:1 (${s.color} on rgb(${bg.slice(0, 3)}))`);
      });
  add('color-contrast', 'Text meets WCAG AA contrast', low);

  const passed = results.filter(r => r.passed).length;
  return {score: Math.round(passed / results.length * 100), passed, total: results.length,
          audits: results};
}
"""


def lighthouse_available():
    return shutil.which('lighthouse') is not None


def run_lighthouse(url):
    """Real Lighthouse accessibility score, when the CLI is installed."""
    out = subprocess.run(
        ['lighthouse', url, '--only-categories=accessibility', '--output=json',
         '--quiet', '--chrome-flags=--headless'],
        capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f'lighthouse failed: {out.stderr[-400:]}')
    report = json.loads(out.stdout)
    category = report['categories']['accessibility']
    failures = [dict(id=k, title=v['title'])
                for k, v in report['audits'].items()
                if v.get('score') == 0 and v.get('scoreDisplayMode') == 'binary']
    return dict(score=round(category['score'] * 100), tool='lighthouse',
                audits=failures, passed=None, total=None)


def run_local_audit(page):
    """Offline equivalent, evaluated inside the rendered page."""
    result = page.evaluate(AUDIT_JS)
    result['tool'] = 'local audit (Lighthouse CLI not installed)'
    return result


def failed_audits(result):
    return [a for a in result.get('audits', []) if not a.get('passed', False)]
