"""Table A7.4: validation tests VT-01 to VT-04.

These score the model against the supplied Siemens extract rather than against
hand-built rows. They are the tests most likely to fail, and a failure here is
a finding about the data or the model, not a broken test.
"""
import pytest

from siemens_capstone import pipeline

from conftest import TEST_WINDOW, TRAIN_WINDOW, requires_real_data

pytestmark = pytest.mark.validation

MAPE_TARGET = 25.0        # VT-01: 25% or less
ON_TIME_TARGET = 80.0     # VT-02: at least 80% alerted in time
FALSE_ALERT_TARGET = 20.0  # VT-03: no more than 20% false
TOP_N = 20


@pytest.fixture(scope='module')
def backtest(real_events):
    return pipeline.backtest_rate(real_events, *TRAIN_WINDOW, *TEST_WINDOW, top_n=TOP_N)


@pytest.fixture(scope='module')
def replay(real_events, config):
    """Replay at the configured warning window, with the full sweep for context."""
    sweep = {days: pipeline.replay_alerts(real_events, warn_days=days)
             for days in (0, 1, 2, 3, 5, 7)}
    return pipeline.replay_alerts(real_events, warn_days=config['warn_days']), sweep


def _sweep_text(sweep):
    return ' | '.join(
        f"±{d}d: {r['on_time_pct']:.1f}% on time, {r['false_alert_pct']:.1f}% false"
        for d, r in sorted(sweep.items()))


@requires_real_data()
def test_VT01_forecast_backtest(backtest):
    """Input: train on May to July, test on August and September for the 20 most
    replenished parts. Expected: MAPE of 25% or less and better than the moving average."""
    assert backtest['scored'] >= 10, 'too few parts had test-window activity to score'

    model = backtest['model_mape']
    baseline = backtest['baseline_mape']
    print(f"\nVT-01 model MAPE {model:.1f}% vs moving average {baseline:.1f}% "
          f"on {backtest['scored']} of {TOP_N} parts "
          f"({backtest['skipped_zero_actual']} had no test-window activity). "
          f"Train quantity {backtest['train_quantity']:,.0f} over {backtest['train_events']:,} events; "
          f"test quantity {backtest['test_quantity']:,.0f} over {backtest['test_events']:,} events.")

    # Beating the trailing average is the part the model controls.
    assert model < baseline, \
        f'model MAPE {model:.1f}% is no better than the moving average {baseline:.1f}%'

    assert model <= MAPE_TARGET, (
        f'forecast MAPE is {model:.1f}%, above the {MAPE_TARGET}% target '
        f'(moving average {baseline:.1f}%). Replenishment volume in the supplied '
        f'extract falls from {backtest["train_events"]:,} events in May-July to '
        f'{backtest["test_events"]:,} in August-September, so a rate fitted on the '
        f'training window over-predicts the test window for most parts.')


@requires_real_data()
def test_VT02_alert_timing(replay, config):
    """Input: replay daily snapshots in order with a 3-hour lead time.
    Expected: at least 80% of low-stock events are alerted in time.

    The supplied extract has one undated bin snapshot, so there are no daily
    snapshots to replay. This replays confirmed replenishments instead: a bin
    is refilled because it reached its minimum. Replenishment dates carry one
    day of resolution, so a 3-hour lead time cannot be resolved; the warning
    window comes from the config file and the sweep below shows the trade-off.
    """
    result, sweep = replay
    print(f"\nVT-02 {result['on_time_pct']:.1f}% of {result['events']:,} low-stock events "
          f"alerted in time at a ±{result['warn_days']:g} day window across "
          f"{result['positions']:,} positions. Sweep: {_sweep_text(sweep)}")

    assert result['events'] > 100, 'too few recurring positions to judge timing'
    assert result['on_time_pct'] >= ON_TIME_TARGET, (
        f"{result['on_time_pct']:.1f}% of low-stock events were alerted in time, "
        f"below the {ON_TIME_TARGET}% target at a ±{result['warn_days']:g} day window. "
        f'Sweep: {_sweep_text(sweep)}')


@requires_real_data()
def test_VT03_false_alerts(replay, config):
    """Input: same replay as VT-02. Expected: no more than 20% of alerts are false."""
    result, sweep = replay
    print(f"\nVT-03 {result['false_alert_pct']:.1f}% of {result['alerts']:,} alerts were false "
          f"at a ±{result['warn_days']:g} day window. Sweep: {_sweep_text(sweep)}")

    assert result['alerts'] > 100, 'too few alerts to judge the false-alert rate'
    assert result['false_alert_pct'] <= FALSE_ALERT_TARGET, (
        f"{result['false_alert_pct']:.1f}% of alerts were false, above the "
        f"{FALSE_ALERT_TARGET}% target at a ±{result['warn_days']:g} day window. "
        f'Sweep: {_sweep_text(sweep)}')


@requires_real_data()
def test_VT04_sponsor_check(real_events, sponsor_summary):
    """Input: compare our most replenished parts to the table Christian sent on October 1.
    Expected: the top 2 parts in each supermarket match."""
    sponsor = pipeline.sponsor_top_parts(sponsor_summary)
    ours = pipeline.top_parts_by_supermarket(real_events, top_n=2)
    assert sponsor, 'the sponsor table could not be read'

    counts = {}
    for location, entries in sponsor.items():
        for entry in entries:
            counts[(location, entry['part'])] = sum(
                1 for e in real_events if e['location'] == location and e['part'] == entry['part'])

    # Every part the sponsor listed must at least exist in our extract.
    unknown = [f'{loc}/{part}' for (loc, part), n in counts.items() if n == 0]
    assert unknown == [], f'sponsor parts absent from our replenishment data: {unknown}'

    lines, matched = [], 0
    for location in sorted(sponsor):
        theirs = [e['part'] for e in sponsor[location]]
        mine = [e['part'] for e in ours.get(location, [])]
        if set(theirs) == set(mine):
            matched += 1
        else:
            lines.append(
                f"{location} {sponsor[location][0]['supermarket'] or ''}: "
                f"sponsor {theirs} (counts {[e['replens'] for e in sponsor[location]]}, "
                f"ours {[counts[(location, p)] for p in theirs]}) "
                f"vs ours {mine} (counts {[e['replens'] for e in ours.get(location, [])]})")
    print(f'\nVT-04 {matched}/{len(sponsor)} supermarkets match on top 2 parts')

    assert matched == len(sponsor), (
        f'top 2 parts match in {matched} of {len(sponsor)} supermarkets. Every sponsor '
        f'part exists in our data, but the counts differ by an order of magnitude, so the '
        f'sponsor table is measuring a different window or a different definition of a '
        f'replenishment. Ask Christian which period and movement types produced it.\n  '
        + '\n  '.join(lines))
