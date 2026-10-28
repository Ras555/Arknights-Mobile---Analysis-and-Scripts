import asyncio
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

import lib.arknights_account as account
from lib.arknights_account import calculate_trust, filter_operators, format_operator_data, TRUST_MILESTONES


# calculate_trust

def test_trust_milestones():
    assert TRUST_MILESTONES == {25: 804, 50: 2732, 75: 6195, 100: 10070, 125: 13945, 150: 17820, 175: 21695, 200: 25570}


@pytest.mark.parametrize('trust, favor', list(TRUST_MILESTONES.items()))
def test_trust_at_each_milestone(trust, favor):
    assert calculate_trust(favor) == pytest.approx(trust)


def test_trust_zero():
    assert calculate_trust(0) == 0


def test_trust_between_zero_and_first_milestone():
    assert calculate_trust(402) == pytest.approx(12.5)


def test_trust_between_milestones():
    assert calculate_trust((2732 + 6195) / 2) == pytest.approx(62.5)


def test_trust_just_below_max():
    assert 199.99 < calculate_trust(25569) < 200


def test_trust_at_max_returns_200_without_error(caplog):
    with caplog.at_level(logging.ERROR):
        assert calculate_trust(25570) == 200
    assert caplog.records == []


@pytest.mark.parametrize('favor', [25571, 99999])
def test_trust_above_max_logs_error_and_caps(caplog, favor):
    with caplog.at_level(logging.ERROR):
        assert calculate_trust(favor) == 200
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == f'Favor point {favor} exceeds the maximum of 25570. Capping trust at 200.'


# format_operator_data

def _char(char_id, potential=0, level=1, phase=0, favor=0, skill=1):
    return SimpleNamespace(char_id=char_id, potential_rank=potential, level=level, evolve_phase=phase,
                           favor_point=favor, main_skill_lvl=skill)


def test_format_operator_data():
    df = format_operator_data({'1': _char('char_a', 2, 40, 2, 10070, 7), '2': _char('char_b')})
    assert list(df.columns) == ['id', 'potential', 'level', 'phase', 'trust', 'skill_level']
    assert df.iloc[0].tolist() == ['char_a', 2, 40, 2, 100, 7]
    assert df.iloc[1]['trust'] == 0


def test_format_operator_data_empty():
    assert format_operator_data({}).empty


# filter_operators

@pytest.fixture
def roster():
    return pd.DataFrame({
        'id': ['a', 'b', 'c', 'd'],
        'phase': [0, 1, 2, 2],
        'level': [10, 50, 1, 90],
        'potential': [0, 3, 5, 1],
    })


def test_filter_no_bounds_returns_all(roster):
    assert filter_operators(roster)['id'].tolist() == ['a', 'b', 'c', 'd']


def test_filter_phase_bounds_inclusive(roster):
    assert filter_operators(roster, min_phase=2, max_phase=2)['id'].tolist() == ['c', 'd']
    assert filter_operators(roster, min_phase=1)['id'].tolist() == ['b', 'c', 'd']
    assert filter_operators(roster, max_phase=1)['id'].tolist() == ['a', 'b']


def test_filter_level_bounds_inclusive(roster):
    assert filter_operators(roster, min_level=50)['id'].tolist() == ['b', 'd']
    assert filter_operators(roster, max_level=10)['id'].tolist() == ['a', 'c']


def test_filter_potential_bounds_inclusive(roster):
    assert filter_operators(roster, min_potential=3)['id'].tolist() == ['b', 'c']
    assert filter_operators(roster, max_potential=1)['id'].tolist() == ['a', 'd']


def test_filter_combined_bounds(roster):
    assert filter_operators(roster, min_phase=2, max_level=50)['id'].tolist() == ['c']


def test_filter_handles_string_columns():
    df = pd.DataFrame({'id': ['a', 'b'], 'phase': ['2', '1'], 'level': ['1', '1'], 'potential': ['0', '0']})
    assert filter_operators(df, min_phase=2)['id'].tolist() == ['a']


def test_filter_rarity_not_applied(roster):
    assert len(filter_operators(roster, rarity=6)) == 4


# arkprts wrappers

