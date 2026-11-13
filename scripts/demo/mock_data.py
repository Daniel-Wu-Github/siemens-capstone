"""Seeded mock workbook with invented parts for the single-file demo. Nothing here is Siemens data."""
from datetime import datetime
import math
import random

ADJ = ['Hex', 'Flat', 'Round', 'Slotted', 'Steel', 'Brass', 'Nylon', 'Coated', 'Angled', 'Spring']
NOUN = ['Bolt', 'Washer', 'Bracket', 'Clip', 'Spacer', 'Bushing', 'Gasket', 'Panel', 'Connector', 'Terminal', 'Hinge', 'Latch']
SIZE = ['M6', 'M8', 'M10', '10 mm', '25 mm', '40 mm', 'Small', 'Large', 'Type A', 'Type B']
TYPES = ['401', '402', '403', '406', '408', '410']
DEFAULT_WEIGHTS = (2.0, 1.0)  # shipped default history : schedule, override off
PROFILES = [('below_min', 10), ('predicted', 14), ('watch', 18), ('ok', 45), ('no_rate', 19), ('shared', 4)]
HOURS = dict(predicted=(3, 25), watch=(30, 68), ok=(90, 500))
WINDOW_DAYS = (1, 30)  # refills fall on October 1..30; the first and last are always present


def build(seed=482):
    rng = random.Random(seed)
    names = set()
    def fake_name():
        while True:
            n = f'{rng.choice(ADJ)} {rng.choice(NOUN).lower()} {rng.choice(SIZE)}'
            if n not in names: names.add(n); return n
    order = [name for name, count in PROFILES for _ in range(count)]
    rng.shuffle(order)
    bins, tols, transfers, bom, schedule = [], [], [], [], []
    n_flag = dict(short=0, freq=0, div=0)
    serial, transfer_id, line_id, shared_pending = 1000, 0, 0, []
    for index, profile in enumerate(order):
        serial += 1
        material = f'DEMO-{serial}'
        storage = TYPES[index % len(TYPES)]
        bin_ = f'D{index % 9 + 1}.{index % 14 + 1:02d}.{"ABC"[index % 3]}'
        name = fake_name()
        if profile == 'shared':
            shared_pending.append((material, storage, name))
            if len(shared_pending) == 2:  # two parts that each sit in two bins: BOM cannot split their schedule
                for mat, typ, nm in shared_pending:
                    for k in range(2):
                        bins.append([mat, nm, typ, f'S{k + 1}.{serial % 9 + 1:02d}.A', 40, 'PC'])
                        tols.append([mat, nm, int(typ), f'S{k + 1}.{serial % 9 + 1:02d}.A', 10, 100])
                    bom.append([f'ITEM-{mat}', 'Demo assembly', mat, 5, 'x', ''])
                shared_pending = []
            continue
        h = round(rng.uniform(3, 40), 1)
        has_rate = profile not in ('no_rate',)
        n = rng.choice([3, 4, 5, 6])
        short = has_rate and rng.random() < 0.28
        freq = has_rate and not short and rng.random() < 0.22
        if freq: n = rng.randint(9, 13)
        q = max(1, round(h * 30 / n))
        ratio = rng.uniform(0.12, 0.4) if short else rng.uniform(0.55, 1.0)
        maximum = max(20, math.ceil(q / ratio / 5) * 5)
        minimum = 0 if (profile == 'below_min' and rng.random() < 0.3) else max(5, round(maximum * rng.uniform(0.08, 0.3)))
        schedule_rate, factor = None, None
        if has_rate and rng.random() < 0.55:
            factor = rng.choice([0.25, 0.35, 2.2, 2.8]) if rng.random() < 0.5 else rng.uniform(0.85, 1.2)
            schedule_rate = h * factor
        demand = (DEFAULT_WEIGHTS[0] * h + DEFAULT_WEIGHTS[1] * schedule_rate) / sum(DEFAULT_WEIGHTS) if schedule_rate is not None else h
        if profile == 'below_min':
            available = minimum if rng.random() < 0.4 or minimum == 0 else max(0, minimum - rng.randint(1, max(1, minimum)))
        elif profile in HOURS:
            lo, hi = HOURS[profile]
            available = minimum + max(1, math.ceil(rng.uniform(lo, hi) * demand / 24))
        else:
            available = minimum + rng.randint(5, 60)
        available = min(available, maximum * 3)
        bins.append([material, name, storage, bin_, available, 'PC'])
        tols.append([material, name, int(storage), bin_, minimum, maximum])
        if has_rate:
            days = sorted({1, 30, *[rng.randint(2, 29) for _ in range(n - 2)]})[:n]
            for day in days:
                transfer_id += 1
                d = datetime(2026, 10, day)
                transfers.append([f'T{transfer_id}', 1, material, '001', storage, bin_, q, d, 0, d, 0])
            n_flag['short'] += short; n_flag['freq'] += freq
        if schedule_rate is not None:
            bom.append([f'ITEM-{material}', 'Demo assembly', material, 10, 'x', ''])
            for _ in range(max(1, round(schedule_rate * 3))):  # each line needs 10 -> planned = lines * 10, per day = lines / 3
                line_id += 1
                schedule.append([f'SO{line_id}', f'ITEM-{material}', datetime(2026, 11, 2)])
            n_flag['div'] += abs(factor - 1) > 0.5
    production = [[f'SO{i}', '0010', f'J{i}', f'PO{i}', 70, datetime(2026, 10, 9), datetime(2026, 10, 10), 'Passed', 'ST1'] for i in range(1, 41)]
    return dict(bins=bins, tolerances=tols, transfers=transfers, bom=bom, schedule=schedule, production=production, flags=n_flag)
