import http.client
import io
import json
import logging
import urllib.error

import pytest

import arkdata.sources.hella_api as client
from arkdata.sources.hella_api import HellaAPIClient, HellaAPIError


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _install_urlopen(monkeypatch, body=None, error=None):
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append({'url': url, 'timeout': timeout})
        if error is not None:
            raise error
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return FakeResponse(raw)

    monkeypatch.setattr(client.urllib.request, 'urlopen', fake_urlopen)
    return calls


def test_base_url_and_timeout_constants():
    assert client.HELLA_API_BASE_URL == 'https://awedtan.ca/api'
    assert client.REQUEST_TIMEOUT_SECONDS == 10


def test_fetch_value_builds_url_and_quotes_key(monkeypatch):
    calls = _install_urlopen(monkeypatch, {'value': 1})
    assert client._fetch_value('operator', 'Ch\'en the Holungday') == 1
    assert calls == [{'url': 'https://awedtan.ca/api/operator/Ch%27en%20the%20Holungday', 'timeout': 10}]


def test_fetch_value_custom_base_url(monkeypatch):
    calls = _install_urlopen(monkeypatch, {'value': 1})
    client._fetch_value('enemy', 'e1', base_url='http://localhost/api')
    assert calls[0]['url'] == 'http://localhost/api/enemy/e1'


@pytest.mark.parametrize('error', [
    urllib.error.URLError('no network'),
    urllib.error.HTTPError('http://x', 404, 'Not Found', {}, None),
])
def test_fetch_value_network_errors_raise_hella_api_error(monkeypatch, error):
    _install_urlopen(monkeypatch, error=error)
    with pytest.raises(HellaAPIError, match='Failed to reach HellaAPI'):
        client._fetch_value('operator', 'x')


def test_fetch_value_invalid_json_raises(monkeypatch):
    _install_urlopen(monkeypatch, b'not json')
    with pytest.raises(HellaAPIError, match='invalid JSON'):
        client._fetch_value('operator', 'x')


@pytest.mark.parametrize('payload', [{}, [], 'text'])
def test_fetch_value_missing_value_raises(monkeypatch, payload):
    _install_urlopen(monkeypatch, payload)
    with pytest.raises(HellaAPIError, match="Unexpected HellaAPI response shape for operator 'x'"):
        client._fetch_value('operator', 'x')


def test_fetch_operator_data_returns_data(monkeypatch):
    _install_urlopen(monkeypatch, {'value': {'data': {'name': 'Amiya'}}})
    assert client.fetch_operator_data('Amiya') == {'name': 'Amiya'}


@pytest.mark.parametrize('value', [{}, None, 'text'])
def test_fetch_operator_data_bad_shape_raises(monkeypatch, value):
    _install_urlopen(monkeypatch, {'value': value})
    with pytest.raises(HellaAPIError, match="operator 'Amiya'"):
        client.fetch_operator_data('Amiya')


def test_fetch_stage_data_returns_list(monkeypatch):
    calls = _install_urlopen(monkeypatch, {'value': [{'excel': {}}]})
    assert client.fetch_stage_data('main_01-07') == [{'excel': {}}]
    assert calls[0]['url'].endswith('/stage/main_01-07')


@pytest.mark.parametrize('value', [[], {}, None])
def test_fetch_stage_data_bad_shape_raises(monkeypatch, value):
    _install_urlopen(monkeypatch, {'value': value})
    with pytest.raises(HellaAPIError, match="stage 'main_01-07'"):
        client.fetch_stage_data('main_01-07')


def test_fetch_enemy_data_returns_dict(monkeypatch):
    value = {'excel': {}, 'levels': {}}
    calls = _install_urlopen(monkeypatch, {'value': value})
    assert client.fetch_enemy_data('enemy_1') == value
    assert calls[0]['url'].endswith('/enemy/enemy_1')


