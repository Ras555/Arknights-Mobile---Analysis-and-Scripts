import pytest

from arkdata.calculations.attack_targets import Targets
from arkdata.calculations.blackboard import BlackboardValue, Target
from arkdata.calculations.damage import DamageType
from arkdata.calculations.operator_profile import build_operator_profile, build_summon_profile
from arkdata.calculations.skills import DamagePart, StatModifiers
from arknights_calc.reports import (
    SUMMARY_COLUMNS,
    deployed_by,
    describe_modifiers,
    describe_part,
    describe_values,
    duration_type,
    join,
    number,
    operator_page,
    skill_rows,
    summary_row,
    summon_stats_row,
    talent_rows,
    unlock_text,
)
from factories import load_operator_fixture, make_operator_value, make_skill_entry, make_skill_level, make_talent_candidate


def _skill(description, blackboard=None, **kwargs):
    return make_skill_entry(make_skill_level(description, blackboard, **kwargs), skill_id=f'sk_{len(description)}')


def _profile(*skills, talents=(), potential=0, **kwargs):
    # ATK 640, attack interval 1.0 s at phase 2 level 90 (make_operator_value).
    value = make_operator_value(skills=skills, talents=talents, **kwargs)
    return build_operator_profile(value, 2, 90, potential, 0, 1), value


# Formatting helpers

def test_join():
    assert join(['a', 'b', 3]) == 'a; b; 3'
    assert join([]) == ''


@pytest.mark.parametrize('value, expected', [
    (None, '-'), (640, '640'), (0.5, '0.5'), (12848.333, '12,848.33'), (2.0, '2'), (-0.001, '0'), (-1.5, '-1.5'),
    # Rounded like the io/ tables (numpy), not Python's formatting (1.07, 2.67).
    (1.075, '1.08'), (2.675, '2.68'),
])
def test_number(value, expected):
    assert number(value) == expected


def test_describe_modifiers():
    modifiers = StatModifiers(atk_percent=0.06, atk_flat=50, def_percent=0.075, hp_percent=0.13, res_flat=13,
                              attack_speed=8, attack_interval_change=-0.11, block=1)
    assert describe_modifiers(modifiers) == ('ATK +6%, ATK +50, DEF +7.5%, Max HP +13%, RES +13, ASPD +8, '
                                             'attack interval -0.11 s, block +1')
    assert describe_modifiers(StatModifiers()) == ''


def test_describe_values():
    values = [BlackboardValue('atk', 'talent@atk', -0.22, True, Target.ENEMY),
              BlackboardValue('damage_scale', 'damage_scale', 1.32, True, Target.ENEMY)]
    assert describe_values(values) == 'atk -0.22; damage_scale 1.32'


@pytest.mark.parametrize('part, expected', [
    (DamagePart(DamageType.PHYSICAL, 2.9), 'Physical 290% ATK'),
    (DamagePart(DamageType.ARTS, 2.2, 2, Targets(3, 3.0, True, 'AoE (no target limit)')),
     'Arts 220% ATK × 2 hits, AoE (no target limit)'),
    (DamagePart(DamageType.TRUE, 0.5, 1, Targets(), True), 'True 50% ATK every second'),
])
def test_describe_part(part, expected):
    assert describe_part(part) == expected


def test_unlock_text():
    assert unlock_text(make_talent_candidate(phase='PHASE_2', level=1, potential=4)) == 'Elite 2 level 1, potential 5'
    assert unlock_text(make_talent_candidate(phase='PHASE_1', level=30)) == 'Elite 1 level 30'
    assert unlock_text({}) == 'Elite 0 level 1'


# Rows

@pytest.mark.parametrize('skill, expected', [
    (_skill('ATK +{atk:0%}', {'atk': 0.5}, duration=20), 'timed'),
    (_skill('The next attack deals {atk_scale:0%} ATK', {'atk_scale': 2.0}, duration=0), 'instant'),
    (_skill('ATK +{atk:0%}\nUnlimited duration', {'atk': 0.5}, duration=-1), 'stays on'),
    (_skill('ATK +{atk:0%}', {'atk': 0.5, 'trigger_time': 8}, duration=0, duration_type='AMMO'), 'ammo'),
    (_skill('After deployment, ATK +{atk:0%}', {'atk': 0.5}, skill_type='PASSIVE', sp_type='8', duration=-1),
     'until retreat'),
    (_skill('ATK +{atk:0%}', {'atk': 0.5}, skill_type='PASSIVE', sp_type='8', duration=-1), 'always on'),
])
def test_duration_type(skill, expected):
    profile, _ = _profile(skill)
    assert duration_type(profile.skills[0]) == expected


