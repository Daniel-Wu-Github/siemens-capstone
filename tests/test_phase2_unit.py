"""Phase 2 pure-logic tests (proactive.py + alerts payload). Expected values come from the Phase 2 contract."""
import copy
import json
import math

import pytest

from siemens_capstone import alerts, proactive

NAN, INF = float('nan'), float('inf')
W = dict(history=1.0, schedule=1.0, observed=1.0)


# ---------------------------------------------------------------- default_settings
def test_default_settings_defaults_are_3_3_0():
    s = proactive.default_settings({})
    assert (s['lead_hours_stocked'], s['lead_hours_fab'], s['safety_hours']) == (3.0, 3.0, 0.0)
    assert s['weights'] == W and s['divergence_pct'] == 50.0
    assert s == proactive.DEFAULT_SETTINGS


def test_default_settings_env_overrides():
    s = proactive.default_settings({'LEAD_HOURS_STOCKED': '4.5', 'LEAD_HOURS_FAB': '12', 'ALERT_SAFETY_HOURS': '1.25'})
    assert (s['lead_hours_stocked'], s['lead_hours_fab'], s['safety_hours']) == (4.5, 12.0, 1.25)
    only = proactive.default_settings({'LEAD_HOURS_FAB': '8'})
    assert (only['lead_hours_stocked'], only['lead_hours_fab'], only['safety_hours']) == (3.0, 8.0, 0.0)


def test_default_settings_reads_os_environ_when_env_is_none(monkeypatch):
    monkeypatch.setenv('LEAD_HOURS_STOCKED', '6')
    monkeypatch.delenv('LEAD_HOURS_FAB', raising=False)
    monkeypatch.delenv('ALERT_SAFETY_HOURS', raising=False)
    assert proactive.default_settings()['lead_hours_stocked'] == 6.0


@pytest.mark.parametrize('name', ['LEAD_HOURS_STOCKED', 'LEAD_HOURS_FAB', 'ALERT_SAFETY_HOURS'])
def test_default_settings_invalid_env_raises(name):
    with pytest.raises(ValueError): proactive.default_settings({name: 'abc'})


def test_default_settings_returns_fresh_deep_copy():
    a = proactive.default_settings({})
    a['weights']['history'] = 9; a['lead_hours_stocked'] = 99
    b = proactive.default_settings({})
    assert b['weights']['history'] == 1.0 and b['lead_hours_stocked'] == 3.0
    assert proactive.DEFAULT_SETTINGS['weights']['history'] == 1.0 and b is not a and b['weights'] is not a['weights']


# ---------------------------------------------------------------- validate_settings
def base():
    return proactive.default_settings({})


def test_validate_merges_without_mutating_current_or_aliasing_it():
    cur = base(); snapshot = copy.deepcopy(cur)
    out = proactive.validate_settings(cur, {'lead_hours_stocked': 5, 'safety_hours': 2})
    assert cur == snapshot
    assert out['lead_hours_stocked'] == 5.0 and out['safety_hours'] == 2.0 and out['lead_hours_fab'] == 3.0
    out['weights']['history'] = 7
    assert cur['weights']['history'] == 1.0
    assert isinstance(out['lead_hours_stocked'], float)


def test_validate_partial_weights_change_only_named_weights():
    cur = base()
    out = proactive.validate_settings(cur, {'weights': {'history': 0}})
    assert out['weights'] == dict(history=0.0, schedule=1.0, observed=1.0)
    out2 = proactive.validate_settings(out, {'weights': {'schedule': 2.5, 'observed': 10}})
    assert out2['weights'] == dict(history=0.0, schedule=2.5, observed=10.0)
    assert cur['weights'] == W


def test_validate_empty_changes_returns_equal_copy():
    cur = base()
    out = proactive.validate_settings(cur, {})
    assert out == cur and out is not cur


@pytest.mark.parametrize('key,lo,hi', [('lead_hours_stocked', 0, 720), ('lead_hours_fab', 0, 720),
                                       ('safety_hours', 0, 168), ('divergence_pct', 0, 1000)])
def test_validate_scalar_ranges_are_inclusive(key, lo, hi):
    assert proactive.validate_settings(base(), {key: lo})[key] == float(lo)
    assert proactive.validate_settings(base(), {key: hi})[key] == float(hi)
    for bad in (lo - 0.001, hi + 0.001):
        with pytest.raises(ValueError): proactive.validate_settings(base(), {key: bad})