@pytest.mark.parametrize('value', [{'excel': {}}, {'levels': {}}, [], None])
def test_fetch_enemy_data_bad_shape_raises(monkeypatch, value):
    _install_urlopen(monkeypatch, {'value': value})
    with pytest.raises(HellaAPIError, match="enemy 'enemy_1'"):
        client.fetch_enemy_data('enemy_1')


def test_fetch_deployable_data_returns_whole_value(monkeypatch):
    value = {'data': {'name': 'Mon3tr'}, 'skills': [{'excel': {}}], 'range': {}}
    calls = _install_urlopen(monkeypatch, {'value': value})
    assert client.fetch_deployable_data('token_10002_kalts_mon3tr') == value
    assert calls[0]['url'].endswith('/deployable/token_10002_kalts_mon3tr')


@pytest.mark.parametrize('value', [{'skills': []}, {'data': None}, {'data': []}, [], None])
def test_fetch_deployable_data_bad_shape_raises(monkeypatch, value):
    _install_urlopen(monkeypatch, {'value': value})
    with pytest.raises(HellaAPIError, match="^Unexpected HellaAPI response shape for deployable 'token_1'$"):
        client.fetch_deployable_data('token_1')


@pytest.mark.parametrize('fetch, resource, value', [
    ('fetch_operator_data', 'operator', {'data': {}}),
    ('fetch_stage_data', 'stage', [{}]),
    ('fetch_enemy_data', 'enemy', {'excel': {}, 'levels': {}}),
    ('fetch_deployable_data', 'deployable', {'data': {}}),
])
def test_fetch_functions_request_their_resource_from_custom_base_url(monkeypatch, fetch, resource, value):
    calls = _install_urlopen(monkeypatch, {'value': value})
    getattr(client, fetch)('key 1', base_url='http://localhost/api')
    assert calls == [{'url': f'http://localhost/api/{resource}/key%201', 'timeout': 10}]


# HellaAPIClient


def _install_responses(monkeypatch, responses):
    '''
    Makes urlopen return (or raise) each item of responses in turn: an
    Exception is raised, bytes are returned as the body, anything else as JSON.
    '''
    responses = list(responses)
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append({'url': url, 'timeout': timeout})
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response if isinstance(response, bytes) else json.dumps(response).encode())

    monkeypatch.setattr(client.urllib.request, 'urlopen', fake_urlopen)
    return calls


def _http_error(code):
    return urllib.error.HTTPError('http://x', code, 'error', {}, None)


def _client(**kwargs):
    sleeps = []
    return HellaAPIClient('http://api.test/api/', sleep=sleeps.append, **kwargs), sleeps


def test_client_defaults():
    hella = HellaAPIClient()
    assert (hella.base_url, hella.timeout, hella.bulk_timeout, hella.retries, hella.retry_delay) == (
        'https://awedtan.ca/api', 10, 120, 3, 1.0)
    assert client.BULK_TIMEOUT_SECONDS == 120
    assert {429, 500, 502, 503, 504} == client.RETRYABLE_STATUS_CODES


@pytest.mark.parametrize('retries', [0, -1])
def test_client_requires_at_least_one_attempt(retries):
    with pytest.raises(ValueError, match=f'retries must be at least 1, got {retries}'):
        HellaAPIClient(retries=retries)


def test_get_json_builds_url_with_repeated_params_and_strips_trailing_slash(monkeypatch):
    calls = _install_responses(monkeypatch, [{'ok': 1}])
    hella, _ = _client()
    assert hella.get_json('operator', params={'include': ['canon', 'meta']}) == {'ok': 1}
    assert calls == [{'url': 'http://api.test/api/operator?include=canon&include=meta', 'timeout': 10}]


def test_get_json_without_params_has_no_query_string(monkeypatch):
    calls = _install_responses(monkeypatch, [[]])
    hella, _ = _client(timeout=4)
    hella.get_json('about')
    assert calls == [{'url': 'http://api.test/api/about', 'timeout': 4}]


