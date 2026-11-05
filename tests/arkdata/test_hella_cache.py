import json
import logging
from datetime import datetime, timedelta

import pytest

from arkdata.sources.hella_api import HellaAPIError
from arkdata.sources.hella_cache import (
    BULK_RESOURCES,
    DEFAULT_BULK_THRESHOLD,
    DEFAULT_MAX_AGE,
    LISTING_FIELDS,
    ON_DEMAND_RESOURCES,
    HellaCache,
    _canon_of,
)

START = datetime(2026, 11, 2, 12, 0, 0)


def _doc(canon, version=1, keys=None, value=None):
    return {
        '_id': f'id-{canon}', 'canon': canon, 'keys': keys if keys is not None else [canon, canon.upper()],
        'meta': {'hash': f'{canon}-v{version}', 'updatedIndex': 100 + version},
        'value': value if value is not None else {'version': version},
    }


def _operator(canon, version=1, keys=None):
    return _doc(canon, version, keys, value={'data': {'name': canon, 'version': version}})


def _enemy(canon, version=1):
    return _doc(canon, version, value={'excel': {'name': canon}, 'levels': {'version': version}})


def _deployable(canon, version=1):
    return _doc(canon, version, value={'data': {'name': canon, 'phases': []}, 'skills': [], 'range': {'version': version}})


def _stage(canon, version=1):
    return _doc(canon, version, value=[{'excel': {'stageId': canon}, 'version': version}])


class FakeHellaClient:
    '''
    A fake HellaAPIClient holding collections of documents in memory and
    recording every request.
    '''

    def __init__(self, collections, about_index=101):
        self.collections = {resource: {doc['canon']: doc for doc in docs} for resource, docs in collections.items()}
        self.about = {'index': about_index, 'hash': f'about-{about_index}', 'date': 1793602532}
        self.calls = []
        self.fail_documents = set()
        self.fail_about = False

    def update(self, resource, document, about_index):
        self.collections[resource][document['canon']] = document
        self.about = {'index': about_index, 'hash': f'about-{about_index}', 'date': 1793602532}

    def remove(self, resource, canon, about_index):
        del self.collections[resource][canon]
        self.about = {'index': about_index, 'hash': f'about-{about_index}', 'date': 1793602532}

    def fetch_about(self):
        self.calls.append(('about',))
        if self.fail_about:
            raise HellaAPIError('HellaAPI down')
        return dict(self.about)

    def fetch_collection(self, resource, include=None):
        self.calls.append(('collection', resource, tuple(include) if include else None))
        documents = list(self.collections.get(resource, {}).values())
        if include:
            return [{field: doc[field] for field in include if field in doc} for doc in documents]
        return [json.loads(json.dumps(doc)) for doc in documents]

    def fetch_document(self, resource, key):
        self.calls.append(('document', resource, key))
        if key in self.fail_documents:
            raise HellaAPIError(f'could not fetch {key}')
        for doc in self.collections.get(resource, {}).values():
            if key == doc['canon'] or key in doc['keys']:
                return json.loads(json.dumps(doc))
        raise HellaAPIError(f'Failed to reach HellaAPI at /{resource}/{key}: HTTP Error 404')


class Clock:
    def __init__(self):
        self.time = START

    def __call__(self):
        return self.time


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api():
    return FakeHellaClient({
        'operator': [_operator('char_002_amiya', keys=['char_002_amiya', 'Amiya', 'amiya']), _operator('char_003_kalts')],
        'enemy': [_enemy('enemy_1'), _enemy('enemy_2')],
        'deployable': [_deployable('token_10002_kalts_mon3tr')],
        'stage': [_stage('main_01-07'), _stage('main_06-09')],
    })


def _cache(api, tmp_path, clock, **kwargs):
    return HellaCache(api, tmp_path / 'cache', now=clock, **kwargs)