@pytest.mark.parametrize('name', ['history', 'schedule', 'observed'])
def test_validate_weight_ranges_are_inclusive(name):
    assert proactive.validate_settings(base(), {'weights': {name: 0}})['weights'][name] == 0.0
    assert proactive.validate_settings(base(), {'weights': {name: 10}})['weights'][name] == 10.0
    for bad in (-0.001, 10.001):
        with pytest.raises(ValueError): proactive.validate_settings(base(), {'weights': {name: bad}})


def test_validate_unknown_keys_rejected_and_nothing_applied():
    cur = base(); snapshot = copy.deepcopy(cur)
    with pytest.raises(ValueError): proactive.validate_settings(cur, {'nope': 1})
    with pytest.raises(ValueError): proactive.validate_settings(cur, {'lead_hours_stocked': 5, 'nope': 1})
    with pytest.raises(ValueError): proactive.validate_settings(cur, {'weights': {'nope': 1}})
    assert cur == snapshot


@pytest.mark.parametrize('bad', [True, False, None, NAN, INF, -INF, 'abc', '', 'nan', 'inf', [], {}])
def test_validate_rejects_non_finite_or_non_numeric_values(bad):
    for key in ('lead_hours_stocked', 'lead_hours_fab', 'safety_hours', 'divergence_pct'):
        with pytest.raises(ValueError): proactive.validate_settings(base(), {key: bad})
    with pytest.raises(ValueError): proactive.validate_settings(base(), {'weights': {'history': bad}})


def test_validate_accepts_numeric_strings_as_float():
    out = proactive.validate_settings(base(), {'lead_hours_stocked': '4', 'weights': {'history': '2.5'}})
    assert out['lead_hours_stocked'] == 4.0 and isinstance(out['lead_hours_stocked'], float)
    assert out['weights']['history'] == 2.5


# ---------------------------------------------------------------- blend_rate
def blend(h=None, s=None, o=None, weights=W, div=50.0):
    return proactive.blend_rate(h, s, o, dict(weights), div)


def test_blend_nothing_available():
    assert blend() == dict(rate=None, used=[], dropped=[])


@pytest.mark.parametrize('bad', [-1.0, -0.001, NAN, INF, -INF, None])
def test_blend_unavailable_inputs_are_ignored_not_dropped(bad):
    out = blend(h=bad, s=10.0)
    assert out == dict(rate=10.0, used=['schedule'], dropped=[])
    out = blend(h=10.0, o=bad)
    assert out == dict(rate=10.0, used=['history'], dropped=[])


def test_blend_all_none_negative_combinations():
    vals = [None, -1.0, 4.0]
    for h in vals:
        for s in vals:
            for o in vals:
                out = blend(h, s, o, div=1000.0)
                good = [(n, v) for n, v in (('history', h), ('schedule', s), ('observed', o)) if v == 4.0]
                if good:
                    assert out['rate'] == 4.0 and out['used'] == [n for n, _ in good] and out['dropped'] == []
                else:
                    assert out == dict(rate=None, used=[], dropped=[])


def test_blend_weighted_mean_arithmetic_and_used_order():
    out = blend(10.0, 12.0, 20.0, weights=dict(history=1, schedule=2, observed=3))
    assert out['used'] == ['history', 'schedule', 'observed'] and out['dropped'] == []
    assert out['rate'] == pytest.approx((1 * 10 + 2 * 12 + 3 * 20) / 6)


def test_blend_used_order_is_fixed_regardless_of_availability():
    out = blend(None, 5.0, 7.0)
    assert out['used'] == ['schedule', 'observed'] and out['rate'] == pytest.approx(6.0)


def test_blend_divergence_exactly_at_threshold_keeps_history():
    out = blend(h=10.0, s=15.0, div=50.0)          # |15-10|/10*100 == 50, not > 50
    assert out['used'] == ['history', 'schedule'] and out['dropped'] == [] and out['rate'] == pytest.approx(12.5)


