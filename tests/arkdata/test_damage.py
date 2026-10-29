import pytest

from arkdata.calculations.damage import damage_per_hit, damage_per_second, MINIMUM_DAMAGE_RATIO


def test_minimum_damage_ratio_is_five_percent():
    assert MINIMUM_DAMAGE_RATIO == 0.05


def test_physical_damage_subtracts_defense():
    assert damage_per_hit(500, 'PHYSICAL', 200, 50) == 300


def test_physical_damage_ignores_resistance():
    assert damage_per_hit(500, 'PHYSICAL', 0, 100) == 500


def test_physical_damage_floors_at_five_percent_of_atk():
    assert damage_per_hit(500, 'PHYSICAL', 1000, 0) == pytest.approx(25)


def test_physical_damage_at_exact_floor_boundary():
    # 500 - 475 = 25, which equals the 5% floor.
    assert damage_per_hit(500, 'PHYSICAL', 475, 0) == pytest.approx(25)
    assert damage_per_hit(500, 'PHYSICAL', 474, 0) == pytest.approx(26)


def test_arts_damage_reduced_by_resistance_percentage():
    assert damage_per_hit(500, 'ARTS', 1000, 20) == pytest.approx(400)


def test_arts_damage_ignores_defense():
    assert damage_per_hit(500, 'ARTS', 9999, 0) == pytest.approx(500)


def test_arts_damage_floors_at_five_percent_of_atk():
    assert damage_per_hit(500, 'ARTS', 0, 99) == pytest.approx(25)


def test_arts_resistance_clamped_to_100():
    assert damage_per_hit(500, 'ARTS', 0, 150) == pytest.approx(25)


def test_arts_negative_resistance_clamped_to_0():
    assert damage_per_hit(500, 'ARTS', 0, -30) == pytest.approx(500)


def test_true_damage_ignores_defense_and_resistance():
    assert damage_per_hit(500, 'TRUE', 9999, 100) == 500


@pytest.mark.parametrize('damage_type', ['HEAL', 'NONE', 'physical', ''])
def test_other_damage_types_deal_no_damage(damage_type):
    assert damage_per_hit(500, damage_type, 0, 0) == 0.0


def test_damage_per_second_divides_hit_damage_by_interval():
    assert damage_per_second(500, 2.0, 'PHYSICAL', 100, 0) == pytest.approx(200)


def test_damage_per_second_with_arts():
    assert damage_per_second(400, 1.6, 'ARTS', 0, 50) == pytest.approx(125)


def test_damage_per_second_zero_interval_raises():
    with pytest.raises(ZeroDivisionError):
        damage_per_second(500, 0, 'TRUE', 0, 0)


def test_damage_type_members():
    from arkdata.calculations.damage import DamageType
    assert [member.value for member in DamageType] == ['PHYSICAL', 'ARTS', 'TRUE', 'HEAL', 'NONE']


def test_damage_type_compares_equal_to_plain_strings():
    from arkdata.calculations.damage import DamageType
    assert DamageType.ARTS == 'ARTS'
    assert str(DamageType.ARTS) == 'ARTS'
    assert damage_per_hit(500, DamageType.ARTS, 0, 20) == damage_per_hit(500, 'ARTS', 0, 20)
