import pytest

from factories import make_enemy_data, make_operator_data


@pytest.fixture
def operator_data():
    return make_operator_data()


@pytest.fixture
def enemy_data():
    return make_enemy_data()