def test_blend_divergence_just_over_threshold_drops_history():
    out = blend(h=10.0, s=15.1, o=20.0, div=50.0)
    assert out['used'] == ['schedule', 'observed'] and out['dropped'] == ['history']
    assert out['rate'] == pytest.approx((15.1 + 20.0) / 2)


def test_blend_divergence_applies_below_and_above_history():
    out = blend(h=100.0, s=10.0)                    # 90 % > 50
    assert out == dict(rate=10.0, used=['schedule'], dropped=['history'])
    out = blend(h=100.0, s=150.0)                   # exactly 50 %
    assert out['dropped'] == [] and out['rate'] == pytest.approx(125.0)


def test_blend_divergence_threshold_is_configurable():
    assert blend(h=10.0, s=11.0, div=5.0)['dropped'] == ['history']
    assert blend(h=10.0, s=11.0, div=10.0)['dropped'] == []
    assert blend(h=10.0, s=10.0, div=0.0)['dropped'] == []        # identical never diverges
    assert blend(h=10.0, s=10.5, div=0.0)['dropped'] == ['history']


def test_blend_history_zero_skips_divergence_check_and_zero_rate_is_valid():
    out = blend(h=0.0, s=100.0)
    assert out['used'] == ['history', 'schedule'] and out['dropped'] == [] and out['rate'] == pytest.approx(50.0)
    out = blend(h=0.0)
    assert out == dict(rate=0.0, used=['history'], dropped=[]) and out['rate'] is not None
    assert blend(o=0.0)['rate'] == 0.0


def test_blend_divergence_ignores_observed_and_needs_both_inputs():
    assert blend(h=10.0, o=500.0)['dropped'] == []
    assert blend(s=10.0, o=500.0)['dropped'] == []


def test_blend_zero_weight_inputs_are_neither_used_nor_dropped():
    out = blend(h=10.0, s=12.0, o=20.0, weights=dict(history=1, schedule=0, observed=1))
    assert out['used'] == ['history', 'observed'] and out['dropped'] == [] and out['rate'] == pytest.approx(15.0)
    out = blend(h=10.0, s=12.0, o=20.0, weights=dict(history=0, schedule=1, observed=1))
    assert out['used'] == ['schedule', 'observed'] and out['dropped'] == []
    assert blend(h=10.0, s=12.0, o=20.0, weights=dict(history=0, schedule=0, observed=0)) == dict(rate=None, used=[], dropped=[])


def test_blend_zero_history_weight_disables_divergence_rule():
    # history weight 0 -> the divergence rule needs "non-zero weights" on both, so nothing is dropped
    out = blend(h=10.0, s=100.0, weights=dict(history=0, schedule=1, observed=1))
    assert out == dict(rate=100.0, used=['schedule'], dropped=[])
    out = blend(h=10.0, s=100.0, weights=dict(history=1, schedule=0, observed=1))
    assert out == dict(rate=10.0, used=['history'], dropped=[])


def test_blend_dropped_history_with_nothing_else_gives_schedule_alone():
    out = blend(h=1.0, s=100.0)
    assert out == dict(rate=100.0, used=['schedule'], dropped=['history'])


# ---------------------------------------------------------------- project
def settings(**kw):
    s = base()
    for k, v in kw.items(): s[k] = v
    return s


def item(status='OK', available=60.0, minimum=10.0, maximum=100.0, material='M1', storage_type='401', bin_='B1', **extra):
    return dict(key=f'{material}|{storage_type}|{bin_}', material=material, storage_type=storage_type, bin=bin_,
                available=available, min=minimum, max=maximum, status=status, **extra)


def inv(material='M1', storage_type='401', bin_='B1', replen=None, planned=None, bom_status='ok', observed=None):
    return dict(material=material, storage_type=storage_type, bin=bin_, replen_qty_per_day=replen,
                planned_qty_next_30d=planned, bom_status=bom_status, observed_depletion_per_day=observed)


NEW_FIELDS = ('lead_class', 'lead_hours', 'demand_per_day', 'demand_basis', 'demand_dropped', 'hours_to_min', 'suggested_qty', 'forecast')


def run(it, inventory=(), st=None, classes=None):
    out = proactive.project([it], list(inventory) if inventory is not None else None, st or base(), classes)
    return out[0]