def test_login_uses_yostar_en_email_code(monkeypatch):
    events = []

    class FakeAuth:
        def __init__(self, server):
            events.append(('auth', server))

        async def login_with_email_code(self, email):
            events.append(('login', email))

    class FakeClient:
        def __init__(self, auth=None, assets=None):
            events.append(('client', assets))
            self.auth = auth

    monkeypatch.setattr(account.arkprts, 'YostarAuth', FakeAuth)
    monkeypatch.setattr(account.arkprts, 'Client', FakeClient)
    client = asyncio.run(account.login('me@example.com'))
    assert events == [('auth', 'en'), ('login', 'me@example.com'), ('client', False)]
    assert isinstance(client.auth, FakeAuth)


class FakeArkClient:
    def __init__(self, chars=None, raw=None):
        self.chars = chars or {}
        self.raw = raw

    async def get_data(self):
        return SimpleNamespace(troop=SimpleNamespace(chars=self.chars))

    async def get_raw_data(self):
        return self.raw


def test_retrieve_operator_info_formats_roster(caplog):
    client = FakeArkClient(chars={'1': _char('char_a', phase=2)})
    with caplog.at_level(logging.INFO):
        df = asyncio.run(account.retrieve_operator_info(client))
    assert df['id'].tolist() == ['char_a']
    assert 'Retrieved 1 operators' in caplog.text


def test_retrieve_raw_data_returns_client_raw_data():
    assert asyncio.run(account.retrieve_raw_data(FakeArkClient(raw={'user': 1}))) == {'user': 1}


@pytest.mark.parametrize('bounds, expected', [
    ({'min_phase': 2, 'min_level': 50}, ['d']),
    ({'min_phase': 1, 'max_level': 10}, ['c']),
    ({'min_level': 10, 'min_potential': 1}, ['b', 'd']),
    ({'min_phase': 2, 'max_potential': 1}, ['d']),
    ({'max_phase': 1, 'min_potential': 1}, ['b']),
])
def test_filter_bounds_combine_with_earlier_bounds(roster, bounds, expected):
    assert filter_operators(roster, **bounds)['id'].tolist() == expected


@pytest.mark.parametrize('bounds, expected', [
    ({'min_phase': 2}, ['c', 'd']),
    ({'max_phase': 1}, ['a', 'b']),
    ({'min_level': 50}, ['b', 'd']),
    ({'max_level': 10}, ['a', 'c']),
    ({'min_potential': 3}, ['b', 'c']),
    ({'max_potential': 1}, ['a', 'd']),
])
def test_filter_compares_string_columns_as_numbers(roster, bounds, expected):
    # As strings, '10' < '9' and '90' > '100', so each bound must convert to int first.
    as_strings = roster.astype(str)
    as_strings.loc[as_strings['id'] == 'b', 'level'] = '50'
    assert filter_operators(as_strings, **bounds)['id'].tolist() == expected


def test_filter_string_levels_compare_numerically():
    df = pd.DataFrame({'id': ['a', 'b'], 'phase': ['2', '2'], 'level': ['9', '10'], 'potential': ['0', '0']})
    assert filter_operators(df, min_level=10)['id'].tolist() == ['b']
    assert filter_operators(df, max_level=9)['id'].tolist() == ['a']


def test_filter_works_with_non_default_index(roster):
    # Rows labelled 10-13 instead of 0-3, e.g. after an earlier filter.
    relabelled = roster.set_index(pd.Index([10, 11, 12, 13]))
    result = filter_operators(relabelled, min_phase=2)
    assert result['id'].tolist() == ['c', 'd']
    assert result.index.tolist() == [12, 13]


def test_filter_result_can_be_filtered_again(roster):
    once = filter_operators(roster, min_phase=1)
    assert filter_operators(once, max_level=10)['id'].tolist() == ['c']


def test_filter_empty_roster_returns_empty():
    result = filter_operators(format_operator_data({}), min_phase=2, max_phase=2)
    assert result.empty
    assert list(result.columns) == account.ROSTER_COLUMNS


def test_format_operator_data_empty_keeps_columns():
    assert list(format_operator_data({}).columns) == ['id', 'potential', 'level', 'phase', 'trust', 'skill_level']
