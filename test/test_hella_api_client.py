import io
import json
import urllib.error

import pytest

import lib.hella_api_client as client
from lib.hella_api_client import HellaAPIError


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


@pytest.mark.parametrize('fetch, resource, value', [
    ('fetch_operator_data', 'operator', {'data': {}}),
    ('fetch_stage_data', 'stage', [{}]),
    ('fetch_enemy_data', 'enemy', {'excel': {}, 'levels': {}}),
])
def test_fetch_functions_request_their_resource_from_custom_base_url(monkeypatch, fetch, resource, value):
    calls = _install_urlopen(monkeypatch, {'value': value})
    getattr(client, fetch)('key 1', base_url='http://localhost/api')
    assert calls == [{'url': f'http://localhost/api/{resource}/key%201', 'timeout': 10}]