def test_project_mutates_and_returns_same_list_with_all_fields_on_every_item():
    items = [item(), item(status='No tolerance', minimum=None, material='M2'), item(status='No stock', available=None, material='M3'),
             item(status='Alert', available=5, material='M4')]
    out = proactive.project(items, [], base())
    assert out is items
    for p in out:
        for f in NEW_FIELDS: assert f in p, f
        assert p['lead_class'] == 'stocked' and p['lead_hours'] == 3.0


def test_project_no_tolerance_and_no_stock_have_no_forecast_and_keep_status():
    for status, kw in (('No tolerance', dict(minimum=None)), ('No stock', dict(available=None))):
        p = run(item(status=status, **kw), [inv(replen=30.0)])
        assert p['status'] == status
        assert p['forecast'] is None and p['hours_to_min'] is None and p['suggested_qty'] is None


def test_project_alert_is_below_min_with_zero_hours_and_status_unchanged():
    p = run(item(status='Alert', available=5.0, minimum=10.0, maximum=100.0), [inv(replen=30.0)])
    assert p['status'] == 'Alert' and p['forecast'] == 'below_min' and p['hours_to_min'] == 0
    assert p['suggested_qty'] == math.ceil(100 - (5 - 30 * 3 / 24)) == 99


def test_project_alert_without_rate_uses_rate_zero_for_suggestion():
    p = run(item(status='Alert', available=5.0, maximum=100.0), [])
    assert p['forecast'] == 'below_min' and p['demand_per_day'] is None and p['suggested_qty'] == 95
    p = run(item(status='Alert', available=5.0, maximum=100.0), [inv()])           # row exists, all rates None
    assert p['suggested_qty'] == 95


def test_project_suggested_qty_uses_ceil_and_floors_at_zero():
    p = run(item(status='Alert', available=5.0, maximum=100.0), [inv(replen=10.0)])
    assert p['suggested_qty'] == 97                                                   # 100 - (5 - 1.25) = 96.25 -> 97
    p = run(item(status='Alert', available=120.0, minimum=150.0, maximum=100.0), [inv(replen=10.0)])
    assert p['suggested_qty'] == 0                                                    # negative -> floored
    assert isinstance(p['suggested_qty'], int)


@pytest.mark.parametrize('maximum', [0, 0.0, None])
def test_project_suggested_qty_none_when_max_zero_or_missing(maximum):
    assert run(item(status='Alert', available=5.0, maximum=maximum), [inv(replen=30.0)])['suggested_qty'] is None
    assert run(item(available=13.0, maximum=maximum), [inv(replen=30.0)])['suggested_qty'] is None


def test_project_ok_without_rate_or_with_zero_rate_is_no_rate():
    for rows in ([], None, [inv()], [inv(replen=0.0)], [inv(replen=-5.0)]):
        p = run(item(), rows)
        assert p['status'] == 'OK' and p['forecast'] == 'no_rate' and p['hours_to_min'] is None and p['suggested_qty'] is None
    assert run(item(), [inv(replen=0.0)])['demand_per_day'] == 0.0


def test_project_within_lead_exactly_at_threshold_then_just_over():
    # rate 240/day -> hours = (available - min) / 10
    at = run(item(available=40.0, minimum=10.0), [inv(replen=240.0)])
    assert at['hours_to_min'] == 3.0 and at['forecast'] == 'within_lead' and at['status'] == 'Predicted'
    over = run(item(available=41.0, minimum=10.0), [inv(replen=240.0)])
    assert over['hours_to_min'] == 3.1 and over['forecast'] == 'ok' and over['status'] == 'OK'
    assert over['suggested_qty'] is None


def test_project_safety_hours_added_to_threshold_but_not_to_suggestion_lead():
    inventory = [inv(replen=240.0)]
    it = lambda: item(available=55.0, minimum=10.0, maximum=200.0)                    # 4.5 h
    assert run(it(), inventory)['forecast'] == 'ok'
    p = run(it(), inventory, settings(safety_hours=2.0))
    assert p['forecast'] == 'within_lead' and p['status'] == 'Predicted' and p['lead_hours'] == 3.0
    assert p['suggested_qty'] == math.ceil(200 - (55 - 240 * 3 / 24))                 # lead_hours only
    assert run(it(), inventory, settings(safety_hours=1.5))['forecast'] == 'within_lead'   # 4.5 <= 4.5
    assert run(it(), inventory, settings(safety_hours=1.4))['forecast'] == 'ok'


