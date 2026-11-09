#File Documentation
'''
File: reports.py
Description: Turns operator profiles (from arkdata's operator_profile.py)
    into this program's outputs: rows for the operator stats, skills,
    talents and summon tables, and a readable Markdown page per operator.
Created: 2026-11-05
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-05
Last Modified by: Claude
'''

# Imports

from collections.abc import Iterable
from dataclasses import fields
from typing import Any

import numpy as np

from arkdata.calculations.blackboard import BlackboardValue, strip_tags
from arkdata.calculations.damage import DamageType
from arkdata.calculations.operator_profile import OperatorProfile, SkillProfile, damage_sum, summon_ids
from arkdata.calculations.operator_stats import parse_phase
from arkdata.calculations.skill_rotation import SkillTrigger
from arkdata.calculations.skills import DamagePart, StatModifiers
from arkdata.calculations.talents import Talent, unlocked_candidate
from arknights_calc.storage import VIEW_DECIMALS

# Constants

# Joins list-like values (notes, assumptions, unmodelled values) in one table cell.
LIST_SEPARATOR = '; '

# How each StatModifiers field is written: (label, written as a percentage, unit).
MODIFIER_LABELS = {
    'atk_percent': ('ATK', True, ''),
    'atk_flat': ('ATK', False, ''),
    'def_percent': ('DEF', True, ''),
    'def_flat': ('DEF', False, ''),
    'hp_percent': ('Max HP', True, ''),
    'hp_flat': ('Max HP', False, ''),
    'res_percent': ('RES', True, ''),
    'res_flat': ('RES', False, ''),
    'attack_speed': ('ASPD', False, ''),
    'attack_interval_change': ('attack interval', False, ' s'),
    'block': ('block', False, ''),
}

# The summary columns added to the operator stats table, in order.
SUMMARY_COLUMNS = [
    'min_dps', 'avg_dps', 'max_dps', 'min_dps_total', 'avg_dps_total', 'max_dps_total',
    'avg_dps_skill', 'avg_dps_total_skill', 'max_dps_skill', 'max_dps_total_skill',
    'min_def', 'max_def', 'min_res', 'max_res', 'min_hp', 'max_hp', 'min_hps', 'avg_hps', 'max_hps',
    'modelled_status',
]

# The columns of the io/ copy of the operator stats table, in order. The ref/ copy keeps every stat in the game data,
# but many are the same for every operator's base stats (checked 2026-11-05 over all 445 operators:
# spRecoveryPerSec, maxDeployCount, maxDeckStackCnt, massLevel, baseForceLevel, hpRecoveryPerSec, moveSpeed and the
# immunity flags), so the view shows only the ones that differ or are used.
OPERATOR_STATS_VIEW_COLUMNS = [
    'operator_id', 'name', 'profession', 'sub_profession_id', 'rarity', 'level', 'phase', 'potential', 'trust',
    'maxHp', 'atk', 'def', 'magicResistance', 'attackSpeed', 'baseAttackTime', 'blockCnt', 'cost', 'respawnTime',
    'tauntLevel', 'damage_type', 'attack_interval', 'dps', *SUMMARY_COLUMNS, 'summons',
]


def join(items: Iterable) -> str:
    '''
    Returns items as one table cell, separated by LIST_SEPARATOR.
    '''
    return LIST_SEPARATOR.join(str(item) for item in items)


def number(value: float | None) -> str:
    '''
    Returns a number for reading: at most VIEW_DECIMALS (2) decimals,
    thousands separated ("12,848.33", "0.5", "640"), or "-" for None.
    Rounded the same way as the io/ tables (numpy's round, as pandas uses),
    so a value shows the same on a page as in a table (1.075 is 1.08 in
    both, where Python's own formatting gives 1.07).
    '''
    if value is None:
        return '-'
    text = f'{float(np.round(value, VIEW_DECIMALS)):,.{VIEW_DECIMALS}f}'.rstrip('0').rstrip('.')
    return '0' if text == '-0' else text