def test_summary_row():
    profile, _ = _profile(_skill('ATK +{atk:0%}', {'atk': 0.5}, sp_cost=40, duration=20, name='Boost'))
    row = summary_row(profile, ['Drone', 'Turret'])
    assert list(row) == [*SUMMARY_COLUMNS, 'summons']
    assert (row['min_dps'], row['max_dps'], row['max_dps_skill'], row['modelled_status'], row['summons']) == (
        640, 960, 'Boost', 'full', 'Drone; Turret')
    assert row['min_hps'] is None


def test_skill_rows():
    profile, _ = _profile(_skill('The next attack deals {atk_scale:0%} ATK and stuns for {stun} s',
                                 {'atk_scale': 3.0, 'stun': 1, 'mystery': 2}, sp_type='INCREASE_WHEN_ATTACK', sp_cost=4,
                                 duration=0, name='Strike'))
    [row] = skill_rows(profile)
    assert (row['operator_id'], row['skill_name'], row['duration_type'], row['damage']) == (
        'char_test', 'Strike', 'instant', 'Physical 300% ATK')
    assert (row['dps'], row['burst'], row['avg_dps']) == (640, 1280, pytest.approx(640 + 1280 / 5))
    assert (row['counts_for_max'], row['status'], row['unmodelled'], row['notes']) == (
        False, 'partial', 'mystery', 'stuns for 1 s')
    assert row['hps'] is None and 'owner_id' not in row


def test_skill_rows_for_a_healer_and_a_summon():
    profile, _ = _profile(_skill('Heals with {heal_scale:0%} ATK', {'heal_scale': 1.5}, sp_cost=20, duration=10),
                          profession='MEDIC', sub_profession='physician')
    summon = build_summon_profile(make_operator_value(operator_id='token_a', name='Drone',
                                                      skills=[_skill('ATK +{atk:0%}', {'atk': 1.0}, duration=10)]),
                                  2, 90, 0, 0, 1)
    assert skill_rows(profile)[0]['hps'] == 960
    [row] = skill_rows(summon, owner=profile)
    assert (row['operator_id'], row['owner_id'], row['owner_name']) == ('token_a', 'char_test', 'Test Operator')


def test_talent_rows_unlocked_not_unlocked_and_hidden():
    talents = [
        [make_talent_candidate('ATK +5%', {'atk': 0.05}, name='Sharp'),
         make_talent_candidate('ATK +10%', {'atk': 0.1}, phase='PHASE_2', name='Sharp')],
        [make_talent_candidate('DEF +10%', {'def': 0.1}, phase='PHASE_2', potential=4, name='Tough')],
        [make_talent_candidate('Secret', {'atk': 0.1}, hidden=True)],
        [make_talent_candidate('', {'atk': 0.1})],
        [make_talent_candidate('When attacking, has a 20% chance to deal 150% damage', {'prob': 0.2, 'atk_scale': 1.5},
                               name='Lucky')],
    ]
    profile, value = _profile(talents=talents)
    rows = talent_rows(profile, value['data'])
    assert [(row['talent_number'], row['talent_name'], row['unlocked'], row['unlocks_at']) for row in rows] == [
        (1, 'Sharp', True, 'Elite 2 level 1'), (2, 'Tough', False, 'Elite 2 level 1, potential 5'),
        (5, 'Lucky', True, 'Elite 0 level 1')]
    assert (rows[0]['group'], rows[0]['applied'], rows[0]['modifiers'], rows[0]['status']) == (
        'ALWAYS', True, 'ATK +10%', 'full')
    assert rows[1]['description'] == 'DEF +10%' and 'group' not in rows[1]
    assert (rows[2]['factor'], rows[2]['damage_multiplier']) == (0.2, pytest.approx(1.1))


def test_talent_rows_without_talents():
    profile, value = _profile()
    assert talent_rows(profile, value['data']) == []


def test_summon_rows():
    owner, owner_value = _profile(_skill('a'), _skill('bb'), tokens=['token_a'], override_tokens={2: 'token_b'})
    summon = build_summon_profile(make_operator_value(operator_id='token_a', name='Drone'), 2, 90, 0, 0, 1)
    row = summon_stats_row(summon, owner, owner_value)
    assert (row['summon_id'], row['owner_id'], row['deployed_by'], row['atk'], row['min_dps']) == (
        'token_a', 'char_test', 'talent or any skill', 640, 640)
    assert 'summons' not in row and row['assumed'] == ''
    assert deployed_by(owner_value, 'token_b') == 'skill 2'