@pytest.mark.parametrize('error', [
    urllib.error.URLError('no network'), TimeoutError('timed out'), ConnectionResetError('reset'),
    http.client.IncompleteRead(b''), _http_error(429), _http_error(500), _http_error(502), _http_error(503), _http_error(504),
])
def test_get_json_retries_temporary_failures_with_growing_waits(monkeypatch, caplog, error):
    calls = _install_responses(monkeypatch, [error, error, {'ok': 1}])
    hella, sleeps = _client(retry_delay=2.0)
    with caplog.at_level(logging.WARNING):
        assert hella.get_json('about') == {'ok': 1}
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]
    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.WARNING]
    assert caplog.records[0].getMessage().startswith('HellaAPI request to http://api.test/api/about failed (')
    assert caplog.records[0].getMessage().endswith('); retrying in 2.0 s (attempt 2 of 3)')
    assert caplog.records[1].getMessage().endswith('); retrying in 4.0 s (attempt 3 of 3)')
    assert caplog.records[0].getMessage() == (
        f'HellaAPI request to http://api.test/api/about failed ({error}); retrying in 2.0 s (attempt 2 of 3)')


@pytest.mark.parametrize('error', [urllib.error.URLError('no network'), _http_error(503)])
def test_get_json_gives_up_after_the_last_attempt(monkeypatch, error):
    calls = _install_responses(monkeypatch, [error, error, error, {'never': 'reached'}])
    hella, sleeps = _client()
    with pytest.raises(HellaAPIError, match=r'^Failed to reach HellaAPI at http://api.test/api/about: '):
        hella.get_json('about')
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize('code', [400, 401, 403, 404])
def test_get_json_does_not_retry_permanent_http_errors(monkeypatch, code):
    calls = _install_responses(monkeypatch, [_http_error(code), {'never': 'reached'}])
    hella, sleeps = _client()
    with pytest.raises(HellaAPIError, match='Failed to reach HellaAPI'):
        hella.get_json('operator/missing')
    assert len(calls) == 1
    assert sleeps == []


def test_get_json_invalid_json_is_not_retried(monkeypatch):
    calls = _install_responses(monkeypatch, [b'<html>', {'never': 'reached'}])
    hella, sleeps = _client()
    with pytest.raises(HellaAPIError, match=r'^HellaAPI returned invalid JSON for http://api.test/api/about: '):
        hella.get_json('about')
    assert (len(calls), sleeps) == (1, [])


def test_single_attempt_client_never_sleeps(monkeypatch):
    calls = _install_responses(monkeypatch, [urllib.error.URLError('down')])
    hella, sleeps = _client(retries=1)
    with pytest.raises(HellaAPIError):
        hella.get_json('about')
    assert (len(calls), sleeps) == (1, [])


@pytest.mark.parametrize('response', [[{'index': 102, 'hash': 'h'}], {'index': 102, 'hash': 'h'}])
def test_fetch_about_returns_the_latest_update(monkeypatch, response):
    calls = _install_responses(monkeypatch, [response])
    hella, _ = _client()
    assert hella.fetch_about() == {'index': 102, 'hash': 'h'}
    assert calls[0]['url'] == 'http://api.test/api/about'


@pytest.mark.parametrize('response', [[], [{'hash': 'h'}], {'hash': 'h'}, 'text'])
def test_fetch_about_bad_shape_raises(monkeypatch, response):
    _install_responses(monkeypatch, [response])
    hella, _ = _client()
    with pytest.raises(HellaAPIError, match='^Unexpected HellaAPI response shape for about$'):
        hella.fetch_about()


def test_fetch_document_quotes_key_and_returns_whole_document(monkeypatch):
    document = {'canon': 'char_002_amiya', 'keys': ['char_002_amiya', 'amiya'], 'value': {'data': {}}, 'meta': {'hash': 'h'}}
    calls = _install_responses(monkeypatch, [document])
    hella, _ = _client()
    assert hella.fetch_document('operator', "Ch'en") == document
    assert calls == [{'url': 'http://api.test/api/operator/Ch%27en', 'timeout': 10}]