def describe_modifiers(modifiers: StatModifiers) -> str:
    '''
    Returns stat changes in words: "ATK +6%, DEF +60, ASPD +8".
    '''
    parts = []
    for item in fields(modifiers):
        value = getattr(modifiers, item.name)
        if not value:
            continue
        label, is_percent, unit = MODIFIER_LABELS[item.name]
        amount = f'{value * 100:+g}%' if is_percent else f'{value:+g}'
        parts.append(f'{label} {amount}{unit}')
    return ', '.join(parts)


def describe_values(values: Iterable[BlackboardValue]) -> str:
    '''
    Returns named values (e.g. enemy effects) as "atk -0.22; damage_scale 1.32".
    '''
    return join(f'{value.key} {value.value:g}' for value in values)


def describe_part(part: DamagePart) -> str:
    '''
    Returns a damage part in words: "Arts 220% ATK × 2 hits every second,
    up to 3 enemies".
    '''
    text = f'{part.damage_type.title()} {part.atk_scale:.0%} ATK'
    if part.hits != 1:
        text += f' × {part.hits:g} hits'
    if part.per_second:
        text += ' every second'
    if part.targets.note:
        text += f', {part.targets.note}'
    return text


def duration_type(skill_profile: SkillProfile) -> str:
    '''
    Returns how long a skill lasts, in words: "always on", "until retreat",
    "stays on", "instant", "ammo" or "timed".
    '''
    skill, rotation = skill_profile.skill, skill_profile.rotation
    if rotation.unlimited:
        if skill.trigger == SkillTrigger.ALWAYS:
            return 'always on'
        return 'until retreat' if skill.trigger == SkillTrigger.DEPLOYMENT else 'stays on'
    if rotation.instant:
        return 'instant'
    return 'ammo' if skill.ammo is not None else 'timed'


def summary_row(profile: OperatorProfile, summon_names: Iterable[str] = ()) -> dict:
    '''
    Returns the summary columns (SUMMARY_COLUMNS) of a profile, plus
    "summons": the names of the operator's summons.
    '''
    summary = profile.summary()
    row: dict[str, Any] = {column: getattr(summary, column) for column in SUMMARY_COLUMNS if column != 'modelled_status'}
    row['modelled_status'] = summary.status
    row['summons'] = join(summon_names)
    return row


def skill_rows(profile: OperatorProfile, owner: OperatorProfile | None = None) -> list[dict]:
    '''
    Returns one row per skill of a profile (the operator_skills and
    summon_skills tables). For a summon, owner adds the owner's id and name.
    Healing (hps, avg_hps) is only given for HEAL operators.
    '''
    rows = []
    for skill_profile in profile.skills:
        skill, rotation, stats = skill_profile.skill, skill_profile.rotation, skill_profile.stats
        burst, burst_total = damage_sum(skill_profile.burst)
        row: dict[str, Any] = {'operator_id': profile.operator_id, 'name': profile.name}
        if owner is not None:
            row.update({'owner_id': owner.operator_id, 'owner_name': owner.name})
        row.update({
            'skill_number': skill.number, 'skill_id': skill.skill_id, 'skill_name': skill.name,
            'skill_level': skill.level, 'mastery': skill.mastery, 'skill_type': skill.skill_type, 'sp_type': skill.sp_type,
            'sp_cost': skill.sp_cost, 'initial_sp': skill.initial_sp, 'trigger': skill.trigger,
            'duration_type': duration_type(skill_profile),
            'duration': rotation.duration, 'ammo': skill.ammo,
            'description': strip_tags(skill.description),
            'damage': join(describe_part(part) for part in skill.damage_parts),
            'dps': skill_profile.dps, 'dps_total': skill_profile.dps_total, 'burst': burst, 'burst_total': burst_total,
            'avg_dps': skill_profile.average_dps, 'avg_dps_total': skill_profile.average_dps_total,
            'full_block_avg_dps': skill_profile.full_block_average_dps,
            'full_block_avg_dps_total': skill_profile.full_block_average_dps_total,
            'uptime': rotation.uptime, 'cycle_time': rotation.cycle_time,
            'atk': stats.atk, 'def': stats.defense, 'res': stats.magic_resistance, 'hp': stats.max_hp,
            'attack_interval': stats.attack_interval,
            'hps': skill_profile.hps if profile.damage_type == DamageType.HEAL else None, 'avg_hps': skill_profile.average_hps,
            'counts_for_max': skill_profile.counts_for_max, 'status': str(skill.status),
            'unmodelled': join(skill.unmodelled), 'notes': join(skill.notes), 'assumed': join(skill.assumed),
        })
        rows.append(row)
    return rows