def _calls(api, kind=None):
    calls = [call for call in api.calls if kind is None or call[0] == kind]
    api.calls.clear()
    return calls


# helpers and defaults

def test_defaults():
    assert timedelta(days=14) == DEFAULT_MAX_AGE
    assert DEFAULT_BULK_THRESHOLD == 40
    assert BULK_RESOURCES == ('operator', 'enemy', 'deployable')
    assert ON_DEMAND_RESOURCES == ('stage',)
    assert LISTING_FIELDS == ['canon', 'keys', 'meta']


def test_cache_stores_settings(tmp_path):
    hella = HellaCache('client', str(tmp_path / 'c'), max_age=timedelta(days=3), bulk_threshold=5)
    assert (hella.client, hella.cache_dir) == ('client', tmp_path / 'c')
    assert (hella.max_age, hella.bulk_threshold) == (timedelta(days=3), 5)
    assert hella.now == datetime.now


@pytest.mark.parametrize('document, fallback, expected', [
    ({'canon': 'c', 'keys': ['k']}, 'f', 'c'),
    ({'keys': ['k', 'j']}, 'f', 'k'),
    ({'keys': []}, 'f', 'f'),
    ({}, 'f', 'f'),
])
def test_canon_of(document, fallback, expected):
    assert _canon_of(document, fallback) == expected


def test_canon_of_without_any_id_raises():
    with pytest.raises(HellaAPIError, match='^HellaAPI document has no id: '):
        _canon_of({'keys': []})


# first sync

