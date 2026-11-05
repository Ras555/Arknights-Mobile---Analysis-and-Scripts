import pytest

from arkdata.calculations.skill_rotation import (
    ASSUMED_ATTACKERS,
    ASSUMED_ENEMY_ATTACK_INTERVAL,
    SP_PER_SECOND,
    SkillRotation,
    SkillTrigger,
    SpType,
    hit_interval,
)


def test_constants():
    assert SP_PER_SECOND == 1.0
    assert (ASSUMED_ENEMY_ATTACK_INTERVAL, ASSUMED_ATTACKERS) == (3.0, 1)
    assert [member.value for member in SpType] == [
        'INCREASE_WITH_TIME', 'INCREASE_WHEN_ATTACK', 'INCREASE_WHEN_TAKEN_DAMAGE', '8']
    assert [member.value for member in SkillTrigger] == ['SP', 'ALWAYS', 'DEPLOYMENT', 'OTHER']


# hit_interval

def test_hit_interval_defaults_to_one_enemy_every_three_seconds():
    assert hit_interval() == 3.0


def test_hit_interval_divides_by_attackers():
    assert hit_interval(3) == 1.0
    assert hit_interval(2, 4.0) == 2.0
    assert hit_interval(0) == 3.0


# timed skills

def test_timed_skill_gaining_sp_over_time():
    # Exusiai S3 at mastery 3: 30 SP over time, active for 15 s, so a 45 s cycle active a third of the time.
    rotation = SkillRotation('INCREASE_WITH_TIME', 30, 15, attack_interval=1.0)
    assert (rotation.charge_time, rotation.cycle_time) == (30, 45)
    assert rotation.uptime == pytest.approx(1 / 3)
    # (15 s * 3000 + 30 s * 600) / 45 s = 1400
    assert rotation.average_dps(600, 3000) == pytest.approx(1400)
    assert not rotation.unlimited and not rotation.instant


def test_timed_skill_gaining_sp_per_attack():
    # 10 SP at one attack per 1.5 s = 15 s to charge, then 10 s active: 25 s cycle.
    rotation = SkillRotation(SpType.PER_ATTACK, 10, 10, attack_interval=1.5)
    assert (rotation.charge_time, rotation.cycle_time, rotation.uptime) == (15, 25, 0.4)
    assert rotation.average_dps(100, 200) == pytest.approx((10 * 200 + 15 * 100) / 25)


def test_average_with_no_active_dps_uses_base_dps():
    assert SkillRotation('INCREASE_WITH_TIME', 20, 10, 1.0).average_dps(500) == pytest.approx(500)


# instant skills

def test_instant_skill_that_replaces_an_attack_gaining_sp_per_attack():
    # Ch'en S3 at mastery 3: 30 SP per attack at 1.2 s per attack = 36 s, plus the skill attack itself
    # (which gains no SP) = 37.2 s cycle. Extra damage 10 * 320% ATK of 1000 ATK = 32000 over a normal attack.
    rotation = SkillRotation('INCREASE_WHEN_ATTACK', 30, 0, attack_interval=1.2)
    assert rotation.instant and rotation.uptime == 0
    assert rotation.cycle_time == pytest.approx(37.2)
    assert rotation.average_dps(1000 / 1.2, burst_damage=32000 - 1000) == pytest.approx(1000 / 1.2 + 31000 / 37.2)


def test_instant_extra_strike_gaining_sp_per_attack_takes_no_extra_attack():
    rotation = SkillRotation('INCREASE_WHEN_ATTACK', 20, 0, attack_interval=1.0, replaces_attack=False)
    assert rotation.cycle_time == 20
    assert rotation.average_dps(100, burst_damage=4000) == pytest.approx(300)


def test_instant_skill_gaining_sp_over_time_keeps_charging():
    rotation = SkillRotation('INCREASE_WITH_TIME', 8, 0, attack_interval=1.6)
    assert rotation.cycle_time == 8
    assert rotation.average_dps(200, burst_damage=800) == pytest.approx(300)


def test_instant_skill_with_zero_cost_returns_active_dps():
    rotation = SkillRotation('INCREASE_WITH_TIME', 0, 0, attack_interval=1.0)
    assert rotation.cycle_time == 0
    assert rotation.uptime == 0
    assert rotation.average_dps(100, 250) == 250


# skills that stay on, passives, and unknown cycles

def test_unlimited_skill_averages_its_active_dps():
    rotation = SkillRotation('INCREASE_WITH_TIME', 70, None, attack_interval=1.2)
    assert rotation.unlimited and not rotation.instant
    assert (rotation.cycle_time, rotation.uptime) == (None, 1.0)
    assert rotation.charge_time == 70
    assert rotation.average_dps(500, 1000) == 1000


