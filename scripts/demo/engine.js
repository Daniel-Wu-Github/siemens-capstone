/* In-browser stand-in for the Python backend, used only by the single-file demo.
   It re-implements the locked formulas (proactive.py), alert syncing (store.py / alerts.py) and the insights flags
   (insights_parts.py) on the embedded mock data. Everything lives in memory: a refresh restores the starting data. */
(function () {
'use strict';
const DATA = /*__DEMO_DATA__*/null;
const NAMES = ['history', 'schedule', 'observed'];
const RANGES = {lead_hours_stocked: [0, 720], lead_hours_fab: [0, 720], safety_hours: [0, 168], watch_hours: [0, 720], divergence_pct: [0, 1000]};
const WEIGHT_RANGE = [0, 10];
const THRESHOLD_RANGES = {refill_size_pct: [0, 100], refills_30d: [0, 100], divergence_pct: [0, 1000], min_refills: [1, 100]};
const DEFAULT_THRESHOLDS = {refill_size_pct: 50, refills_30d: 8, divergence_pct: 50, min_refills: 3};
const NO_RATE_REASONS = ['not_in_bom', 'shared_bom', 'nothing_planned', 'zero_rate', 'unweighted'];
const clone = x => JSON.parse(JSON.stringify(x));
const nowIso = () => new Date().toISOString();
class HttpError extends Error { constructor(status, message) { super(message); this.status = status; } }
const bad = message => new HttpError(400, message);

function num(value, name, lo, hi) {
  if (typeof value === 'boolean' || value === null || value === undefined || value === '') throw bad(`${name} must be a number.`);
  const n = Number(value);
  if (!Number.isFinite(n)) throw bad(Number.isNaN(n) ? `${name} must be a number.` : `${name} must be a finite number.`);
  if (n < lo || n > hi) throw bad(`${name} must be between ${lo} and ${hi}.`);
  return n;
}
function validateSettings(current, changes) {
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) throw bad('Settings must be an object.');
  const out = clone(current);
  for (const [key, value] of Object.entries(changes)) {
    if (key === 'weights') {
      if (!value || typeof value !== 'object') throw bad('weights must be an object.');
      for (const [name, w] of Object.entries(value)) {
        if (!NAMES.includes(name)) throw bad(`Unknown weight: ${name}.`);
        out.weights[name] = num(w, `${name} weight`, ...WEIGHT_RANGE);
      }
    } else if (key in RANGES) out[key] = num(value, key, ...RANGES[key]);
    else throw bad(`Unknown setting: ${key}.`);
  }
  return out;
}
function validateThresholds(changes) {
  const out = {...DEFAULT_THRESHOLDS};
  for (const [key, value] of Object.entries(changes || {})) {
    if (!(key in THRESHOLD_RANGES)) throw bad(`Unknown threshold: ${key}.`);
    const [lo, hi] = THRESHOLD_RANGES[key], n = typeof value === 'boolean' || value === '' || value === null ? NaN : Number(value);
    if (!Number.isFinite(n) || n < lo || n > hi) throw bad(`${key} must be a number between ${lo} and ${hi}.`);
    out[key] = n;
  }
  return out;
}

/* ---- locked formulas (docs/formulas.md) ---- */
const rateOk = r => typeof r === 'number' && Number.isFinite(r) && r >= 0;
function blendRate(history, schedule, observed, weights, divergence) {
  const rates = {history, schedule, observed}, live = {};
  for (const n of NAMES) if ((weights[n] || 0) > 0 && rateOk(rates[n])) live[n] = rates[n];
  const dropped = [];
  if ('history' in live && 'schedule' in live && live.history > 0 && Math.abs(live.schedule - live.history) / live.history * 100 > divergence) { delete live.history; dropped.push('history'); }
  const used = NAMES.filter(n => n in live), total = used.reduce((a, n) => a + weights[n], 0);
  return {rate: used.length ? used.reduce((a, n) => a + weights[n] * live[n], 0) / total : null, used, dropped};
}
function noRateReason(inputs, bomStatus) {
  if (rateOk(inputs.history)) return 'zero_rate';
  if (bomStatus === 'not_in_bom') return 'not_in_bom';
  if (bomStatus === 'shared') return 'shared_bom';
  if (bomStatus === 'ok') return 'nothing_planned';
  return rateOk(inputs.observed) ? 'unweighted' : 'not_in_bom';
}
const round2 = x => Math.round((x + Number.EPSILON) * 100) / 100;
function project(items, settings, leadClasses) {
  const weights = settings.weights, safety = settings.safety_hours;
  for (const p of items) {
    let leadClass = leadClasses[p.key] || 'stocked';
    if (leadClass !== 'stocked' && leadClass !== 'fab') leadClass = 'stocked';
    const lead = Number(settings[leadClass === 'fab' ? 'lead_hours_fab' : 'lead_hours_stocked']);
    const inputs = {...p.demand_inputs};
    const blend = blendRate(inputs.history, inputs.schedule, inputs.observed, weights, settings.divergence_pct);
    const rate = blend.rate;
    Object.assign(p, {lead_class: leadClass, lead_hours: lead, demand_per_day: rate, demand_basis: blend.used, demand_dropped: blend.dropped,
      hours_to_min: null, suggested_qty: null, forecast: null});
    delete p.no_rate_reason;
    if (p.status === 'No tolerance' || p.status === 'No stock') continue;
    if (p.status === 'Alert') { p.forecast = 'below_min'; p.hours_to_min = 0; }
    else if (!rate) { p.forecast = 'no_rate'; p.no_rate_reason = noRateReason(inputs, p.bom_status); continue; }
    else {
      const hours = Math.max(0, p.available - p.min) / rate * 24;
      p.hours_to_min = round2(hours);
      if (hours <= lead + safety) { p.forecast = 'within_lead'; p.status = 'Predicted'; }
      else if (hours <= settings.watch_hours) p.forecast = 'watch';
      else p.forecast = 'ok';
    }
    if (p.max !== null && p.max > 0 && (rate !== null || p.forecast === 'below_min'))
      p.suggested_qty = Math.max(0, Math.ceil(p.max - (p.available - (rate || 0) * lead / 24)));
    if (p.forecast === 'ok') p.suggested_qty = null;
  }
  return items;
}

/* ---- state ---- */
const base = clone(DATA.stock.positions);
const DEFAULT_SETTINGS = clone(DATA.stock.forecast.settings);
let settings, overrides, leadClasses, alerts, notifications, activity, presets, seq;
function reset() {
  settings = clone(DEFAULT_SETTINGS); overrides = {}; leadClasses = {}; alerts = []; notifications = []; presets = clone(DATA.presets); seq = 0;
  activity = clone(DATA.sources.activity);
  const t = nowIso(); sync(positions());  // the starting workspace already has its alerts open
  for (const a of alerts) a.created_at = t;
}
function evaluated() {
  return base.map(b => {
    const p = clone(b), available = p.key in overrides ? overrides[p.key] : p.source_available;
    p.available = available; p.overridden = p.key in overrides;
    p.status = p.min === null ? 'No tolerance' : available === null ? 'No stock' : available <= p.min ? 'Alert' : 'OK';
    return p;
  });
}
function positions(s = settings) { return project(evaluated(), s, leadClasses); }
const SNAP = ['material', 'description', 'supermarket', 'storage_type', 'bin', 'unit', 'available', 'min', 'max', 'note', 'overridden', 'hours_to_min', 'lead_hours', 'suggested_qty', 'demand_per_day'];
const RESOLUTIONS = {OK: 'recovered', 'No tolerance': 'minimum no longer set', 'No stock': 'no stock reading'};
function sync(items) {
  const open = new Map(alerts.filter(a => a.status !== 'Resolved').map(a => [a.position, a]));
  const created = [], resolved = [];
  for (const p of items) {
    const row = open.get(p.key); open.delete(p.key);
    const snap = Object.fromEntries(SNAP.map(k => [k, p[k] === undefined ? null : p[k]]));
    if (p.status === 'Alert' || p.status === 'Predicted') {
      const kind = p.status === 'Alert' ? 'below_min' : 'predicted';
      if (row) {
        const old = row.kind || 'below_min'; Object.assign(row, snap, {kind, updated_at: nowIso()});
        if (old === 'predicted' && kind === 'below_min') { row.status = 'Open'; row.escalated_at = nowIso(); created.push({...row, escalated: true}); }
      } else { const a = {...snap, id: 'a' + (++seq), position: p.key, status: 'Open', kind, created_at: nowIso()}; alerts.push(a); created.push(a); }
    } else if (row) {
      Object.assign(row, snap, {status: 'Resolved', resolved_at: nowIso(), resolution: row.kind === 'predicted' && p.status === 'OK' ? 'forecast cleared' : RESOLUTIONS[p.status]});
      resolved.push({...row});
    }
  }
  return {created, resolved};
}
const openAlerts = () => alerts.filter(a => a.status !== 'Resolved').map(a => clone(a));
function event(message) { activity.unshift({created_at: nowIso(), message}); activity.length = Math.min(activity.length, 12); }

/* ---- notifications (shown in the page; nothing is sent) ---- */
const fmtN = n => n === null || n === undefined ? '—' : (Number.isInteger(n) ? String(n) : String(+Number(n).toPrecision(6)));
function subject(p) {
  if (p.new.length) {
    const f = p.new[0], extra = p.new.length > 1 ? ` (+${p.new.length - 1} more)` : '';
    return f.kind === 'predicted' ? `[Stock alert] ${f.material} at ${f.supermarket} ${f.bin} predicted to reach minimum in ${fmtN(f.hours_to_min)} h${extra}`
      : `[Stock alert] ${f.material} at ${f.supermarket} ${f.bin} is at or below minimum${extra}`;
  }
  if (p.resolved.length) return `[Stock alert] ${p.resolved.length} alert(s) resolved`;
  return `[Stock alert] Digest: ${p.open} open alert(s)`;
}
const forecastText = a => a.kind === 'predicted' ? ` · reaches min in ${fmtN(a.hours_to_min)} h · suggest ${fmtN(a.suggested_qty)}` : '';
const rowsText = items => items.map(a => `- ${a.material} (${a.description}) · ${a.supermarket} ${a.bin} · available ${fmtN(a.available)} / min ${fmtN(a.min)}${forecastText(a)}${a.note ? ' · ' + a.note : ''}`);
function notify(created, resolved, digest) {
  const open = openAlerts(), p = {new: created, resolved, open: open.length};
  const text = ['Rule: available <= min · snapshot as of unknown', `Open alerts: ${open.length}`, ''];
  for (const [title, items] of [['New alerts', created], ['Resolved', resolved], ['All open alerts', digest ? open : []]]) if (items.length) text.push(title + ':', ...rowsText(items), '');
  text.push('Demo build: this message is shown here and not sent. Stock edits are simulations; nothing is saved when the page is refreshed.');
  const record = {id: 'n' + (++seq), created_at: nowIso(), subject: subject(p), digest: !!digest, text: text.join('\n'),
    summary: {open: open.length, new: created.length, resolved: resolved.length},
    results: [{channel: 'email', status: 'saved', detail: 'Demo: shown on this page, not sent'}]};
  notifications.unshift(record); notifications.length = Math.min(notifications.length, 5);
  event(`Stock alert notification: ${record.subject} (email saved).`);
  return record;
}
const announce = ch => ch && (ch.created.length || ch.resolved.length) ? notify(ch.created, ch.resolved) : null;

/* ---- views ---- */
function stockView() {
  const items = positions(), counts = {predicted: 0, watch: 0, below_min: 0, no_rate: 0, ok: 0};
  for (const p of items) { const k = p.forecast === 'within_lead' ? 'predicted' : p.forecast; if (k in counts) counts[k]++; }
  return {dataset_id: DATA.dataset_id, snapshot_as_of: null, tolerances_loaded: true, positions: items,
    summary: {positions: items.length, alerts: items.filter(p => p.status === 'Alert').length, ok: items.filter(p => p.status === 'OK').length,
      no_tolerance: items.filter(p => p.status === 'No tolerance').length, overridden: items.filter(p => p.overridden).length},
    forecast: {settings: clone(settings), summary: counts}, open_alerts: openAlerts(), notifications: clone(notifications),
    channels: clone(DATA.stock.channels), notification: null};
}
function median(a) { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }
function partsReport(th) {
  const parts = clone(DATA.parts.parts).map(p => {
    const pcts = p.refills.map(r => r.pct_of_max).filter(v => v !== null), flags = [];
    if (p.median_refill_pct !== null && pcts.length >= th.min_refills && p.median_refill_pct < th.refill_size_pct) flags.push('short_refills');
    if (p.refills.length > th.refills_30d) flags.push('frequent_refills');
    if (p.divergence_pct !== null && p.divergence_pct > th.divergence_pct) flags.push('divergent');
    if (p.refills.length === 0) flags.push('no_refill');
    else if (p.refills.length === 1) flags.push('slow_refill');
    return Object.assign(p, {flags});
  });
  const summary = {...DATA.parts.summary, flagged: parts.filter(p => p.flags.length).length};
  for (const f of ['short_refills', 'frequent_refills', 'divergent', 'slow_refill', 'no_refill']) summary[f] = parts.filter(p => p.flags.includes(f)).length;
  return {thresholds: th, summary, notes: clone(DATA.parts.notes), parts, dataset_id: DATA.dataset_id,
    window_days: DATA.parts.window_days, window_start: DATA.parts.window_start, window_end: DATA.parts.window_end};
}
const CSV_FIELDS = ['key', 'material', 'description', 'supermarket', 'storage_type', 'bin', 'min', 'max', 'refills_30d', 'median_refill_pct', 'share_refills_95',
  'history_per_day', 'schedule_per_day', 'divergence_pct', 'days_under_min', 'days_under_min_status', 'flags'];
function csvCell(v) {
  if (v === null || v === undefined) return '';
  let s = Array.isArray(v) ? v.join(';') : String(v);
  if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function partsCsv(th) {
  const rep = partsReport(th);
  return CSV_FIELDS.join(',') + '\r\n' + rep.parts.map(p => CSV_FIELDS.map(k => csvCell(p[k])).join(',')).join('\r\n') + '\r\n';
}
function thresholdsFrom(q) {
  const map = {refill_pct: 'refill_size_pct', refills: 'refills_30d', divergence: 'divergence_pct', min_refills: 'min_refills'}, ch = {};
  for (const [k, v] of Object.entries(map)) if (q.has(k)) ch[v] = q.get(k);
  return validateThresholds(ch);
}
function tierCounts(items) {
  const c = {predicted: 0, watch: 0, below_min: 0, no_rate: 0, ok: 0};
  for (const p of items) { const k = p.forecast === 'within_lead' ? 'predicted' : p.forecast; if (k in c) c[k]++; }
  return c;
}
function preview(changes) {
  const candidate = validateSettings(settings, changes), before = positions(settings), after = positions(candidate);
  const old = Object.fromEntries(before.map(p => [p.key, p.forecast]));
  const changed = after.filter(p => old[p.key] !== p.forecast).map(p => ({key: p.key, material: p.material, bin: p.bin, from: old[p.key], to: p.forecast, hours_to_min: p.hours_to_min}));
  changed.sort((a, b) => (a.hours_to_min === null) - (b.hours_to_min === null) || (a.hours_to_min || 0) - (b.hours_to_min || 0) || (a.key < b.key ? -1 : 1));
  return {current: {settings: clone(settings), summary: tierCounts(before)}, candidate: {settings: candidate, summary: tierCounts(after)}, changed: changed.slice(0, 50)};
}
function preName(name) {
  if (typeof name !== 'string' || !(name.trim().length >= 1 && name.trim().length <= 40)) throw bad('Preset name must contain 1–40 characters.');
  return name.trim();
}

/* ---- API ---- */
function handleGet(url) {
  const q = url.searchParams;
  switch (url.pathname) {
    case '/api/health': return {status: 'ok', storage: 'memory', mode: 'demo'};
    case '/api/datasets': return clone(DATA.datasets);
    case '/api/sources': { const s = clone(DATA.sources); s.activity = clone(activity); return s; }
    case '/api/stock': return stockView();
    case '/api/stock/presets': return clone(presets).sort((a, b) => a.name < b.name ? -1 : 1);
    case '/api/insights/parts': return partsReport(thresholdsFrom(q));
    case '/api/insights/parts.csv': return {csv: partsCsv(thresholdsFrom(q))};
    default: throw new HttpError(404, 'Not found.');
  }
}
function handlePost(url, body) {
  if (url.pathname === '/api/import') throw bad('Importing files is turned off in this demo. The invented data is already loaded.');
  const b = body && typeof body === 'object' ? body : {};
  switch (url.pathname) {
    case '/api/stock/available': {
      const before = sync(positions()), pos = b.position;
      if (!base.some(p => p.key === pos)) throw bad('Stock position not found.');
      if (b.available === null || b.available === undefined) delete overrides[pos]; else overrides[pos] = num(b.available, 'Available stock', 0, 1e6);
      const after = sync(positions());
      event(`Simulated available stock ${b.available == null ? 'reset' : 'set to ' + fmtN(Number(b.available))} for ${pos.split('|')[0]}.`);
      announce(before);
      return {new: after.created.length, resolved: after.resolved.length, notification: after.created.length || after.resolved.length ? notify(after.created, after.resolved) : null};
    }
    case '/api/stock/settings': {
      const next = validateSettings(settings, Object.fromEntries(Object.entries(b).filter(([k]) => k !== 'dataset')));
      announce(sync(positions())); settings = next; event('Forecast settings saved.');
      const ch = sync(positions()), n = announce(ch);
      return {settings: clone(settings), new: ch.created.length, resolved: ch.resolved.length, notification: n};
    }
    case '/api/stock/preview': return preview(Object.fromEntries(Object.entries(b).filter(([k]) => k !== 'dataset')));
    case '/api/stock/presets': {
      const name = preName(b.name), s = validateSettings(settings, b.settings || {}), rec = {name, settings: s, created_at: nowIso()};
      presets = presets.filter(p => p.name !== name).concat([rec]); return clone(rec);
    }
    case '/api/stock/presets/delete': { const name = preName(b.name); presets = presets.filter(p => p.name !== name); return {ok: true}; }
    case '/api/stock/lead-class': {
      if (!['stocked', 'fab', null].includes(b.lead_class === undefined ? null : b.lead_class)) throw bad('Lead class must be stocked or fab.');
      if (!base.some(p => p.key === b.position)) throw bad('Stock position not found.');
      announce(sync(positions()));
      if (!b.lead_class) delete leadClasses[b.position]; else leadClasses[b.position] = b.lead_class;
      event(`Lead class ${b.lead_class || 'reset'} for ${b.position.split('|')[0]}.`);
      const ch = sync(positions()), n = announce(ch);
      return {new: ch.created.length, resolved: ch.resolved.length, notification: n};
    }
    case '/api/stock/check': { const ch = sync(positions()); return {new: ch.created.length, resolved: ch.resolved.length, notification: announce(ch), checked_at: nowIso()}; }
    case '/api/stock/reset': {
      announce(sync(positions())); overrides = {}; const ch = sync(positions());
      event('Simulated stock edits cleared; source snapshot values restored.');
      return {new: ch.created.length, resolved: ch.resolved.length, notification: announce(ch)};
    }
    case '/api/stock/digest': { announce(sync(positions())); return {notification: notify([], [], true)}; }
    case '/api/stock/ack-all': {
      let n = 0;
      for (const a of alerts) if (a.status === 'Open' && (a.kind || 'below_min') === 'below_min') { a.status = 'Acknowledged'; a.acknowledged_at = nowIso(); n++; }
      if (n) event(`${n} below-minimum stock alert(s) acknowledged together.`);
      return {acknowledged: n};
    }
    case '/api/stock/ack': {
      const a = alerts.find(x => x.id === b.id);
      if (!a || a.status !== 'Open') throw bad('Only an open alert can be acknowledged.');
      a.status = 'Acknowledged'; a.acknowledged_at = nowIso(); event(`Stock alert acknowledged for ${a.material} / ${a.bin}.`);
      return {ok: true};
    }
    default: throw new HttpError(404, 'Not found.');
  }
}
function respond(status, data) {
  return new Response(JSON.stringify(data), {status, headers: {'Content-Type': 'application/json'}});
}
reset();
const realFetch = window.fetch ? window.fetch.bind(window) : null;
window.fetch = async function (input, init) {
  const url = new URL(typeof input === 'string' ? input : input.url, 'http://demo.local');
  if (!url.pathname.startsWith('/api/')) return realFetch(input, init);
  try {
    const post = init && init.method === 'POST';
    let body; if (post && typeof init.body === 'string') body = JSON.parse(init.body);
    return respond(200, post ? handlePost(url, body) : handleGet(url));
  } catch (e) {
    if (e instanceof HttpError) return respond(e.status, {error: e.message});
    return respond(500, {error: 'The demo hit an unexpected error: ' + e.message});
  }
};
window.DEMO = {reset, csv: q => partsCsv(thresholdsFrom(new URLSearchParams(q))), state: () => ({overrides: clone(overrides), settings: clone(settings)})};
})();
