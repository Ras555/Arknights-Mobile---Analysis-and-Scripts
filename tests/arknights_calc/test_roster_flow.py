import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from arknights_calc.flows.roster import RosterProcessor, check_roster_age
from arknights_calc.storage import Storage


def _char(char_id, phase, level=1):
    return SimpleNamespace(char_id=char_id, potential_rank=1, level=level, evolve_phase=phase,
                           favor_point=10070, main_skill_lvl=7)


class FakeNetwork:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeArkClient:
    def __init__(self, chars, raw, fail_on=None):
        self.chars = chars
        self.raw = raw
        self.fail_on = fail_on
        self.network = FakeNetwork()

    async def get_data(self):
        if self.fail_on == 'get_data':
            raise RuntimeError('server error')
        return SimpleNamespace(troop=SimpleNamespace(chars=self.chars))

    async def get_raw_data(self):
        return self.raw


def _make_processor(tmp_path, client, email='me@example.com'):
    logins = []

    async def fake_login(user_email):
        logins.append(user_email)
        return client

    processor = RosterProcessor(email, Storage(tmp_path / 'ref', tmp_path / 'io'), login=fake_login)
    return processor, logins


def test_run_saves_raw_data_and_elite2_roster(tmp_path, capsys):
    client = FakeArkClient({'1': _char('char_a', 2, 40), '2': _char('char_b', 1), '3': _char('char_c', 2)}, {'user': {'id': 1}})
    processor, logins = _make_processor(tmp_path, client)

    result = asyncio.run(processor.run())

    assert logins == ['me@example.com']
    assert client.network.closed
    assert result['id'].tolist() == ['char_a', 'char_c']
    with open(tmp_path / 'ref' / 'raw_data.json') as f:
        assert json.load(f) == {'user': {'id': 1}}
    saved = pd.read_csv(tmp_path / 'ref' / 'elite2_operators.csv')
    assert saved['id'].tolist() == ['char_a', 'char_c']
    assert list(saved.columns) == ['id', 'potential', 'level', 'phase', 'trust', 'skill_level']
    assert 'char_a' in capsys.readouterr().out


def test_run_closes_network_on_error(tmp_path):
    client = FakeArkClient({}, {}, fail_on='get_data')
    processor, _ = _make_processor(tmp_path, client)
    with pytest.raises(RuntimeError):
        asyncio.run(processor.run())
    assert client.network.closed
    assert not os.path.exists(tmp_path / 'ref' / 'raw_data.json')


@pytest.mark.parametrize('email', ['', None])
def test_run_without_email_raises(tmp_path, email):
    processor, logins = _make_processor(tmp_path, FakeArkClient({}, {}), email=email)
    with pytest.raises(ValueError, match=r'^No account email set\. Add USER_EMAIL to the \.env file\.$'):
        asyncio.run(processor.run())
    assert logins == []


def test_run_keeps_only_phase_2_operators(tmp_path):
    client = FakeArkClient({'1': _char('char_a', 2), '2': _char('char_b', 3), '3': _char('char_c', 1)}, {})
    processor, _ = _make_processor(tmp_path, client)
    assert asyncio.run(processor.run())['id'].tolist() == ['char_a']


def test_run_twice_overwrites_existing_files(tmp_path):
    processor, _ = _make_processor(tmp_path, FakeArkClient({'1': _char('char_a', 2)}, {'run': 1}))
    asyncio.run(processor.run())
    processor, _ = _make_processor(tmp_path, FakeArkClient({'1': _char('char_b', 2)}, {'run': 2}))
    asyncio.run(processor.run())
    with open(tmp_path / 'ref' / 'raw_data.json') as f:
        assert json.load(f) == {'run': 2}
    assert pd.read_csv(tmp_path / 'ref' / 'elite2_operators.csv')['id'].tolist() == ['char_b']


def test_raw_data_saved_with_4_space_indent(tmp_path):
    processor, _ = _make_processor(tmp_path, FakeArkClient({'1': _char('char_a', 2)}, {'user': {'id': 1}}))
    asyncio.run(processor.run())
    assert (tmp_path / 'ref' / 'raw_data.json').read_text() == json.dumps({'user': {'id': 1}}, indent=4)


def test_elite2_csv_saved_without_index_column(tmp_path):
    processor, _ = _make_processor(tmp_path, FakeArkClient({'1': _char('char_a', 2)}, {}))
    asyncio.run(processor.run())
    assert (tmp_path / 'ref' / 'elite2_operators.csv').read_text().splitlines()[0] == 'id,potential,level,phase,trust,skill_level'


def test_run_prints_roster_without_index(tmp_path, capsys):
    processor, _ = _make_processor(tmp_path, FakeArkClient({'1': _char('char_a', 2)}, {}))
    asyncio.run(processor.run())
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ['id', 'potential', 'level', 'phase', 'trust', 'skill_level']
    assert lines[1].split()[0] == 'char_a'


def test_elite_2_phase_constant():
    from arknights_calc.flows.roster import ELITE_2_PHASE
    assert ELITE_2_PHASE == 2


def test_run_with_empty_roster_saves_empty_csv_with_headers(tmp_path):
    processor, _ = _make_processor(tmp_path, FakeArkClient({}, {}))
    assert asyncio.run(processor.run()).empty
    assert (tmp_path / 'ref' / 'elite2_operators.csv').read_text().splitlines() == ['id,potential,level,phase,trust,skill_level']


# check_roster_age

class FakeRosterStorage:
    def __init__(self, saved_at):
        self.saved_at = saved_at

    def roster_saved_at(self):
        return self.saved_at


def test_check_roster_age_without_roster_returns_none(caplog):
    with caplog.at_level(logging.WARNING):
        assert check_roster_age(FakeRosterStorage(None), timedelta(days=7)) is None
    assert caplog.records == []


def test_check_roster_age_within_limit_is_quiet(caplog):
    now = datetime(2026, 11, 2, 12)
    with caplog.at_level(logging.WARNING):
        assert check_roster_age(FakeRosterStorage(now - timedelta(days=7)), timedelta(days=7), now) == timedelta(days=7)
    assert caplog.records == []


def test_check_roster_age_past_limit_warns(caplog):
    now = datetime(2026, 11, 2, 12)
    saved_at = datetime(2026, 10, 24, 9)
    with caplog.at_level(logging.WARNING):
        assert check_roster_age(FakeRosterStorage(saved_at), timedelta(days=7), now) == now - saved_at
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(
        logging.WARNING,
        'Your roster was last updated 9 days ago (2026-10-24). Run `arknights-calc roster` to refresh it.',
    )]


def test_check_roster_age_defaults_to_now():
    assert check_roster_age(FakeRosterStorage(datetime.now() - timedelta(hours=1)), timedelta(days=7)) >= timedelta(hours=1)


def test_run_records_roster_save_time(tmp_path):
    processor, _ = _make_processor(tmp_path, FakeArkClient({'1': _char('char_a', 2)}, {}))
    before = datetime.now()
    asyncio.run(processor.run())
    info = json.loads((tmp_path / 'ref' / 'roster_info.json').read_text())
    assert info['operator_count'] == 1
    assert before <= datetime.fromisoformat(info['saved_at']) <= datetime.now()