# Operator pages

def test_operator_page_with_skills_talents_and_summons():
    talents = [[make_talent_candidate('When attacking, has a 20% chance to deal 150% damage, ATK +10%',
                                      {'prob': 0.2, 'atk_scale': 1.5, 'atk': 0.1}, name='Lucky')],
               [make_talent_candidate('Enemies in range have RES -10%', {'magic_resistance': -0.1}, name='Hex')],
               [make_talent_candidate('DEF +10%', {'def': 0.1}, potential=4, name='Tough')]]
    skills = [_skill('The next attack deals {atk_scale:0%} ATK to nearby enemies', {'atk_scale': 3.0},
                     sp_type='INCREASE_WHEN_TAKEN_DAMAGE', sp_cost=10, duration=0, name='Strike'),
              _skill('ATK +{atk:0%}', {'atk': 1.0}, sp_cost=40, duration=20, name='Boost')]
    profile, value = _profile(*skills, talents=talents, tokens=['token_a'])
    drone = make_operator_value(operator_id='token_a', name='Drone')
    drone['data']['potentialRanks'] = []
    summon = build_summon_profile(drone, 2, 90, 5, 0, 1)
    page = operator_page(profile, value['data'], [summon], skill_level=7)
    assert page.startswith('# Test Operator\n\nElite 2 level 90, potential 1, trust 0%, skill level 7.\n')
    for text in ('| DPS per target | 774.4 |', '| Skill used | none | Boost | Boost |',
                 '### S1: Strike (level 1)', '- 10 SP (INCREASE_WHEN_TAKEN_DAMAGE), starting at 0; instant.',
                 '- Each activation adds', '- Average with a full block attacking:',
                 '### S2: Boost (level 1)', '- Average over its 60 s cycle:',
                 '### T1: Lucky', '- Effects: ATK +10%, damage × 1.1.', '- Scaled by 0.2 (its chance and/or average stacks).',
                 '- Group: LIKELY, applied. This version unlocks at Elite 0 level 1.',
                 '### T2: Hex', '- On enemies: magic_resistance -0.1.',
                 '### T3: Tough', '- Not unlocked yet: unlocks at Elite 0 level 1, potential 5.',
                 '## Summons', '### Drone', "- no potential bonuses listed, so the owner's potential isn't used"):
        assert text in page, text
    assert page.endswith('\n') and not page.endswith('\n\n')


def test_operator_page_for_a_healer_without_skills_or_talents():
    profile, value = _profile(profession='MEDIC', sub_profession='physician')
    page = operator_page(profile, value['data'])
    assert '| Healing per second | 640 | 640 | 640 |' in page
    assert 'No skills unlocked.' in page and 'No talents.' in page
    assert 'skill level' not in page.splitlines()[2] and '## Summons' not in page


def test_operator_page_notes_assumptions_and_different_total_skills():
    stacking = [make_talent_candidate('Each attack gives ATK +1%, stacking up to 10 times', {'atk': 0.01,
                                                                                              'max_stack_cnt': 10})]
    skills = [_skill('ATK +{atk:0%}', {'atk': 1.0, 'mystery': 1}, sp_cost=10, duration=10, name='Solo'),
              _skill('Attacks hit nearby enemies', {}, sp_cost=10, duration=10, name='Spread'),
              _skill('Heals allies with {heal_scale:0%} ATK and stuns for {stun} s', {'heal_scale': 1.0, 'stun': 1},
                     sp_cost=10, duration=10, name='Care')]
    profile, value = _profile(*skills, talents=[stacking])
    page = operator_page(profile, value['data'])
    assert 'In total (over every enemy hit), the best average uses Spread and the best max uses Spread.' in page
    assert '## Assumptions\n\n- stacking talents averaged over 60 s with no skill' in page
    assert '- Not modelled: mystery' in page and '- Note: stuns for 1 s' in page


def test_operator_page_real_kaltsit():
    kaltsit = load_operator_fixture('kaltsit')
    profile = build_operator_profile(kaltsit, 2, 90, 5, 100, 7, (3, 3, 3))
    page = operator_page(profile, kaltsit['data'])
    assert "# Kal'tsit" in page and '| Healing per second | 180.7 |' in page
    assert '- Healing: ' in page and '### T2: Non-Damaging Restructuring' in page
