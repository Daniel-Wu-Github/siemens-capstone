"""Phase 1 reactive stock alerts and platform-agnostic notifiers.

The rule only compares available stock with the sponsor's minimum tolerance; it
needs no consumption rate. Every channel receives the same neutral payload, so
email, Teams, or any webhook consumer can be added without touching the rule.
"""
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape
import json
import math
import os
from pathlib import Path
import re
import smtplib
import ssl
import urllib.request
import uuid

from .analytics import supermarket

RULES = {
    'at_or_below': ('available <= min', lambda available, minimum: available <= minimum),
    'below': ('available < min', lambda available, minimum: available < minimum),
}


def label(value):
    """Text key; integral floats from Excel ('100200.0') match their text form ('100200')."""
    if value is None: return ''
    if isinstance(value, float) and value.is_integer(): return str(int(value))
    s = str(value).strip()
    return s.split('.')[0] if re.fullmatch(r'\d+\.0+', s) else s


def position_key(material, storage_type, bin_):
    return '|'.join(label(v) for v in (material, storage_type, bin_))


def count(value):
    if value is None or value == '' or isinstance(value, bool): return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def positions(stock_rows, tolerance_rows, overrides=None):
    """Join stock rows with tolerances on material + bin (+ storage type when both have one)."""
    overrides = overrides or {}
    tolerances = {}
    for t in tolerance_rows or []:
        tolerances.setdefault((label(t.get('Part')), label(t.get('Location'))), []).append(t)
    groups = {}
    for row in stock_rows:
        material, bin_ = label(row.get('Material')), label(row.get('Storage Bin'))
        if not material or not bin_: continue
        key = position_key(material, label(row.get('Storage Type')), bin_)
        segments = groups.setdefault(key, [])
        if row not in segments: segments.append(row)  # identical duplicate rows count once
    out = []
    for key, segments in groups.items():
        # Several storage units in one bin are summed into one position, so each bin raises at most one alert.
        row = segments[0]
        material, bin_, storage_type = label(row.get('Material')), label(row.get('Storage Bin')), label(row.get('Storage Type'))
        matches = [t for t in tolerances.get((material, bin_), [])
                   if not storage_type or not label(t.get('Type')) or label(t.get('Type')) == storage_type]
        tol = matches[0] if len(matches) == 1 else None
        values = [count(r.get('Available stock')) for r in segments]
        source = None if None in values else sum(values)
        available = overrides.get(key, source)
        minimum, maximum = (count(tol.get('Min')), count(tol.get('Max'))) if tol else (None, None)
        notes = [f'{len(segments)} storage units summed'] if len(segments) > 1 else []
        if tol is None: notes.append('no tolerance' if not matches else 'ambiguous tolerance')
        elif minimum is not None and maximum is not None and maximum > 0 and minimum > maximum: notes.append('check tolerance (min > max)')
        elif minimum is not None and maximum == 0 and minimum > 0: notes.append('max not set')
        out.append(dict(key=key, material=material, description=label(row.get('Material Description')),
                        storage_type=storage_type, supermarket=supermarket(storage_type), bin=bin_,
                        unit=label(row.get('Base Unit of Measure')), source_available=source,
                        available=available, overridden=key in overrides, min=minimum, max=maximum,
                        note='; '.join(notes) or None))
    return out


def evaluate(items, rule='at_or_below'):
    """Return each position with status Alert, OK, No tolerance, or No stock."""
    if rule not in RULES: raise ValueError('Unknown alert rule.')
    test = RULES[rule][1]
    for p in items:
        if p['min'] is None: p['status'] = 'No tolerance'
        elif p['available'] is None: p['status'] = 'No stock'
        else: p['status'] = 'Alert' if test(p['available'], p['min']) else 'OK'
    return items


def payload(dataset_id, rule, snapshot_as_of, new, resolved, open_alerts, dashboard_url=None):
    by_supermarket = {}
    for a in open_alerts: by_supermarket[a['supermarket']] = by_supermarket.get(a['supermarket'], 0) + 1
    return dict(run_id=uuid.uuid4().hex, created_at=datetime.now(timezone.utc).isoformat(), dataset=dataset_id,
                rule=RULES[rule][0], snapshot_as_of=snapshot_as_of,
                summary=dict(open=len(open_alerts), new=len(new), resolved=len(resolved), by_supermarket=by_supermarket),
                new=new, resolved=resolved, dashboard_url=dashboard_url)


RULE_WORDS = {'available <= min': 'at or below minimum', 'available < min': 'below minimum'}


def _oneline(value):
    return ' '.join(str(value).split())