def test_project_fab_vs_stocked_lead_hours_and_default_class():
    st = settings(lead_hours_stocked=3.0, lead_hours_fab=10.0)
    inventory = [inv(replen=240.0)]
    mk = lambda: item(available=70.0, minimum=10.0, maximum=200.0)                    # 6 h
    stocked = run(mk(), inventory, st)
    assert stocked['lead_class'] == 'stocked' and stocked['lead_hours'] == 3.0 and stocked['forecast'] == 'ok'
    p = mk(); fab = run(p, inventory, st, {p['key']: 'fab'})
    assert fab['lead_class'] == 'fab' and fab['lead_hours'] == 10.0 and fab['forecast'] == 'within_lead'
    assert fab['suggested_qty'] == math.ceil(200 - (70 - 240 * 10 / 24)) == 230
    p = mk(); other = run(p, inventory, st, {'someone|else|x': 'fab'})
    assert other['lead_class'] == 'stocked'
    p = mk(); explicit = run(p, inventory, st, {p['key']: 'stocked'})
    assert explicit['lead_hours'] == 3.0


def test_project_hours_rounded_to_two_decimals():
    p = run(item(available=11.0, minimum=10.0), [inv(replen=7.0)])                    # 24/7 = 3.428571...
    assert p['hours_to_min'] == 3.43


def test_project_suggested_qty_for_within_lead_example():
    p = run(item(available=13.0, minimum=10.0, maximum=100.0), [inv(replen=30.0)])
    assert p['hours_to_min'] == 2.4 and p['forecast'] == 'within_lead'
    assert p['suggested_qty'] == 91                                                   # ceil(100 - (13 - 3.75))
    assert run(item(available=13.0, minimum=10.0, maximum=5.0), [inv(replen=30.0)])['suggested_qty'] == 0


def test_project_join_normalises_floats_strings_and_whitespace():
    it = item(material='100200', storage_type='401', bin_='A1')
    for row in (inv('100200', '401', 'A1', replen=30.0), inv(100200.0, 401.0, 'A1', replen=30.0),
                inv(' 100200 ', '401.0', ' A1 ', replen=30.0), inv('100200.0', 401, 'A1', replen=30.0)):
        p = run(copy.deepcopy(it), [row])
        assert p['demand_per_day'] == 30.0, row
    it_float = item(material=401.0, storage_type=401.0, bin_=7.0)
    assert run(it_float, [inv('401', '401', '7', replen=30.0)])['demand_per_day'] == 30.0


def test_project_join_requires_all_three_fields_to_match():
    for row in (inv('M1', '402', 'B1', replen=30.0), inv('M1', '401', 'B2', replen=30.0), inv('M2', '401', 'B1', replen=30.0)):
        p = run(item(), [row])
        assert p['demand_per_day'] is None and p['forecast'] == 'no_rate'


def test_project_missing_inventory_row_gives_all_none_inputs():
    p = run(item(), [inv('OTHER', replen=30.0)])
    assert p['demand_per_day'] is None and p['demand_basis'] == [] and p['demand_dropped'] == []
    assert p['forecast'] == 'no_rate'


def test_project_bom_status_not_ok_ignores_schedule():
    for status in ('shared', 'not_in_bom', None, 'unknown'):
        p = run(item(), [inv(planned=300.0, bom_status=status)])
        assert p['demand_per_day'] is None and p['forecast'] == 'no_rate', status
    p = run(item(), [inv(planned=300.0, bom_status='ok')])
    assert p['demand_per_day'] == 10.0 and p['demand_basis'] == ['schedule']          # 300 / 30
    p = run(item(), [inv(planned=None, bom_status='ok')])
    assert p['demand_per_day'] is None
    # not-ok schedule is ignored but history still counts, with no 'dropped' entry
    p = run(item(), [inv(replen=5.0, planned=900.0, bom_status='shared')])
    assert p['demand_per_day'] == 5.0 and p['demand_basis'] == ['history'] and p['demand_dropped'] == []


