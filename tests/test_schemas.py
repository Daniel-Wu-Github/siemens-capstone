"""Unit tests for BOM, Routing, and Bin Master data contracts and schemas."""
import pytest
from siemens_capstone.schemas import (
    parse_bom_records,
    parse_routing_records,
    parse_bin_master_records,
    generate_synthetic_manufacturing_fixtures,
    BOMItem,
    BinMaster,
)


def test_parse_valid_bom_records():
    raw = [
        {'Item': 21010, 'Part': '11-1801-01', 'Description': 'Standoff', 'Qty': '4', 'Mat Type': 'M', 'SubAssembly': 'SUB-1'},
        {'Item': '21010.0', 'Part': 'P15480-02', 'Description': 'Bracket', 'Qty': 1.0, 'Mat Type': 'M', 'SubAssembly': 'SUB-1'},
    ]
    bom = parse_bom_records(raw)
    assert len(bom) == 2
    assert bom[0].item_id == '21010'
    assert bom[0].part_id == '11-1801-01'
    assert bom[0].qty_per_item == 4.0
    assert bom[1].item_id == '21010'
    assert bom[1].part_id == 'P15480-02'


def test_parse_invalid_bom_raises():
    with pytest.raises(ValueError, match="Missing Item"):
        parse_bom_records([{'Item': '', 'Part': '11-1801-01', 'Qty': 1}])

    with pytest.raises(ValueError, match="Missing Part"):
        parse_bom_records([{'Item': '21010', 'Part': None, 'Qty': 1}])

    with pytest.raises(ValueError, match="Qty"):
        parse_bom_records([{'Item': '21010', 'Part': '11-1801-01', 'Qty': -5}])

    with pytest.raises(ValueError, match="Qty"):
        parse_bom_records([{'Item': '21010', 'Part': '11-1801-01', 'Qty': 'not-a-number'}])


def test_parse_routing_records():
    raw = [
        {'Item': '21010', 'Routing': 'CCO07_MSB01-S1-P6'},
        {'Item': '21010', 'Routing': 'CCO07_MSB01-S1-TEST01'},
        {'Item': '21020', 'Routing': 'CCO07_MSB01-S2-M3'},
    ]
    routes = parse_routing_records(raw)
    assert len(routes) == 3
    assert routes[0].sequence_order == 1
    assert routes[1].sequence_order == 2
    assert routes[1].station_name == 'TEST01'
    assert routes[2].sequence_order == 1


def test_parse_bin_master_records():
    raw = [
        {'Part': '11-1801-01', 'Description': 'Insulator', 'Type': '401', 'Location': 'BIN-A', 'Min': 10, 'Max': 50},
        {'Part': '11-1628-01', 'Description': 'Spacer', 'Type': '402', 'Location': 'BIN-B', 'Min': 0, 'Max': 25},
    ]
    bins = parse_bin_master_records(raw)
    assert '11-1801-01' in bins
    assert bins['11-1801-01'].min_stock == 10.0
    assert bins['11-1801-01'].max_capacity == 50.0
    assert bins['11-1801-01'].safety_headroom_capacity == 40.0


def test_bin_master_invalid_ranges():
    with pytest.raises(ValueError, match="Max"):
        parse_bin_master_records([{'Part': 'P1', 'Min': 50, 'Max': 20}])

    with pytest.raises(ValueError, match="Min"):
        parse_bin_master_records([{'Part': 'P1', 'Min': -1, 'Max': 20}])


def test_synthetic_fixtures_consistent():
    fixtures = generate_synthetic_manufacturing_fixtures()
    assert 'bom' in fixtures and len(fixtures['bom']) > 5
    assert 'routing' in fixtures and len(fixtures['routing']) > 3
    assert 'bin_masters' in fixtures and len(fixtures['bin_masters']) >= 5
    for item in fixtures['bom']:
        assert item.part_id in fixtures['bin_masters']