@pytest.mark.parametrize('response', [{'keys': []}, [], None])
def test_fetch_document_bad_shape_raises(monkeypatch, response):
    _install_responses(monkeypatch, [response])
    hella, _ = _client()
    with pytest.raises(HellaAPIError, match="^Unexpected HellaAPI response shape for enemy 'e1'$"):
        hella.fetch_document('enemy', 'e1')


def test_fetch_collection_uses_bulk_timeout_and_include(monkeypatch):
    calls = _install_responses(monkeypatch, [[{'canon': 'a'}], [{'canon': 'a'}]])
    hella, _ = _client(bulk_timeout=99)
    assert hella.fetch_collection('enemy') == [{'canon': 'a'}]
    assert hella.fetch_collection('enemy', include=['canon', 'meta']) == [{'canon': 'a'}]
    assert calls == [
        {'url': 'http://api.test/api/enemy', 'timeout': 99},
        {'url': 'http://api.test/api/enemy?include=canon&include=meta', 'timeout': 99},
    ]


def test_fetch_collection_bad_shape_raises(monkeypatch):
    _install_responses(monkeypatch, [{'not': 'a list'}])
    hella, _ = _client()
    with pytest.raises(HellaAPIError, match='^Unexpected HellaAPI response shape for the stage collection$'):
        hella.fetch_collection('stage')


def test_client_fetch_operator_stage_and_enemy_data(monkeypatch):
    calls = _install_responses(monkeypatch, [
        {'value': {'data': {'name': 'Amiya'}}},
        {'value': [{'excel': {}}]},
        {'value': {'excel': {}, 'levels': {}}},
    ])
    hella, _ = _client()
    assert hella.fetch_operator_data('Amiya') == {'name': 'Amiya'}
    assert hella.fetch_stage_data('main_01-07') == [{'excel': {}}]
    assert hella.fetch_enemy_data('enemy_1') == {'excel': {}, 'levels': {}}
    assert [call['url'] for call in calls] == [
        'http://api.test/api/operator/Amiya', 'http://api.test/api/stage/main_01-07', 'http://api.test/api/enemy/enemy_1']


def test_client_fetch_deployable_data(monkeypatch):
    calls = _install_responses(monkeypatch, [{'value': {'data': {'name': 'Mon3tr'}, 'skills': []}}])
    hella, _ = _client()
    assert hella.fetch_deployable_data('Mon3tr') == {'data': {'name': 'Mon3tr'}, 'skills': []}
    assert [call['url'] for call in calls] == ['http://api.test/api/deployable/Mon3tr']


@pytest.mark.parametrize('method, value, message', [
    ('fetch_operator_data', {}, "operator 'k'"),
    ('fetch_stage_data', [], "stage 'k'"),
    ('fetch_enemy_data', {'excel': {}}, "enemy 'k'"),
    ('fetch_deployable_data', {'skills': []}, "deployable 'k'"),
])
def test_client_fetch_data_bad_value_shape_raises(monkeypatch, method, value, message):
    _install_responses(monkeypatch, [{'value': value}])
    hella, _ = _client()
    with pytest.raises(HellaAPIError, match=f'^Unexpected HellaAPI response shape for {message}$'):
        getattr(hella, method)('k')


def test_module_functions_make_a_single_attempt(monkeypatch):
    calls = _install_responses(monkeypatch, [urllib.error.URLError('down'), {'value': {'data': {}}}])
    with pytest.raises(HellaAPIError):
        client.fetch_operator_data('Amiya')
    assert len(calls) == 1


def test_module_function_shape_error_names_the_url(monkeypatch):
    _install_responses(monkeypatch, [{'no value': 1}])
    with pytest.raises(HellaAPIError, match=r"^Unexpected HellaAPI response shape for operator 'x' from https://awedtan.ca/api/operator/x$"):
        client._fetch_value('operator', 'x')