def test_project_basis_and_dropped_follow_blend():
    p = run(item(), [inv(replen=10.0, planned=600.0, observed=5.0)])                   # schedule 20/day: 100 % > 50
    assert p['demand_basis'] == ['schedule', 'observed'] and p['demand_dropped'] == ['history']
    assert p['demand_per_day'] == pytest.approx(12.5)
    p = run(item(), [inv(replen=10.0, planned=300.0, observed=4.0)])                   # schedule 10/day agrees
    assert p['demand_basis'] == ['history', 'schedule', 'observed'] and p['demand_per_day'] == pytest.approx(8.0)
    p = run(item(), [inv(observed=6.0)])
    assert p['demand_basis'] == ['observed'] and p['demand_per_day'] == 6.0


def test_project_respects_weights_in_settings():
    st = settings(weights=dict(history=0.0, schedule=1.0, observed=3.0))
    p = run(item(), [inv(replen=100.0, planned=300.0, observed=2.0)], st)
    assert p['demand_basis'] == ['schedule', 'observed'] and p['demand_dropped'] == []
    assert p['demand_per_day'] == pytest.approx((10.0 + 6.0) / 4)


def test_project_schedule_zero_drops_history_and_yields_no_rate():
    p = run(item(), [inv(replen=10.0, planned=0.0)])
    assert p['demand_dropped'] == ['history'] and p['demand_basis'] == ['schedule']
    assert p['demand_per_day'] == 0.0 and p['forecast'] == 'no_rate' and p['status'] == 'OK'


def test_project_strict_rule_available_equal_min_with_ok_status_becomes_predicted():
    p = run(item(status='OK', available=10.0, minimum=10.0), [inv(replen=30.0)])      # ALERT_RULE=below leaves this OK
    assert p['hours_to_min'] == 0 and p['forecast'] == 'within_lead' and p['status'] == 'Predicted'
    p = run(item(status='OK', available=8.0, minimum=10.0), [inv(replen=30.0)])        # clamped at zero
    assert p['hours_to_min'] == 0 and p['status'] == 'Predicted'


def test_project_zero_lead_still_predicts_when_hours_to_min_is_zero():
    st = settings(lead_hours_stocked=0.0)
    assert run(item(available=10.0, minimum=10.0), [inv(replen=30.0)], st)['status'] == 'Predicted'
    assert run(item(available=11.0, minimum=10.0), [inv(replen=30.0)], st)['status'] == 'OK'


def test_project_empty_or_none_inputs_do_not_crash():
    assert proactive.project([], None, base()) == []
    assert proactive.project([], [], base(), {}) == []
    assert proactive.project([item()], None, base())[0]['forecast'] == 'no_rate'


def test_project_integrates_with_alerts_evaluate_output():
    rows = [dict(Material='M1', **{'Storage Type': '401', 'Storage Bin': 'B1', 'Available stock': 13, 'Base Unit of Measure': 'PC', 'Material Description': 'x'})]
    tol = [dict(Part='M1', Location='B1', Type=401, Min=10, Max=100)]
    items = alerts.evaluate(alerts.positions(rows, tol))
    out = proactive.project(items, [inv('M1', 401.0, 'B1', replen=30.0)], base())
    assert out[0]['status'] == 'Predicted' and out[0]['hours_to_min'] == 2.4 and out[0]['suggested_qty'] == 91


# ---------------------------------------------------------------- alerts.payload / messages
def alert(kind='predicted', material='PRED-1', **kw):
    d = dict(material=material, description='Synthetic part', supermarket='Silver', bin='B1', available=13, min=10, max=100,
             note=None, kind=kind, hours_to_min=2.4 if kind == 'predicted' else 0, lead_hours=3.0,
             suggested_qty=91 if kind == 'predicted' else 99, demand_per_day=30.0)
    d.update(kw)
    return d


def pay(new=(), resolved=(), open_alerts=None):
    new = list(new)
    return alerts.payload('ds', 'at_or_below', None, new, list(resolved), new if open_alerts is None else open_alerts)