def test_always_on_passive_is_always_active():
    rotation = SkillRotation(8, 0, None, attack_interval=1.2, trigger=SkillTrigger.ALWAYS)
    assert rotation.unlimited and not rotation.instant
    assert (rotation.charge_time, rotation.cycle_time, rotation.uptime) == (0.0, None, 1.0)
    assert rotation.average_dps(100, 120) == 120
    # Always on whatever the duration says.
    assert SkillRotation(8, 0, 0, 1.2, trigger=SkillTrigger.ALWAYS).unlimited


# deployment and other triggers

def test_timed_deployment_skill_is_left_out_of_the_average():
    # Projekt Red S1 at mastery 3: ATK +80% for 10 s after each deployment, so none of a long fight.
    rotation = SkillRotation(8, 0, 10, attack_interval=1.0, trigger=SkillTrigger.DEPLOYMENT)
    assert not rotation.unlimited and not rotation.instant
    assert (rotation.charge_time, rotation.cycle_time, rotation.uptime) == (None, None, 0.0)
    assert rotation.average_dps(500, 900) == 500


def test_one_off_deployment_hit_is_left_out_of_the_average():
    rotation = SkillRotation(8, 0, 0, attack_interval=1.0, trigger=SkillTrigger.DEPLOYMENT)
    assert rotation.instant
    assert rotation.uptime == 0.0
    assert rotation.average_dps(500, burst_damage=5000) == 500


def test_deployment_skill_lasting_until_retreat_is_always_active():
    rotation = SkillRotation(8, 0, None, attack_interval=1.0, trigger=SkillTrigger.DEPLOYMENT)
    assert rotation.unlimited
    assert (rotation.uptime, rotation.average_dps(500, 700)) == (1.0, 700)


def test_skill_started_by_something_else_has_no_cycle():
    rotation = SkillRotation(8, 0, 20, attack_interval=1.0, trigger=SkillTrigger.OTHER)
    assert not rotation.unlimited
    assert (rotation.charge_time, rotation.cycle_time, rotation.uptime) == (None, None, None)
    assert rotation.average_dps(100, 200) is None
    assert not SkillRotation(8, 0, None, 1.0, trigger=SkillTrigger.OTHER).unlimited


# SP gained when hit

def test_sp_gained_when_hit_needs_a_hit_interval():
    rotation = SkillRotation('INCREASE_WHEN_TAKEN_DAMAGE', 5, 0, attack_interval=1.2)
    assert (rotation.charge_time, rotation.cycle_time, rotation.uptime) == (None, None, None)
    assert rotation.average_dps(100, burst_damage=500) is None


def test_sp_gained_when_hit_with_one_attacker():
    # Liskarm S2 at mastery 3: 34 SP at one hit every 3.0 s = 102 s to charge, then 20 s active.
    rotation = SkillRotation('INCREASE_WHEN_TAKEN_DAMAGE', 34, 20, attack_interval=1.7, hit_interval=hit_interval())
    assert (rotation.charge_time, rotation.cycle_time) == (102, 122)
    assert rotation.uptime == pytest.approx(20 / 122)
    assert rotation.average_dps(100, 400) == pytest.approx((20 * 400 + 102 * 100) / 122)


def test_sp_gained_when_hit_with_a_full_block():
    # The same skill with 3 blocked enemies attacking: one hit per second, 34 s to charge.
    rotation = SkillRotation('INCREASE_WHEN_TAKEN_DAMAGE', 34, 20, attack_interval=1.7, hit_interval=hit_interval(3))
    assert (rotation.charge_time, rotation.cycle_time) == (34, 54)


def test_instant_skill_gaining_sp_when_hit_takes_no_extra_attack():
    # Hung S1: the next attack heals instead; the healing attack still lets SP charge from hits.
    rotation = SkillRotation('INCREASE_WHEN_TAKEN_DAMAGE', 4, 0, attack_interval=1.2, hit_interval=3.0)
    assert rotation.cycle_time == 12
    assert rotation.average_dps(100, burst_damage=240) == pytest.approx(120)


def test_unknown_sp_type_has_no_cycle():
    assert SkillRotation('SOMETHING_NEW', 5, 10, 1.0).average_dps(100, 200) is None


def test_sp_type_given_as_enum_or_string_behaves_the_same():
    assert SkillRotation(SpType.OVER_TIME, 30, 15, 1.0) == SkillRotation('INCREASE_WITH_TIME', 30, 15, 1.0)