def unlock_text(candidate: dict) -> str:
    '''
    Returns what unlocks a talent version: "Elite 2 level 1, potential 5".
    The game counts potential from 1, so required rank 4 is potential 5.
    '''
    condition = candidate.get('unlockCondition') or {}
    text = f"Elite {parse_phase(condition.get('phase', 0))} level {condition.get('level', 1)}"
    rank = candidate.get('requiredPotentialRank') or 0
    return f'{text}, potential {rank + 1}' if rank else text


def talent_rows(profile: OperatorProfile, operator_data: dict) -> list[dict]:
    '''
    Returns one row per talent (the operator_talents table): the unlocked
    version's figures (unlocks_at is what unlocks that version), or, for a
    talent not unlocked yet, its first version's description and what
    unlocks it. Hidden talents and ones without a description are left out.

    Args:
        profile: The operator's profile (its talents are the unlocked ones).
        operator_data: The operator's HellaAPI data, for talents not unlocked.
    '''
    unlocked = {talent.number: talent for talent in profile.talents}
    rows = []
    for number, talent_data in enumerate(operator_data.get('talents') or [], start=1):
        candidates = talent_data.get('candidates') or []
        if number in unlocked:
            current = unlocked_candidate(talent_data, profile.phase, profile.level, profile.potential) or {}
            rows.append(_talent_row(profile, unlocked[number], unlock_text(current)))
            continue
        first = next((candidate for candidate in candidates
                      if not candidate.get('isHideTalent') and strip_tags(candidate.get('description')).strip()), None)
        if first is None or unlocked_candidate(talent_data, profile.phase, profile.level, profile.potential):
            # Hidden, or unlocked but without a description: build_talents left it out.
            continue
        rows.append({
            'operator_id': profile.operator_id, 'name': profile.name, 'talent_number': number,
            'talent_name': first.get('name') or '', 'unlocked': False, 'unlocks_at': unlock_text(first),
            'description': strip_tags(first.get('description')),
        })
    return rows


def _talent_row(profile: OperatorProfile, talent: Talent, unlocks_at: str) -> dict:
    '''
    Returns the row of an unlocked talent.
    '''
    return {
        'operator_id': profile.operator_id, 'name': profile.name, 'talent_number': talent.number,
        'talent_name': talent.name, 'unlocked': True, 'unlocks_at': unlocks_at, 'description': talent.description,
        'group': str(talent.group), 'condition': talent.condition or '', 'applied': talent.applied,
        'factor': talent.factor, 'modifiers': describe_modifiers(talent.modifiers),
        'damage_multiplier': talent.damage_multiplier, 'enemy_effects': describe_values(talent.enemy_effects),
        'status': talent.status, 'overridden': talent.overridden, 'unmodelled': join(talent.unmodelled),
        'notes': join(talent.notes), 'assumed': join(talent.assumed),
    }


def deployed_by(owner_value: dict, summon_id: str) -> str:
    '''
    Returns what deploys a summon: "skill 3", or "talent or any skill" when
    it isn't tied to one skill.
    '''
    number = summon_ids(owner_value).get(summon_id)
    return f'skill {number}' if number else 'talent or any skill'


def summon_stats_row(summon: OperatorProfile, owner: OperatorProfile, owner_value: dict) -> dict:
    '''
    Returns a summon's row (the summon_stats table): who owns and deploys
    it, its level, its stats with no skill, and its summary columns.
    '''
    stats = summon.base_stats
    row: dict[str, Any] = {
        'summon_id': summon.operator_id, 'name': summon.name, 'owner_id': owner.operator_id, 'owner_name': owner.name,
        'deployed_by': deployed_by(owner_value, summon.operator_id), 'phase': summon.phase, 'level': summon.level,
        'potential': summon.potential, 'trust': summon.trust, 'damage_type': summon.damage_type,
        'atk': stats.atk, 'def': stats.defense, 'res': stats.magic_resistance, 'hp': stats.max_hp,
        'attack_interval': stats.attack_interval, 'block': stats.block,
    }
    row.update(summary_row(summon))
    del row['summons']
    row['assumed'] = join(summon.assumed)
    return row