def test_payload_summary_counts_predicted_and_below_min_and_keeps_old_keys():
    open_alerts = [alert('predicted'), alert('predicted', material='P2'), alert('below_min', material='B1'),
                   {k: v for k, v in alert('below_min', material='LEGACY').items() if k != 'kind'}]
    s = pay(open_alerts=open_alerts)['summary']
    assert s['predicted'] == 2 and s['below_min'] == 2 and s['open'] == 4
    assert {'open', 'new', 'resolved', 'by_supermarket'} <= set(s)
    assert s['by_supermarket'] == {'Silver': 4}
    empty = pay()['summary']
    assert empty['predicted'] == 0 and empty['below_min'] == 0


def test_predicted_email_subject_wording_and_more_suffix():
    one = alerts.email_message(pay([alert()]), 'a@x.test', ['b@x.test'])
    assert one['Subject'] == '[Stock alert] PRED-1 at Silver B1 predicted to reach minimum in 2.4 h'
    two = alerts.email_message(pay([alert(), alert(material='PRED-2')]), 'a@x.test', ['b@x.test'])
    assert two['Subject'] == '[Stock alert] PRED-1 at Silver B1 predicted to reach minimum in 2.4 h (+1 more)'
    whole = alerts.email_message(pay([alert(hours_to_min=3.0)]), 'a@x.test', ['b@x.test'])
    assert 'reach minimum in 3 h' in whole['Subject']                               # fmt() drops trailing .0


def test_below_min_email_subject_unchanged():
    msg = alerts.email_message(pay([alert('below_min', material='BM-1')]), 'a@x.test', ['b@x.test'])
    assert msg['Subject'] == '[Stock alert] BM-1 at Silver B1 is at or below minimum'
    legacy = {k: v for k, v in alert('below_min', material='BM-1').items() if k != 'kind'}
    assert alerts.email_message(pay([legacy]), 'a@x.test', ['b@x.test'])['Subject'] == msg['Subject']


def test_email_body_lists_predicted_rows_with_hours_and_suggestion():
    msg = alerts.email_message(pay([alert(), alert('below_min', material='BM-1')]), 'a@x.test', ['b@x.test'])
    text = msg.get_body(('plain',)).get_content()
    line = next(l for l in text.splitlines() if 'PRED-1' in l)
    assert 'reaches min in 2.4 h' in line and 'suggest 91' in line
    bm = next(l for l in text.splitlines() if 'BM-1' in l)
    assert 'reaches min' not in bm


def test_teams_card_has_predicted_section_only_when_a_new_alert_is_predicted():
    def blocks(p): return alerts.teams_card(p)['attachments'][0]['content']['body']
    with_pred = blocks(pay([alert(), alert('below_min', material='BM-1')]))
    assert any(b.get('type') == 'TextBlock' and b.get('text') == 'Predicted' for b in with_pred)
    dumped = json.dumps(with_pred)
    assert 'reaches min in 2.4 h' in dumped and 'suggest 91' in dumped and 'PRED-1' in dumped
    without = blocks(pay([alert('below_min', material='BM-1')]))
    assert not any(b.get('text') == 'Predicted' for b in without)
    assert 'reaches min' not in json.dumps(without)


def test_delivery_of_predicted_payload_leaks_no_secrets(tmp_path):
    cfg = dict(alerts.config({}), outbox=str(tmp_path / 'outbox'), smtp_password='pw-SECRET1', teams_url='https://teams.invalid/h?sig=SECRET2',
               webhook_url='https://hook.invalid/x?k=SECRET3')

    def boom(url, body): raise OSError(f'cannot reach {url}')
    p = pay([alert()])
    out = alerts.deliver(p, cfg, tmp_path / 'outbox', poster=boom)
    blob = json.dumps(out) + json.dumps(alerts.public_config(cfg)) + out['text']
    for secret in ('pw-SECRET1', 'SECRET2', 'SECRET3'): assert secret not in blob
    assert 'predicted to reach minimum' in out['subject']
    assert {r['channel']: r['status'] for r in out['results']} == {'email': 'saved', 'teams': 'failed', 'webhook': 'failed'}


def test_project_exposes_raw_demand_inputs_for_the_dashboard_preview():
    p = run(item(), [inv(replen=30, planned=600, observed=4)])
    assert p['demand_inputs'] == dict(history=30, schedule=20.0, observed=4)
    assert run(item(), [])['demand_inputs'] == dict(history=None, schedule=None, observed=None)
    assert run(item(), [inv(planned=600, bom_status='shared')])['demand_inputs']['schedule'] is None
