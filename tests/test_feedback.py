"""Feedback form: storage, API, and validation."""
import json
from urllib.request import Request, urlopen
import pytest
from qc_helpers import make_store, serving


def test_submit_and_list_round_trip(tmp_path):
    store = make_store(tmp_path)
    result = store.submit_feedback('Alice', 'predicted', 'Chart is too small')
    assert 'id' in result and 'created_at' in result
    entries = store.list_feedback()
    assert len(entries) == 1
    assert entries[0]['name'] == 'Alice'
    assert entries[0]['page'] == 'predicted'
    assert entries[0]['message'] == 'Chart is too small'


def test_name_is_optional(tmp_path):
    store = make_store(tmp_path)
    store.submit_feedback(None, None, 'Anonymous feedback')
    assert store.list_feedback()[0]['name'] is None


def test_empty_message_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match='1–2000'):
        store.submit_feedback('Alice', 'stock', '')
    with pytest.raises(ValueError, match='1–2000'):
        store.submit_feedback('Alice', 'stock', '   ')


def test_long_message_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match='1–2000'):
        store.submit_feedback('Alice', 'stock', 'x' * 2001)


def test_list_order_is_newest_first(tmp_path):
    store = make_store(tmp_path)
    store.submit_feedback(None, None, 'First')
    store.submit_feedback(None, None, 'Second')
    entries = store.list_feedback()
    assert entries[0]['message'] == 'Second'


def test_list_limit_200(tmp_path):
    store = make_store(tmp_path)
    for i in range(210):
        store.submit_feedback(None, None, f'Entry {i}')
    assert len(store.list_feedback()) == 200


def test_http_post_and_get(tmp_path):
    store = make_store(tmp_path)
    with serving(store) as base:
        req = Request(base + '/api/feedback', data=json.dumps(
            {'name': 'Bob', 'page': 'insights', 'message': 'Great tool'}).encode(),
            headers={'Content-Type': 'application/json', 'Host': base.split('//')[1]},
            method='POST')
        with urlopen(req) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
            assert 'id' in body
        req2 = Request(base + '/api/feedback',
            headers={'Host': base.split('//')[1]})
        with urlopen(req2) as resp:
            entries = json.loads(resp.read())
            assert len(entries) == 1 and entries[0]['message'] == 'Great tool'


def test_http_empty_message_400(tmp_path):
    store = make_store(tmp_path)
    with serving(store) as base:
        req = Request(base + '/api/feedback',
            data=json.dumps({'message': ''}).encode(),
            headers={'Content-Type': 'application/json', 'Host': base.split('//')[1]},
            method='POST')
        try:
            urlopen(req)
            assert False, 'Should have raised'
        except Exception as e:
            assert e.code == 400