def operator_page(profile: OperatorProfile, operator_data: dict, summons: Iterable[OperatorProfile] = (),
                  skill_level: int | None = None) -> str:
    '''
    Returns a readable Markdown page about an operator: its summary
    figures, stats, skills, talents and summons, and what was assumed or
    couldn't be modelled.

    Args:
        profile: The operator's profile.
        operator_data: The operator's HellaAPI data (for talents not unlocked).
        summons: The profiles of its summons.
        skill_level: The operator's skill level, shown at the top.
    '''
    summary = profile.summary()
    lines = [f'# {profile.name}', '']
    level_text = f'Elite {profile.phase} level {profile.level}, potential {profile.potential + 1}, trust {number(profile.trust)}%'
    if skill_level is not None:
        level_text += f', skill level {skill_level}'
    lines += [level_text + '.', '',
              f'Archetype {profile.sub_profession_id}, {profile.damage_type.lower()} attacks. '
              f'Modelled: {summary.status}.', '']

    lines += ['## Summary', '', '| | Min | Average | Max |', '|---|---|---|---|',
              f'| DPS per target | {number(summary.min_dps)} | {number(summary.avg_dps)} | {number(summary.max_dps)} |',
              f'| DPS in total | {number(summary.min_dps_total)} | {number(summary.avg_dps_total)} | '
              f'{number(summary.max_dps_total)} |',
              f'| Skill used | none | {summary.avg_dps_skill or "none"} | {summary.max_dps_skill or "none"} |',
              f'| DEF | {number(summary.min_def)} | | {number(summary.max_def)} |',
              f'| RES | {number(summary.min_res)} | | {number(summary.max_res)} |',
              f'| HP | {number(summary.min_hp)} | | {number(summary.max_hp)} |']
    if summary.min_hps is not None:
        lines.append(f'| Healing per second | {number(summary.min_hps)} | {number(summary.avg_hps)} | '
                     f'{number(summary.max_hps)} |')
    lines.append('')
    if summary.avg_dps_total_skill != summary.avg_dps_skill or summary.max_dps_total_skill != summary.max_dps_skill:
        lines += [f'In total (over every enemy hit), the best average uses {summary.avg_dps_total_skill or "no skill"} '
                  f'and the best max uses {summary.max_dps_total_skill or "no skill"}.', '']

    stats = profile.base_stats
    lines += ['## Stats with no skill', '',
              f'ATK {number(stats.atk)}, DEF {number(stats.defense)}, RES {number(stats.magic_resistance)}, '
              f'HP {number(stats.max_hp)}, attack interval {number(stats.attack_interval)} s, block {number(stats.block)}. '
              'These include the talents that are applied.', '']

    lines += ['## Skills', '']
    if not profile.skills:
        lines += ['No skills unlocked.', '']
    for skill_profile in profile.skills:
        lines += _skill_section(skill_profile)

    lines += ['## Talents', '']
    rows = talent_rows(profile, operator_data)
    if not rows:
        lines += ['No talents.', '']
    for row in rows:
        lines += _talent_section(row)

    summons = list(summons)
    if summons:
        lines += ['## Summons', '']
        for summon in summons:
            summon_summary = summon.summary()
            lines += [f'### {summon.name}', '',
                      f'DPS per target: {number(summon_summary.min_dps)} min, {number(summon_summary.avg_dps)} average, '
                      f'{number(summon_summary.max_dps)} max. ATK {number(summon.base_stats.atk)}, '
                      f'DEF {number(summon.base_stats.defense)}, HP {number(summon.base_stats.max_hp)}. '
                      f'Modelled: {summon_summary.status}.', '']
            lines += [f'- {item}' for item in summon.assumed] + ([''] if summon.assumed else [])

    if profile.assumed:
        lines += ['## Assumptions', ''] + [f'- {item}' for item in profile.assumed] + ['']
    return '\n'.join(lines).rstrip('\n') + '\n'