def test_first_sync_downloads_bulk_collections_and_saves_state(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()

    assert _calls(api) == [('about',), ('collection', 'operator', None), ('collection', 'enemy', None),
                           ('collection', 'deployable', None)]
    root = tmp_path / 'cache'
    assert sorted(p.name for p in (root / 'operator').iterdir()) == ['char_002_amiya.json', 'char_003_kalts.json', 'index.json']
    expected = _operator('char_002_amiya', keys=['char_002_amiya', 'Amiya', 'amiya'])
    assert json.loads((root / 'operator' / 'char_002_amiya.json').read_text()) == expected
    index = json.loads((root / 'operator' / 'index.json').read_text())
    assert index['hashes'] == {'char_002_amiya': 'char_002_amiya-v1', 'char_003_kalts': 'char_003_kalts-v1'}
    assert index['aliases']['Amiya'] == 'char_002_amiya'
    assert index['aliases']['amiya'] == 'char_002_amiya'
    assert index['aliases']['char_003_kalts'.upper()] == 'char_003_kalts'
    assert json.loads((root / 'state.json').read_text()) == {'about': api.about, 'checked_at': START.isoformat()}
    assert not (root / 'stage').exists()
    assert not list(root.rglob('*.tmp'))


def test_fetching_after_sync_uses_the_cache(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    assert hella.fetch_operator_data('char_002_amiya') == {'name': 'char_002_amiya', 'version': 1}
    assert hella.fetch_operator_data('Amiya')['name'] == 'char_002_amiya'
    assert hella.fetch_operator_data('AMIYA')['name'] == 'char_002_amiya'
    assert hella.fetch_enemy_data('enemy_2') == {'excel': {'name': 'enemy_2'}, 'levels': {'version': 1}}
    assert _calls(api) == []


def test_fetching_an_operators_whole_value_uses_the_cache(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    assert hella.fetch_operator_value('Amiya') == {'data': {'name': 'char_002_amiya', 'version': 1}}
    assert _calls(api) == []


def test_fetching_a_deployable_after_sync_uses_the_cache(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    deployable = hella.fetch_deployable_data('token_10002_kalts_mon3tr')
    assert deployable == {'data': {'name': 'token_10002_kalts_mon3tr', 'phases': []}, 'skills': [], 'range': {'version': 1}}
    assert hella.fetch_deployable_data('TOKEN_10002_KALTS_MON3TR')['data']['name'] == 'token_10002_kalts_mon3tr'
    assert _calls(api) == []


def test_up_to_date_cache_still_downloads_a_bulk_collection_it_has_never_had(api, tmp_path, clock):
    # A cache synced before deployables were added: the about update is unchanged, but there's no deployable index.
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    for path in (tmp_path / 'cache' / 'deployable').iterdir():
        path.unlink()
    (tmp_path / 'cache' / 'deployable').rmdir()
    clock.time += timedelta(days=1)
    _calls(api)
    hella.sync()
    assert _calls(api) == [('about',), ('collection', 'operator', ('canon', 'keys', 'meta')),
                           ('collection', 'enemy', ('canon', 'keys', 'meta')), ('collection', 'deployable', None)]
    assert (tmp_path / 'cache' / 'deployable' / 'token_10002_kalts_mon3tr.json').exists()
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['checked_at'] == clock.time.isoformat()
    _calls(api)
    hella.sync()
    assert _calls(api) == [('about',)]


# later syncs

def test_sync_with_unchanged_about_fetches_nothing_else(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    clock.time += timedelta(days=3)
    with caplog.at_level(logging.INFO):
        hella.sync()
    assert _calls(api) == [('about',)]
    assert 'HellaAPI data unchanged since the last check (update 101); using the cache' in caplog.text
    # The check time isn't moved forward, so the 14-day backstop still counts from the last full check.
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['checked_at'] == START.isoformat()


def test_sync_after_max_age_checks_listings_even_if_about_unchanged(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    clock.time += timedelta(days=14)
    hella.sync()
    assert _calls(api) == [('about',)]
    clock.time += timedelta(seconds=1)
    hella.sync()
    assert _calls(api) == [('about',), ('collection', 'operator', ('canon', 'keys', 'meta')),
                           ('collection', 'enemy', ('canon', 'keys', 'meta')),
                           ('collection', 'deployable', ('canon', 'keys', 'meta'))]
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['checked_at'] == clock.time.isoformat()


def test_new_about_hash_with_same_index_counts_as_a_change(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    api.about['hash'] = 'different'
    hella.sync()
    assert ('collection', 'operator', ('canon', 'keys', 'meta')) in _calls(api)


def test_changed_documents_are_downloaded_one_by_one(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    api.update('operator', _operator('char_003_kalts', version=2), about_index=102)
    api.update('enemy', _enemy('enemy_3'), about_index=102)
    _calls(api)
    with caplog.at_level(logging.INFO):
        hella.sync()

    assert _calls(api) == [
        ('about',),
        ('collection', 'operator', ('canon', 'keys', 'meta')),
        ('document', 'operator', 'char_003_kalts'),
        ('collection', 'enemy', ('canon', 'keys', 'meta')),
        ('document', 'enemy', 'enemy_3'),
        ('collection', 'deployable', ('canon', 'keys', 'meta')),
    ]
    assert hella.fetch_operator_data('char_003_kalts')['version'] == 2
    assert hella.fetch_enemy_data('enemy_3')['excel']['name'] == 'enemy_3'
    assert _calls(api) == []
    index = json.loads((tmp_path / 'cache' / 'operator' / 'index.json').read_text())
    assert index['hashes']['char_003_kalts'] == 'char_003_kalts-v2'
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['about']['index'] == 102
    assert 'Updated 1, removed 0 operator documents in the HellaAPI cache' in caplog.text


def test_unchanged_hash_is_not_downloaded_even_if_update_index_moved(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    reimported = _operator('char_002_amiya', keys=['char_002_amiya', 'Amiya', 'amiya'])
    reimported['meta']['updatedIndex'] = 999
    api.update('operator', reimported, about_index=102)
    _calls(api)
    hella.sync()
    assert _calls(api, 'document') == []


def test_removed_documents_are_deleted(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    api.remove('operator', 'char_003_kalts', about_index=102)
    hella.sync()
    assert not (tmp_path / 'cache' / 'operator' / 'char_003_kalts.json').exists()
    index = json.loads((tmp_path / 'cache' / 'operator' / 'index.json').read_text())
    assert 'char_003_kalts' not in index['hashes']
    assert 'char_003_kalts' not in index['aliases'].values()
    assert index['aliases']['Amiya'] == 'char_002_amiya'


def test_missing_document_file_is_downloaded_again(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    (tmp_path / 'cache' / 'enemy' / 'enemy_1.json').unlink()
    api.about = {'index': 102, 'hash': 'about-102'}
    _calls(api)
    hella.sync()
    assert _calls(api, 'document') == [('document', 'enemy', 'enemy_1')]
    assert (tmp_path / 'cache' / 'enemy' / 'enemy_1.json').exists()


def test_more_changes_than_threshold_downloads_whole_collection(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock, bulk_threshold=1)
    hella.sync()
    api.update('operator', _operator('char_002_amiya', version=2), about_index=102)
    api.update('operator', _operator('char_003_kalts', version=2), about_index=102)
    _calls(api)
    with caplog.at_level(logging.INFO):
        hella.sync()
    calls = _calls(api)
    assert ('collection', 'operator', None) in calls
    assert ('document', 'operator', 'char_002_amiya') not in calls
    assert hella.fetch_operator_data('char_002_amiya')['version'] == 2
    assert '2 operator documents changed; downloading the whole collection' in caplog.text


def test_changes_equal_to_threshold_are_downloaded_one_by_one(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock, bulk_threshold=2)
    hella.sync()
    api.update('operator', _operator('char_002_amiya', version=2), about_index=102)
    api.update('operator', _operator('char_003_kalts', version=2), about_index=102)
    _calls(api)
    hella.sync()
    assert _calls(api, 'document') == [('document', 'operator', 'char_002_amiya'), ('document', 'operator', 'char_003_kalts')]


def test_bulk_redownload_drops_documents_no_longer_in_the_collection(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock, bulk_threshold=0)
    hella.sync()
    api.remove('operator', 'char_003_kalts', about_index=102)
    api.update('operator', _operator('char_002_amiya', version=2), about_index=102)
    hella.sync()
    assert not (tmp_path / 'cache' / 'operator' / 'char_003_kalts.json').exists()
    index = json.loads((tmp_path / 'cache' / 'operator' / 'index.json').read_text())
    assert index['hashes'] == {'char_002_amiya': 'char_002_amiya-v2'}


def test_failed_document_keeps_old_copy_and_retries_next_run(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    api.update('operator', _operator('char_003_kalts', version=2), about_index=102)
    api.update('operator', _operator('char_002_amiya', version=2), about_index=102)
    api.fail_documents = {'char_003_kalts'}
    with caplog.at_level(logging.WARNING):
        hella.sync()

    assert hella.fetch_operator_data('char_003_kalts')['version'] == 1
    assert hella.fetch_operator_data('char_002_amiya')['version'] == 2
    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.WARNING]
    assert caplog.records[0].getMessage() == (
        'Could not update operator char_003_kalts in the HellaAPI cache: could not fetch char_003_kalts')
    assert caplog.records[1].getMessage() == '1 documents could not be updated; the HellaAPI cache will be checked again next run'
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['about']['index'] == 101

    api.fail_documents = set()
    _calls(api)
    hella.sync()
    assert _calls(api, 'document') == [('document', 'operator', 'char_003_kalts')]
    assert hella.fetch_operator_data('char_003_kalts')['version'] == 2
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['about']['index'] == 102


def test_about_failure_raises_and_leaves_cache_usable(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    api.fail_about = True
    state_before = (tmp_path / 'cache' / 'state.json').read_text()
    with pytest.raises(HellaAPIError, match='HellaAPI down'):
        hella.sync()
    assert (tmp_path / 'cache' / 'state.json').read_text() == state_before
    _calls(api)
    assert hella.fetch_operator_data('Amiya')['name'] == 'char_002_amiya'
    assert _calls(api) == []


@pytest.mark.parametrize('state', [
    'not json', '[]', '{"about": {"index": 101}}', '{"about": {"index": 101, "hash": "about-101"}, "checked_at": "bad"}',
])
def test_unreadable_or_incomplete_state_triggers_a_check(api, tmp_path, clock, state):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    (tmp_path / 'cache' / 'state.json').write_text(state)
    _calls(api)
    hella.sync()
    assert ('collection', 'operator', ('canon', 'keys', 'meta')) in _calls(api)


# stages (cached on demand)

def test_stage_is_downloaded_on_first_use_then_cached(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    assert hella.fetch_stage_data('main_06-09') == [{'excel': {'stageId': 'main_06-09'}, 'version': 1}]
    assert hella.fetch_stage_data('MAIN_06-09')[0]['version'] == 1
    assert _calls(api) == [('document', 'stage', 'main_06-09')]
    index = json.loads((tmp_path / 'cache' / 'stage' / 'index.json').read_text())
    assert index['hashes'] == {'main_06-09': 'main_06-09-v1'}


def test_sync_only_checks_stages_when_some_are_cached(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    api.about = {'index': 102, 'hash': 'about-102'}
    _calls(api)
    hella.sync()
    assert ('collection', 'stage', ('canon', 'keys', 'meta')) not in _calls(api)


def test_changed_and_removed_cached_stages_are_dropped_and_refetched_when_used(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    hella.fetch_stage_data('main_06-09')
    hella.fetch_stage_data('main_01-07')
    api.update('stage', _stage('main_06-09', version=2), about_index=102)
    api.remove('stage', 'main_01-07', about_index=102)
    api.update('stage', _stage('main_09-01'), about_index=102)
    _calls(api)
    with caplog.at_level(logging.INFO):
        hella.sync()

    assert ('collection', 'stage', ('canon', 'keys', 'meta')) in _calls(api)
    assert not (tmp_path / 'cache' / 'stage' / 'main_06-09.json').exists()
    assert not (tmp_path / 'cache' / 'stage' / 'main_09-01.json').exists()
    assert 'Dropped 2 changed stage documents from the HellaAPI cache' in caplog.text
    assert hella.fetch_stage_data('main_06-09')[0]['version'] == 2
    assert _calls(api) == [('document', 'stage', 'main_06-09')]


def test_unchanged_cached_stages_are_kept(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    hella.fetch_stage_data('main_06-09')
    api.update('operator', _operator('char_003_kalts', version=2), about_index=102)
    hella.sync()
    _calls(api)
    hella.fetch_stage_data('main_06-09')
    assert _calls(api) == []


# refresh

def test_refresh_downloads_everything_again_and_drops_stages(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    hella.fetch_stage_data('main_06-09')
    clock.time += timedelta(hours=1)
    _calls(api)
    hella.sync(refresh=True)
    assert _calls(api) == [('about',), ('collection', 'operator', None), ('collection', 'enemy', None),
                           ('collection', 'deployable', None)]
    assert not (tmp_path / 'cache' / 'stage' / 'main_06-09.json').exists()
    assert json.loads((tmp_path / 'cache' / 'stage' / 'index.json').read_text())['hashes'] == {}
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['checked_at'] == clock.time.isoformat()


def test_refresh_on_an_empty_cache(api, tmp_path, clock):
    _cache(api, tmp_path, clock).sync(refresh=True)
    assert (tmp_path / 'cache' / 'operator' / 'char_002_amiya.json').exists()


# fetching without a sync, and damaged files

def test_fetch_without_sync_downloads_and_caches_by_canon_with_aliases(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    assert hella.fetch_operator_data('Amiya')['name'] == 'char_002_amiya'
    assert _calls(api) == [('document', 'operator', 'Amiya')]
    assert (tmp_path / 'cache' / 'operator' / 'char_002_amiya.json').exists()
    assert hella.fetch_operator_data('char_002_amiya')['name'] == 'char_002_amiya'
    assert hella.fetch_operator_data('amiya')['name'] == 'char_002_amiya'
    assert _calls(api) == []


def test_fetch_falls_back_to_requested_key_when_document_has_no_id(tmp_path, clock):
    api = FakeHellaClient({'operator': []})
    api.fetch_document = lambda resource, key: {'keys': [], 'value': {'data': {'name': 'x'}}}
    hella = _cache(api, tmp_path, clock)
    assert hella.fetch_operator_data('some key')['name'] == 'x'
    assert (tmp_path / 'cache' / 'operator' / 'some%20key.json').exists()
    api.fetch_document = None
    assert hella.fetch_operator_data('some key')['name'] == 'x'


def test_damaged_document_file_is_downloaded_again(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    (tmp_path / 'cache' / 'operator' / 'char_002_amiya.json').write_text('{broken')
    _calls(api)
    with caplog.at_level(logging.WARNING):
        assert hella.fetch_operator_data('Amiya')['name'] == 'char_002_amiya'
    assert _calls(api) == [('document', 'operator', 'Amiya')]
    assert 'Ignoring unreadable cache file' in caplog.text
    assert json.loads((tmp_path / 'cache' / 'operator' / 'char_002_amiya.json').read_text())['canon'] == 'char_002_amiya'


def test_damaged_index_is_treated_as_empty(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    (tmp_path / 'cache' / 'operator' / 'index.json').write_text('[]')
    _calls(api)
    hella.fetch_operator_data('Amiya')
    assert _calls(api) == [('document', 'operator', 'Amiya')]


def test_missing_document_raises_hella_api_error(api, tmp_path, clock):
    with pytest.raises(HellaAPIError, match='404'):
        _cache(api, tmp_path, clock).fetch_enemy_data('enemy_missing')


@pytest.mark.parametrize('method, resource, value, message', [
    ('fetch_operator_data', 'operator', {'no data': 1}, "operator 'x'"),
    ('fetch_stage_data', 'stage', [], "stage 'x'"),
    ('fetch_enemy_data', 'enemy', {'excel': {}}, "enemy 'x'"),
    ('fetch_deployable_data', 'deployable', {'data': None}, "deployable 'x'"),
    ('fetch_operator_value', 'operator', {'data': 'not a dict'}, "operator 'x'"),
])
def test_fetch_data_checks_value_shape(tmp_path, clock, method, resource, value, message):
    api = FakeHellaClient({resource: [_doc('x', value=value)]})
    with pytest.raises(HellaAPIError, match=f'^Unexpected HellaAPI response shape for {message}$'):
        getattr(_cache(api, tmp_path, clock), method)('x')


# edge cases found by mutation testing

def test_canon_of_error_shows_the_document():
    with pytest.raises(HellaAPIError, match=r"^HellaAPI document has no id: \{'keys': \[\], 'value': 1\}$"):
        _canon_of({'keys': [], 'value': 1})


def test_unreadable_file_warning_names_file_and_error(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    path = tmp_path / 'cache' / 'operator' / 'char_002_amiya.json'
    path.write_text('{broken')
    with caplog.at_level(logging.WARNING):
        hella.fetch_operator_data('Amiya')
    assert caplog.records[0].getMessage().startswith(f'Ignoring unreadable cache file {path}: Expecting property name')


def test_ids_with_slashes_stay_in_the_collection_folder(tmp_path, clock):
    api = FakeHellaClient({'stage': [_stage('act/01')]})
    hella = _cache(api, tmp_path, clock)
    hella.fetch_stage_data('act/01')
    assert (tmp_path / 'cache' / 'stage' / 'act%2F01.json').exists()
    assert not (tmp_path / 'cache' / 'stage' / 'act').exists()


@pytest.mark.parametrize('index', ['{}', '{"aliases": {}}', '{"hashes": {}}'])
def test_index_missing_parts_is_completed(api, tmp_path, clock, index):
    hella = _cache(api, tmp_path, clock)
    (tmp_path / 'cache' / 'operator').mkdir(parents=True)
    (tmp_path / 'cache' / 'operator' / 'index.json').write_text(index)
    assert hella.fetch_operator_data('char_003_kalts')['name'] == 'char_003_kalts'
    saved = json.loads((tmp_path / 'cache' / 'operator' / 'index.json').read_text())
    assert saved['hashes'] == {'char_003_kalts': 'char_003_kalts-v1'}
    assert saved['aliases']['char_003_kalts'] == 'char_003_kalts'


def test_lowercase_aliases_match_any_capitalisation(tmp_path, clock):
    api = FakeHellaClient({'operator': [_operator('char_017_huang', keys=['char_017_huang', "Kal'tsit"])]})
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    _calls(api)
    assert hella.fetch_operator_data("KAL'TSIT")['name'] == 'char_017_huang'
    assert _calls(api) == []


def test_new_alias_in_listing_is_picked_up_without_downloading(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    renamed = _operator('char_003_kalts')
    renamed['keys'] = renamed['keys'] + ['Kaltsit']
    api.update('operator', renamed, about_index=102)
    _calls(api)
    hella.sync()
    assert _calls(api, 'document') == []
    assert hella.fetch_operator_data('kaltsit')['name'] == 'char_003_kalts'
    assert _calls(api) == []


def test_removing_a_document_whose_file_is_already_gone(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    (tmp_path / 'cache' / 'operator' / 'char_003_kalts.json').unlink()
    api.remove('operator', 'char_003_kalts', about_index=102)
    hella.sync()
    assert 'char_003_kalts' not in json.loads((tmp_path / 'cache' / 'operator' / 'index.json').read_text())['hashes']


def test_bulk_redownload_with_already_missing_old_file(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    (tmp_path / 'cache' / 'operator' / 'char_003_kalts.json').unlink()
    api.remove('operator', 'char_003_kalts', about_index=102)
    hella.sync(refresh=True)
    assert json.loads((tmp_path / 'cache' / 'operator' / 'index.json').read_text())['hashes'] == {
        'char_002_amiya': 'char_002_amiya-v1'}


def test_bulk_redownload_after_threshold_still_records_the_update(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock, bulk_threshold=0)
    hella.sync()
    api.update('operator', _operator('char_003_kalts', version=2), about_index=102)
    hella.sync()
    assert json.loads((tmp_path / 'cache' / 'state.json').read_text())['about']['index'] == 102


def test_every_failed_document_is_counted_and_others_still_updated(api, tmp_path, clock, caplog):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    for canon in ('char_002_amiya', 'char_003_kalts'):
        api.update('operator', _operator(canon, version=2), about_index=102)
    api.update('enemy', _enemy('enemy_1', version=2), about_index=102)
    api.fail_documents = {'char_002_amiya', 'char_003_kalts'}
    with caplog.at_level(logging.WARNING):
        hella.sync()
    assert hella.fetch_enemy_data('enemy_1')['levels']['version'] == 2
    assert '2 documents could not be updated; the HellaAPI cache will be checked again next run' in caplog.text


def test_first_failure_does_not_stop_later_documents(api, tmp_path, clock):
    hella = _cache(api, tmp_path, clock)
    hella.sync()
    for canon in ('char_002_amiya', 'char_003_kalts'):
        api.update('operator', _operator(canon, version=2), about_index=102)
    api.fail_documents = {'char_002_amiya'}
    hella.sync()
    assert hella.fetch_operator_data('char_003_kalts')['version'] == 2