def _subject(p):
    if p['new']:
        first = p['new'][0]
        extra = f" (+{len(p['new'])-1} more)" if len(p['new']) > 1 else ''
        return _oneline(f"[Stock alert] {first['material']} at {first['supermarket']} {first['bin']} is {RULE_WORDS.get(p['rule'], 'at or below minimum')}{extra}")
    if p['resolved']: return f"[Stock alert] {len(p['resolved'])} alert(s) resolved"
    return f"[Stock alert] Digest: {p['summary']['open']} open alert(s)"


def _rows(items):
    return [f"- {a['material']} ({a['description']}) · {a['supermarket']} {a['bin']} · available {fmt(a['available'])} / min {fmt(a['min'])}"
            + (f" · {a['note']}" if a.get('note') else '') for a in items]


def fmt(n):
    return '—' if n is None else (str(int(n)) if float(n).is_integer() else f'{n:g}')


def email_message(p, sender, recipients, digest=None):
    msg = EmailMessage()
    msg['Subject'] = _subject(p); msg['From'] = sender; msg['To'] = ', '.join(recipients)
    sections = [('New alerts', p['new']), ('Resolved', p['resolved'])] + ([('All open alerts', digest)] if digest else [])
    text = [f"Rule: {p['rule']} · snapshot as of {p['snapshot_as_of'] or 'unknown'}", f"Open alerts: {p['summary']['open']}", '']
    html = [f"<p>Rule: <b>{escape(p['rule'])}</b> · snapshot as of {escape(p['snapshot_as_of'] or 'unknown')}<br>Open alerts: <b>{p['summary']['open']}</b></p>"]
    for title, items in sections:
        if not items: continue
        text += [f'{title}:'] + _rows(items) + ['']
        html.append(f'<h3>{escape(title)}</h3><table border="1" cellpadding="4" cellspacing="0"><tr><th>Material</th><th>Description</th><th>Supermarket</th><th>Bin</th><th>Available</th><th>Min</th><th>Note</th></tr>'
                    + ''.join(f"<tr><td>{escape(a['material'])}</td><td>{escape(a['description'])}</td><td>{escape(a['supermarket'])}</td><td>{escape(a['bin'])}</td><td>{fmt(a['available'])}</td><td>{fmt(a['min'])}</td><td>{escape(a.get('note') or '')}</td></tr>" for a in items) + '</table>')
    if p.get('dashboard_url'):
        text.append(f"Dashboard: {p['dashboard_url']}"); html.append(f"<p><a href=\"{escape(p['dashboard_url'])}\">Open dashboard</a></p>")
    text.append('Generated by the capstone prototype. Stock edits made in the demo are simulations; source files are unchanged.')
    msg.set_content('\n'.join(text)); msg.add_alternative('<html><body>' + ''.join(html) + '</body></html>', subtype='html')
    return msg


TEAMS_ROWS = 15


def teams_card(p, digest=None):
    """Adaptive Card wrapped for a Teams Workflows ("When a Teams webhook request is received") trigger."""
    body = [dict(type='TextBlock', size='Medium', weight='Bolder', text=_subject(p), wrap=True),
            dict(type='TextBlock', text=f"Open: {p['summary']['open']} · new: {p['summary']['new']} · resolved: {p['summary']['resolved']}", wrap=True)]
    for title, items in (('New alerts', p['new']), ('Resolved', p['resolved']), ('All open alerts', digest or [])):
        if not items: continue
        body.append(dict(type='TextBlock', text=title, weight='Bolder', wrap=True, spacing='Medium'))
        body.append(dict(type='FactSet', facts=[dict(title=a['material'], value=f"{a['supermarket']} {a['bin']} · {fmt(a['available'])} / min {fmt(a['min'])}") for a in items[:TEAMS_ROWS]]))
        if len(items) > TEAMS_ROWS: body.append(dict(type='TextBlock', text=f'+{len(items) - TEAMS_ROWS} more', isSubtle=True, wrap=True))
    card = {'$schema': 'http://adaptivecards.io/schemas/adaptive-card.json', 'type': 'AdaptiveCard', 'version': '1.4', 'body': body}
    if p.get('dashboard_url'): card['actions'] = [dict(type='Action.OpenUrl', title='Open dashboard', url=p['dashboard_url'])]
    return dict(type='message', attachments=[dict(contentType='application/vnd.microsoft.card.adaptive', contentUrl=None, content=card)])


def _post(url, body, timeout=10):
    if not url.lower().startswith(('https://', 'http://')): raise ValueError('Webhook URL must use http(s).')
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if not 200 <= response.status < 300: raise OSError(f'HTTP {response.status}')