def _skill_section(skill_profile: SkillProfile) -> list[str]:
    '''
    Returns the Markdown lines about one skill.
    '''
    skill, rotation = skill_profile.skill, skill_profile.rotation
    mastery = f' M{skill.mastery}' if skill.mastery else ''
    lines = [f'### S{skill.number}: {skill.name} (level {skill.level}{mastery})', '',
             strip_tags(skill.description).replace('\n', ' '), '']
    kind = duration_type(skill_profile)
    duration = f'{kind}, {number(rotation.duration)} s' if kind in ('timed', 'ammo') else kind
    sp = f'{number(skill.sp_cost)} SP ({skill.sp_type}), starting at {number(skill.initial_sp)}' if skill.trigger == \
        SkillTrigger.SP else f'started by {str(skill.trigger).lower()}'
    lines.append(f'- {sp}; {duration}.')
    if skill.damage_parts:
        lines.append(f'- Damage: {join(describe_part(part) for part in skill.damage_parts)}.')
    stats = skill_profile.stats
    lines.append(f'- While active: {number(skill_profile.dps)} DPS per target, {number(skill_profile.dps_total)} in total; '
                 f'ATK {number(stats.atk)}, DEF {number(stats.defense)}, RES {number(stats.magic_resistance)}, '
                 f'attack interval {number(stats.attack_interval)} s.')
    burst, burst_total = damage_sum(skill_profile.burst)
    if burst or burst_total:
        lines.append(f'- Each activation adds {number(burst)} damage per target, {number(burst_total)} in total.')
    if skill_profile.average_dps is not None:
        average = f'- Average over its {number(rotation.cycle_time)} s cycle: ' if rotation.cycle_time else '- Average: '
        lines.append(f'{average}{number(skill_profile.average_dps)} DPS per target, '
                     f'{number(skill_profile.average_dps_total)} in total.')
    if skill_profile.full_block_average_dps is not None:
        lines.append(f'- Average with a full block attacking: {number(skill_profile.full_block_average_dps)} DPS per target.')
    if skill_profile.average_hps is not None:
        lines.append(f'- Healing: {number(skill_profile.hps)} per second while active, '
                     f'{number(skill_profile.average_hps)} on average.')
    lines.append(f'- Modelled: {skill.status}.')
    lines += [f'- Note: {item}' for item in skill.notes]
    lines += [f'- Assumed: {item}' for item in skill.assumed]
    lines += [f'- Not modelled: {item}' for item in skill.unmodelled]
    return lines + ['']


def _talent_section(row: dict) -> list[str]:
    '''
    Returns the Markdown lines about one talent row (see talent_rows).
    '''
    lines = [f"### T{row['talent_number']}: {row['talent_name']}", '', row['description'], '']
    if not row['unlocked']:
        return lines + [f"- Not unlocked yet: unlocks at {row['unlocks_at']}.", '']
    applied = 'applied' if row['applied'] else 'not applied'
    condition = f" ({row['condition']})" if row['condition'] else ''
    lines.append(f"- Group: {row['group']}{condition}, {applied}. This version unlocks at {row['unlocks_at']}.")
    effects = [text for text in (row['modifiers'],) if text]
    if row['damage_multiplier'] != 1:
        effects.append(f"damage × {number(row['damage_multiplier'])}")
    if effects:
        lines.append(f"- Effects: {', '.join(effects)}.")
    if row['factor'] != 1:
        lines.append(f"- Scaled by {number(row['factor'])} (its chance and/or average stacks).")
    if row['enemy_effects']:
        lines.append(f"- On enemies: {row['enemy_effects']}.")
    lines.append(f"- Modelled: {row['status']}{', overridden' if row['overridden'] else ''}.")
    for key, label in (('notes', 'Note'), ('assumed', 'Assumed'), ('unmodelled', 'Not modelled')):
        lines += [f'- {label}: {item}' for item in row[key].split(LIST_SEPARATOR) if item]
    return lines + ['']
