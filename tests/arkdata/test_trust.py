import logging

import pytest

from arkdata.calculations.trust import calculate_trust, TRUST_MILESTONES


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