def config(env=None):
    env = os.environ if env is None else env
    if env.get('ALERT_RULE', 'at_or_below') not in RULES: raise ValueError(f"ALERT_RULE must be one of: {', '.join(RULES)}.")
    recipients = [r.strip() for r in env.get('ALERT_EMAIL_TO', '').split(',') if r.strip()]
    return dict(rule=env.get('ALERT_RULE', 'at_or_below'), smtp_host=env.get('SMTP_HOST', ''), smtp_port=int(env.get('SMTP_PORT', '587') or 587),
                smtp_user=env.get('SMTP_USER', ''), smtp_password=env.get('SMTP_PASSWORD', ''), smtp_starttls=env.get('SMTP_STARTTLS', '1') != '0',
                smtp_ssl=env.get('SMTP_SSL', '') == '1' or env.get('SMTP_PORT', '') == '465',
                email_from=env.get('ALERT_EMAIL_FROM', '') or env.get('SMTP_USER', '') or 'stock-alerts@localhost', email_to=recipients,
                teams_url=env.get('TEAMS_WEBHOOK_URL', ''), webhook_url=env.get('ALERT_WEBHOOK_URL', ''),
                outbox=env.get('ALERT_OUTBOX', ''), dashboard_url=env.get('ALERT_DASHBOARD_URL', ''))


def public_config(cfg):
    """Channel status for the UI; never includes passwords or webhook URLs."""
    return dict(rule=cfg['rule'], rule_text=RULES.get(cfg['rule'], ('unknown',))[0],
                email=dict(configured=bool(cfg['smtp_host'] and cfg['email_to']), recipients=cfg['email_to']),
                teams=dict(configured=bool(cfg['teams_url'])), webhook=dict(configured=bool(cfg['webhook_url'])))


def _detail(exc, cfg):
    """Exception text for the UI and log, with every configured secret removed."""
    text = f'{type(exc).__name__}: {exc}'[:300]
    for secret in (cfg.get('smtp_password'), cfg.get('teams_url'), cfg.get('webhook_url')):
        if secret: text = text.replace(secret, '***')
    return text


def _smtp_send(cfg, msg, smtp_factory, smtp_ssl_factory):
    context = ssl.create_default_context()  # verifies the server certificate and host name
    if cfg.get('smtp_ssl'):
        smtp = smtp_ssl_factory(cfg['smtp_host'], cfg['smtp_port'], timeout=15, context=context)
    else:
        smtp = smtp_factory(cfg['smtp_host'], cfg['smtp_port'], timeout=15)
    with smtp:
        if not cfg.get('smtp_ssl'):
            if cfg['smtp_starttls']: smtp.starttls(context=context)
            elif cfg['smtp_user']: raise ValueError('Refusing to send the SMTP password without TLS; set SMTP_STARTTLS=1 or SMTP_SSL=1.')
        if cfg['smtp_user']: smtp.login(cfg['smtp_user'], cfg['smtp_password'])
        smtp.send_message(msg)


def deliver(p, cfg, outbox_dir, digest=None, smtp_factory=smtplib.SMTP, poster=_post, smtp_ssl_factory=smtplib.SMTP_SSL):
    """Send one payload to every configured channel. Failures are reported, never raised.

    Email goes through SMTP when configured. The .eml outbox copy is written only when SMTP is
    not configured or the send failed, so a failed email is never lost.
    """
    results = []
    recipients = cfg['email_to'] or ['warehouse@example.invalid']
    msg = email_message(p, cfg['email_from'], recipients, digest)
    smtp_configured = bool(cfg['smtp_host'] and cfg['email_to'])
    email_error = None
    if smtp_configured:
        try:
            _smtp_send(cfg, msg, smtp_factory, smtp_ssl_factory)
            results.append(dict(channel='email', status='sent', detail='Sent to ' + ', '.join(cfg['email_to'])))
        except Exception as exc:
            email_error = _detail(exc, cfg)
    if not smtp_configured or email_error:
        outbox = Path(cfg['outbox'] or outbox_dir)
        try:
            outbox.mkdir(parents=True, exist_ok=True)
            path = outbox / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{p['run_id'][:8]}.eml"
            path.write_bytes(bytes(msg))
            saved = f'saved to outbox as {path.name}'
        except OSError as exc:
            saved = None
            results.append(dict(channel='outbox', status='failed', detail=_detail(exc, cfg)))
        if email_error:
            results.append(dict(channel='email', status='failed', detail=f"{email_error}; " + (saved or 'outbox save also failed')))
        elif saved:
            results.append(dict(channel='email', status='saved', detail=f'SMTP not configured; email {saved}'))
    for channel, url, body in (('teams', cfg['teams_url'], teams_card(p, digest)), ('webhook', cfg['webhook_url'], p)):
        if not url: continue
        try:
            poster(url, body); results.append(dict(channel=channel, status='sent', detail='Posted'))
        except Exception as exc:
            results.append(dict(channel=channel, status='failed', detail=_detail(exc, cfg)))
    return dict(subject=msg['Subject'], to=recipients, text=msg.get_body(('plain',)).get_content(), results=results)
