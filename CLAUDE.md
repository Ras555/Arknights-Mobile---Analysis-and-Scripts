# Arknights Data Analysis

## Project Description

Arknights Damage and Stat Calculation is a set of utility scripts for the Arknights mobile game. The program retrieves the
player's operator roster from the Arknights (Yostar EN) servers, retrieves operator, stage, enemy and deployable game data from
the HellaAPI, and calculates each operator's stats and damage per second (DPS). It then compares those operators against the
enemies in a chosen stage to show how much damage each operator deals to, and takes from, each enemy, making it easier
to decide which operators to bring to a stage.

## Context

### Arknights Servers

The player's account data is retrieved from the Arknights (Yostar EN) servers with the arkprts library. Logging in uses an
email code: the account email is stored in the USER_EMAIL environment variable, a login code is emailed to it, and arkprts
asks for the code in the console. Of importance to this program are each operator's char_id, potential_rank, level,
evolve_phase, favor_point (raw trust points, converted to a trust percentage), main_skill_lvl, skills (each skill's
specialize_level, its mastery from 0 to 3) and default_skill_index (the selected skill, counting from 0; -1 without
skills).

Yostar allows only one login per account at a time: logging in from this program logs the player out of the game, and
logging into the game revokes this program's login token. A token lasts at most 30 days and, for a daily player, usually
only hours, so tokens are never stored. arkprts' login_with_email_code prints the token to the console, so the program
gets the token with get_token_from_email_code and logs in with login_with_token instead, never printing or saving it.

### HellaAPI

> An Arknights EN game data API. Data is fetched from HellaAssets and official game servers, lightly massaged into a nicer format, and stored in a MongoDB database. Made with Express and self-hosted (RIP Cyclic). Also an under construction personal project.

The project can be downloaded from git and hosted locally, or accessed from the hosted https://awedtan.ca site.

Various game data can be accessed from the api, but of particular interest are the operator, stage, enemy, and
deployable info.

#### Collections, Query Parameters and Update Metadata

- `api/{resource}` (e.g. https://awedtan.ca/api/operator) returns every document of that type as a list. All operators
are about 33 MB (445 documents), all enemies about 7 MB, and there are 832 deployables; the stage collection is much
larger.
- Every document has the top-level fields `_id`, `canon` (its main id, e.g. char_002_amiya), `keys` (every id and name
it can be looked up by), `meta` and `value` (the data shown in the sections below). `meta` holds `hash` (a fingerprint of
the document's content), `updatedIndex`, `createdIndex`, `created`, `updated` and `date`.
- `include={field}` / `exclude={field}` (repeatable) limit which fields are returned. Only top-level fields work: nested
paths such as `value.data.name` return an empty `value`. `?include=canon&include=keys&include=meta` lists a whole
collection's ids and content hashes cheaply (about 160 KB for all operators).
- `limit={n}` caps the number of documents. The `search` and `searchv2` endpoints search document values only; filters on
`meta` fields return nothing.
- `api/about` returns the latest HellaAPI data update, e.g. `[{"index": 102, "hash": "...", "date": 1793602532,
"message": "update: ..."}]`. index and hash change on every update.
- `updatedIndex` is not a reliable change marker: an update can rewrite every document in a collection (all operators
had updatedIndex 101 after one update), so changes are detected by comparing each document's `meta.hash` instead.

#### Operator Data

Data for a particular operator can be retrieved using a link of the format: https://awedtan.ca/api/operator/{operator_key}

where operator key is either the coded id for that operator or some version of the Operators printed name.

The important pieces of the expected format are as follows:

```
{
    "keys": [..._operator_keys_]
    "value": 
    {
        "id": _operator_id_,
        "archetype": _operator_archetype_,
        "data": 
        {
            "name": _operator_name_,
            "nationId": _operator_nation_,
            "groupId": _operator_group_,
            "tagList": [..._operator_tags_],
            "position": _operator_position_,
            "maxPotentialLevel": _operator_max_potential_,
            "rarity": _operator_rarity_,
            "profession": _operator_profession_,
            "subProfessionId": _operator_sub_profession_,
            "trait": 
            {
                "candidates": 
                [
                    ...{
                        "unlockCondition": 
                        {
                            "phase": _required_phase_,
                            "level": _required_level_
                        }
                        "requiredPotentialRank": _required_potential_,
                        "blackboard": 
                        [
                            ...{
                                "key": _blackboard_key_,
                                "value": _blackboard_value_,
                                "valueStr": _blackboard_value_str_
                            }
                        ]
                    }
                ]
            },
            "phases": # Index from 0 to maximum 2, representing PHASE_0 (Elite 0) to PHASE_2 (Elite 2)
            [
                ...{
                    "maxLevel": _phaseMaxLevel_,
                    "attributesKeyFrames": # The API spells this "attributesKeyFrames" (with an s)
                    [
                        {
                            "level": 1,
                            "data": {
                                "maxHp": _maxHP_,
                                "atk": _atk_,
                                "def": _def_,
                                "magicResistance": _magicResistance_,
                                "cost": _cost_,
                                "blockCnt": _blockCnt_,
                                "moveSpeed": _moveSpeed_,
                                "attackSpeed": _attackSpeed_,
                                "baseAttackTime": _baseAttackTime_,
                                "respawnTime": _respawnTime_,
                                "hpRecoveryPerSec": _hpRecoveryPerSec_,
                                "spRecoveryPerSec": _spRecoveryPerSec_,
                                "maxDeployCount": _maxDeployCount_,
                                "maxDeckStackCnt": _maxDeckStackCnt_,
                                "tauntLevel": _tauntLevel_,
                                "massLevel": _massLevel_,
                                "baseForceLevel": _baseForceLevel_,
                                "stunImmune": _stunImmune_,
                                "silenceImmune": _silenceImmune_,
                                "sleepImmune": _sleepImmune_,
                                "frozenImmune": _frozenImmune_,
                                "levitateImmune": _levitateImmune_,
                                "disarmedCombatImmune": _disarmedCombatImmune_,
                                "fearedImmune": _fearedImmune_,
                                "palsyImmune": _palsyImmune_,
                                "attractImmune": _attractImmune_,
                            }
                        },
                        {
                            "level": _phaseMaxLevel_,
                            "data":
                            {
                                #Same stats as shown above
                            }
                        }

                    ]
                }
            ],
            "skills": # Up to 3 skills, depending on operator rarity and phase
            [
                ...{
                    "skillId": _skill_id_,
                    "levelUpCostCond": #Costs and requirements for skill specializations, from 1 to 3
                    [
                        ...{
                            "unlockCond":
                            {
                                "phase": _requiredPhase_,
                                "level": _requiredLevel_
                            },
                            "lvlUpTime": _levelUpTimeSec_,
                            "levelUpCost": 
                            [
                                ...{
                                    "id": _itemId_,
                                    "count": _itemAmount_,
                                    "type": _itemType_
                                }
                            ]
                        }
                    ],
                    "unlockCond":
                    {
                        "phase": _requiredPhase_,
                        "level": _requiredLevel_
                    }
                }
            ],
            "talents":
            [
                ...{
                    "candidates": [
                        ...{
                            "unlockCondition": 
                            {
                                "phase": _requiredPhase_,
                                "level": _requiredLevel_
                            },
                            "requiredPotentialRank": _requiredPotential_, # 0-5, compared with the roster's potential (from 0)
                            "name": _talentName_,
                            "description": _talentDescription_, # numbers already filled in, unlike skill templates
                            "isHideTalent": _isHidden_,
                            "blackboard":
                            [
                                ...{
                                    "key": _blackboardKey_,
                                    "value": _blackboardValue_,
                                    "valueStr": _blackboardValueStr_
                                }
                            ]
                        }
                    ]
                }
            ],
            "potentialRanks": # One entry per potential upgrade, from potential 1 to maxPotentialLevel
            [
                ...{
                    ...
                    "buff": # null when the potential doesn't change stats (e.g. talent upgrades)
                    {
                        "attributes":
                        {
                            "attributeModifiers":
                            [
                                ...{
                                    "attributeType": _attributeType_, # e.g. MAX_HP, ATK, DEF, COST, RESPAWN_TIME
                                    "formulaItem": _formulaItem_, # ADDITION adds value to the stat
                                    "value": _value_
                                }
                            ]
                        }
                    }
                }
            ],
            "favorKeyFrames": # Trust stat bonus, scaling linearly between the two frames
            [
                {
                    "level": 0, # 0% trust, all bonuses are 0
                    "data": {...} # Same stats as attributesKeyFrames
                },
                {
                    "level": 50, # 100% trust, the maximum bonus (trust above 100% gives no more stats)
                    "data": {...}
                }
            ]
        }
    }
}
```


#### Operator Skill Levels

Each skill's levels are in the operator's `value.skills` list (beside `value.data`, matched to `data.skills` by skill
id). fetch_operator_value returns the whole value; fetch_operator_data returns only `data`.

```
"skills":
[
    ...{
        "excel":
        {
            "skillId": _skill_id_,
            "levels": # 10 levels: skill levels 1-7, then masteries 1-3 (7 for skills that can't be mastered)
            [
                ...{
                    "name": _skill_name_,
                    "description": _template_, # e.g. "ATK <@ba.vup>+{atk:0%}</>, DEF <@ba.vup>+{def:0%}</>"
                    "skillType": _type_, # MANUAL, AUTO or PASSIVE
                    "durationType": _duration_type_, # NONE or AMMO
                    "duration": _seconds_, # 0 for instant effects; -1 for unlimited/toggles AND some one-off effects
                    "spData": {"spType": _sp_type_, "spCost": _cost_, "initSp": _initial_sp_, ...},
                    # spType: INCREASE_WITH_TIME, INCREASE_WHEN_ATTACK, INCREASE_WHEN_TAKEN_DAMAGE, or 8 (passive)
                    "blackboard": [...{"key": _name_, "value": _number_}] # e.g. atk 0.7, attack@times 5
                }
            ]
        }
    }
]
```

The description template shows how each named value is used: `{atk:0%}` is a percentage, `{def}` a plain number,
`{-def}` the value negated, and the words around it say who it affects ("Targets' DEF -{-def}" lowers enemies' DEF,
"DEF -{-def:0%}" lowers the operator's own). About a quarter of values don't appear in the text at all. Names may have
an `attack@` prefix (ignored), and a few templates write names in capitals.

#### Deployable Info

Deployables are the units operators place or summon besides themselves: summons (e.g. Kal'tsit's Mon3tr), traps and
devices. They can be retrieved using a link of the format: https://awedtan.ca/api/deployable/{deployable_id} (e.g.
token_10002_kalts_mon3tr). Their documents have the same structure as operators: `value.data` holds name, profession
(TOKEN), subProfessionId, phases (with attributesKeyFrames), talents and so on, and `value.skills` holds each skill's
levels. fetch_deployable_data returns the whole value (data, skills and range), not just data, since the skills are
beside it.

#### Stage Info

Information for a particular stage, including map and enemy info, can be accessed using a link of the format: https://awedtan.ca/api/stage/{stage_id}

Below is a partial response from the api call:
```
{
    ...
    "value": 
    {
        [
            {
                "excel": 
                {
                    "stageType": _stageType_,
                    "difficulty": _stageDiff_,
                    "stageId": _stageId_,
                    "levelId": _levelId_,
                    "zoneId": _zoneId_,
                    "code": _stageCode_,
                    "name": _stageName_,
                    "description": _stageDescription_,
                    "expGain": _expGain_,
                    "goldGain": _moneyGain_, 
                    ...                
                }
                "levels": {
                    "options": {
                        "characterLimit": _deployLimit_,
                        "maxLifePoint": _lifePoints_,
                        "initialCost": _initialDP_,
                        "maxCost": _maxDP_,
                        "costIncreaseTime": _dpIncreaseInterval_,
                        ...
                    }
                    "mapData": {...}
                    "routes": [...]
                    "enemyDbRefs":
                    [
                        {
                            "useDb": _useDb_,
                            "id": _enemyId_,
                            "level": _enemyLevel_,
                            "overwrittenData": {...} # Optional stage-specific overrides, in the same {m_defined, m_value} format as the enemy's levels
                        },
                        ...
                    ],
                    "waves": [...]
                }
            }
        ]
    }
}
```

#### Enemy Info

Information on an enemy can be accessed using a link of the format: https://awedtan.ca/api/enemy/{enemy_id}

Where enemy_id corresponds to the id in enemyDbRefs in the stage info api. The enemy API returns exact stat values and traits of a particular enemy. When paired with the level in the enemyDbRefs from the stage info api, you can retrieve the exact stats of an enemy.

Level 0 holds the enemy's full data, while higher levels only define (m_defined = true) the fields that change, so any undefined field falls back to its level 0 value. If the stage's enemyDbRefs entry has overwrittenData, those values are applied last and only for that stage.

Below is an example of a partial response from the api:

```
{
    ...
    "value": 
    {
        "excel":
        {
            "enemyId": _enemyId_,
            "enemyIndex": _enemyIndex_,
            "name": _enemyName_,
            "enemyLevel": _enemyTier_,
            "description": _enemyDescription_,
            "abilityList":
            [
                {
                    "text": _abilityText_,
                    "textFormat": _abilityFormat_
                },
                ...
            ]
            "damageType": [...] # Can be "PHYSIC", "MAGIC", or "HEAL"
        },
        "levels":
        {
            "Value":
            [
                {
                    "level": _level_,
                    "enemyData":
                    {
                        "name": 
                        {
                            "m_defined": _isDef_,
                            "m_value": _enemyName_
                        },
                        "description": 
                        {
                            "m_defined": _isDef_,
                            "m_value": _enemyDescription_
                        },
                        "prefabKey": 
                        {
                            "m_defined": _isDef_,
                            "m_value": _enemyId_
                        },
                        "attributes": # All values have format {m_defined: _bool_, m_value: _value}. Stat only applies if m_defined is true.
                        {
                            "maxHp": ...,
                            "atk": ...,
                            "def": ...,
                            "magicResistance": ...,
                            "cost": ...,
                            "blockCnt": ...,
                            "moveSpeed": ...,
                            "attackSpeed": ...,
                            "baseAttackTime": ...,
                            "respawnTime": ...,
                            "hpRecoveryPerSec": ...,
                            "spRecoveryPerSec": ...,
                            "maxDeployCount": ...,
                            "massLevel": ...,
                            "baseForceLevel": ...,
                            "tauntLevel": ...,
                            "epDamageResistance": ...,
                            "epResistance": ...,
                            "damageHitratePhysical": ...,
                            "damageHitrateMagical": ...,
                            "stunImmune": ...,
                            "silenceImmune": ...,
                            "sleepImmune": ...,
                            "frozenImmune": ...,
                            "levitateImmune": ...,
                            "disarmedCombatImmune": ...,
                            "fearedImmune": ...
                        },
                        "applyWay": {
                            "m_defined": true,
                            "m_value": ... #Either MELEE, RANGED, NONE, or ALL
                        },
                        "motion": {
                            "m_defined": true,
                            "m_value": ... #Possible values: WALK or FLY
                        }
                        "enemyTags": {
                            "m_defined": true,
                            "m_value": [...] #String tags
                        },
                        "lifePointReduce": {
                            "m_defined": true,
                            "m_value": _lpReduce_
                        },
                        "levelType": {
                            "m_defined": true,
                            "m_value": _enemyTier_
                        },
                        "rangeRadius": {
                            "m_defined": _isDef_ # true for RANGED units, false for MELEE units,
                            "m_value": _range_ #Radius of range as a number of squares.
                        },
                        "numOfExtraDrops": {
                            "m_defined": _isDef_,
                            "m_value": _extraDrops_
                        },
                        "viewRadius": {
                            "m_defined": true,
                            "m_value": _viewRad_
                        },
                        "notCountInTotal": {
                            "m_defined": _isDef_,
                            "m_value": _value_
                        },
                        "talentBlackboard": _talentValues_,
                        "skills": _skills_,
                        "spData": _spData_
                    }
                }
                ...
            ]
        }
    }
}
```


## Calculations

### Stat Calculations

Within a given operators phase, the operators stats increase linearly with each level up, up to a maximum. For example, Amiya has an attack of 276 at Elite 0 (phase 0) level 1, and at the max level of 50 has an attack of 390. This means her attack stat
increases at a rate of (390 - 276)/(50 - 1) = 114/49 ≈ 2.327 per level (going from level 1 to 50 is 49 level ups, not 50). Therefore at level 30, she would have leveled up 29 times and have an attack stat of 114/49 * 29 + 276 ≈ 343.47.

### Damage Per Second

The time between attacks (the attack interval) depends on a unit's base attack time and attack speed, where attack speed is a percentage of the normal rate (100 = normal speed):
> AttackInterval = baseAttackTime / (attackSpeed / 100)

The raw damage dealt per second, before the target's DEF or RES, is then:
> DPS = ATK / AttackInterval

Against a specific target, ATK is replaced by the damage per hit after that target's DEF or RES (see below). This applies to both operators and enemies.

### Damage Dealt/Received

There are three main types of damage in Arknights: physical, arts and true damage. The difference between these three is what enemy stat reduces the damage taken. For physical damage, the damage taken is reduced by defence (or DEF, or def) as a flat amount. For Arts damage, the damage taken is reduced by Res (or Resistance or magicResistance) as a percentage. True damage is not reduced by any enemy stats.

Physical and arts hits always deal at least 5% of the attacker's ATK, no matter how high the defender's DEF or RES is:
> Physical damage per hit = max(ATK - DEF, 0.05 * ATK)
>
> Arts damage per hit = max(ATK * (1 - RES / 100), 0.05 * ATK), with RES limited to 0-100
>
> True damage per hit = ATK

Typically Casters, Supporters and other ranged units other than Snipers deal Arts damage in their base attacks while Snipers and most melee units deal Physical damage. However, some units have special traits that cause them to deal arts damage instead of physical damage. True damage is generally reserved for specific abilities or talents, and is much rarer than the other two.

Damage types are represented by the DamageType enum in src/arkdata/calculations/damage.py (PHYSICAL, ARTS, TRUE, HEAL, NONE). It is a StrEnum, so its members compare equal to the plain strings saved in CSV files.

### Skills

Skills are read from their HellaAPI data (see Operator Skill Levels) by src/arkdata/calculations/skills.py, with
blackboard.py reading the named values. The full plan for skills, talents and summons is in the project doc
`claude/skills-talents-plan.md`.
- Unlocked skills come from each skill's unlockCond (phase and level). The level used is skill level 1-7, then
masteries 1-3 (index = skill level - 1 + mastery; masteries only exist at skill level 7).
- Stat values (atk, def, max_hp, magic_resistance) that affect the operator itself become StatModifiers; whether they're
percentages comes from the template, or, for values not in the text, from their size (atk/def/max_hp up to 3, RES up
to 1, are percentages), which is recorded as an assumption. attack_speed is flat ASPD, base_attack_time is seconds
added to the attack interval, block_cnt extra block. Values that affect enemies are kept as enemy_effects (for
matchups), values that buff allies become notes.
- Damage: each atk_scale placeholder is one DamagePart, typed by the damage named after it ("410% of ATK Physical
damage and 410% of ATK Arts damage" is two parts), otherwise the operator's type. times is hits per attack,
max_target the target limit. "damage type changes to True" (and similar) changes the whole attack. A multiplier only
dealt to enemies that attack the unit (counter damage) isn't treated as an attack multiplier. Damage "every second" is
marked per_second.
- What starts a skill (Skill.trigger, a SkillTrigger): SP for normal skills. Passive skills (skillType PASSIVE, spType
8) are mostly not always on (8 of 54 are, checked 2026-11-04): "After deployment" wording (also "after Skadi is
deployed", "Upon deployment") makes them DEPLOYMENT skills, lasting their duration, the {duration} value from the text
when the data says -1, or until retreat, and "immediately deals" makes them a one-off hit. Passives with a duration
or that "spend" Coins, happen "when switching" or "rush" are OTHER (started by something else; flagged as assumed).
The rest are ALWAYS on.
- Duration: "Unlimited duration" and toggles ("Can switch between") stay on (duration None). "next attack" skills
and skills with a duration of 0 or less are instant (duration 0); instant skills either replace the next attack or are
an extra strike. Ammo skills (durationType AMMO) keep duration 0 and set ammo from trigger_time.
- Known values that don't affect DPS (stun, slow, DP, shift, taunt, regeneration, range and similar) become notes.
Unknown values are listed as unmodelled. A skill's status is full (everything understood, nothing assumed), partial,
or none (nothing could be modelled).

How many enemies an attack hits (src/arkdata/calculations/attack_targets.py), in order: the skill's max_target; AoE
wording in the clause with the skill's multiplier ("all enemies", "nearby enemies", "AOE"...), assumed to be 3 enemies;
otherwise the archetype's basic attack. AoE archetypes (Artilleryman, Splash/Blast Caster, Flinger, Reaper,
Spreadshooter, Ambusher, Fortress, Earthshaker) assume 3 enemies; Centurion, Crusher and Pusher hit as many as their
block count; Chain Casters jump between 4 enemies losing 15% per jump; Earthshakers splash 50% to the others.
Targets.damage_share is the sum of the damage each enemy takes (3.0 for an even AoE on 3, 2.0 for an Earthshaker).
Swordmasters hit twice and Flingers add a half-ATK shockwave; Phalanx Casters and Liberators don't attack outside
their skills.

### Skill Rotation

src/arkdata/calculations/skill_rotation.py models one cycle: charge SP, then the skill is active (SP doesn't charge
while it's active).
> Charge time = SP cost × 1 s (SP over time), SP cost × attack interval (SP per attack), or SP cost × seconds between
> hits taken (SP when hit, = enemy attack interval / attackers)
>
> Average DPS = (active DPS × duration + base DPS × charge time) / (charge time + duration)

Instant skills add their extra damage once per cycle (a per-attack "next attack" skill's cycle includes that attack,
which gains no SP). Skills that stay on and always-on passives average their active DPS. Deployment skills happen once
per deployment, so they're left out of the average (it's the base DPS), though a timed one still counts towards max
DPS. Skills started by something else (OTHER) have no average.

Skills that gain SP when hit (1 SP per hit taken, mostly Defenders) need an assumed rate of incoming attacks:
hit_interval() uses ASSUMED_ENEMY_ATTACK_INTERVAL (3.0 s, the median attack interval of the 1,313 attacking enemies in
the HellaAPI data) divided by the number of attackers. The main average DPS uses ASSUMED_ATTACKERS (1 enemy); the skill
details also show the rotation with a full block (block count attackers). Max DPS doesn't depend on this, since it's
the DPS while the skill is active.

### Talents

Talents are read by src/arkdata/calculations/talents.py, reusing apply_stat_value from skills.py for stat values.
- The version used is the last candidate whose phase and level the operator has reached and whose requiredPotentialRank
is at most its potential. Hidden talents (isHideTalent) and ones without a description are skipped.
- Talent descriptions have their numbers filled in, so each value is found by its number ("8%" for 0.08, "15" for 15,
"115%" or "15%" for a 1.15 multiplier), and the clause around it decides who it affects (words after the last comma
before it: enemies, allies, or the unit itself; a value followed by Fragile/Fragility is on enemies) and its group.
Prefixes (attack@, talent@) and variants (skadi2_t_2[atk][1].atk) are dropped from value names; a second copy of a
value with a different number is ignored and recorded as an assumption. Stat and damage values that aren't in the
description aren't plain bonuses (e.g. an "atk" of 1), so they're listed as unmodelled instead of applied.
- Groups (TalentGroup), checked in this order on each clause holding one of the talent's own effects (or its effects on
enemies when it has none; the least likely group wins): PER_ENEMY (aerial/enemy drones, unblocked, blocked, elite/leader/boss,
enemy HP ("enemies with 80% or more HP"), weight, or an enemy type such as "[Infected Creatures]", "Sarkaz enemies" or
"to Machine enemies", kept as the talent's condition), UNLIKELY (depends on allies, operator counts, tiles or the team),
LIKELY (after deployment, while the skill is active, HP thresholds, when
attacking/dealing damage, per attack, per kill, per Slow inflicted, every N seconds deployed...), and ALWAYS (no
condition words). Wording that no rule recognises, and effects picked at random, are UNLIKELY and flagged for review. ALWAYS and LIKELY talents are
applied to the operator's figures; PER_ENEMY ones only in matchups against enemies that fit; UNLIKELY ones not at all.
Every talent's effects are still worked out, whatever its group.
- Factor (what each value is multiplied by): a chance (prob, or the first of prob1, prob2...) of the talent's own
effect is its expected value (a 20% chance to deal 160% is a 1.12 multiplier). It only scales the effects in the part of
the clause (between commas) that mentions the chance ("ASPD +6, 15% chance to increase ATK to 150%" keeps the full ASPD),
or every effect when only the clause as a whole does. A dodge chance, or a chance of something not modelled, is a note. Stacking
effects use the average number of stacks over a window (one skill rotation) when they stack per attack, every N
attacks, or every N seconds; when the rate isn't known (per kill, per Slow) they assume half the maximum stacks
(UNKNOWN_STACK_FRACTION), flagged as assumed.
- damage_scale and atk_scale on the unit's attacks become damage_multiplier (1 + (m - 1) × factor). On enemies (Fragile,
Arts Fragility) they're enemy_effects, as are DEF/RES penetration values. atk_scale not on attacks (e.g. damage when
deploying), multipliers below 1 (e.g. "at least 33% of ATK") and splash-only multipliers are listed as unmodelled.
- Talent.status is full, partial or none, as for skills.

#### Talent Override File

src/arkdata/data/talent_conditions.csv (columns operator_id, talent, group, factor, note) overrides the automatic group
of a talent, and optionally its factor, when the wording rules get it wrong. talent is the talent's number from 1, group
a TalentGroup name (case doesn't matter), and an empty factor keeps the calculated one. The file ships with the package
(package-data in pyproject.toml) and is read once with importlib.resources by load_talent_overrides(); an unknown group
raises a ValueError. Overridden talents are marked overridden and get an "overridden" note (with the row's note).

### Operator Profiles

src/arkdata/calculations/operator_profile.py puts stats, talents and skills together:
`build_operator_profile(operator_value, phase, level, potential, trust, skill_level, masteries, operator_id)` returns an
OperatorProfile, and `OperatorProfile.summary()` a ProfileSummary (one row of figures for PR 3's outputs).
> ATK = (ATK + flat bonuses) × (1 + sum of ATK %), and the same for DEF, HP and RES
>
> attack interval = (base attack time + interval change) / (ASPD / 100), with ASPD kept between 20 and 600
>
> DPS per target = ATK × ATK multiplier × hits × talent damage multiplier / attack interval
>
> total DPS = DPS per target × Targets.damage_share

- Base stats come from OperatorStats (level, potential, trust). The applied talents (ALWAYS and LIKELY) are added, then
a skill's changes while it's active. Healing isn't multiplied by talent damage multipliers.
- Hits: a damage part with 1 hit uses the archetype's hits (a Swordmaster's 2), since only skills that change the number
of hits say so. Damage dealt every second isn't divided by the attack interval, and is added to the normal attack.
- Phalanx Casters and Liberators don't attack outside their skills (0 DPS with no skill), but do during them.
- Each skill gets a SkillProfile: stats and damage (one Damage per damage type, per target and in total) while active,
and its SkillRotation (charging at the attack interval with no skill; SP when hit at 1 attacker every
ASSUMED_ENEMY_ATTACK_INTERVAL, plus a full-block average with block count attackers).
  - Instant skills keep the normal attack's damage and add a burst per activation: the skill's strike minus the attack it
  replaces, its boosted next attack, or an extra strike. An instant skill with no damage and no next attack (e.g.
  "Blocks the next source of damage") adds nothing.
  - "Stops attacking" leaves only damage every second. Ammo skills last ammo × their attack interval.
  - Skills that can only be used once per battle are left out of average and max DPS (noted as assumed).
- Stacking talents: the no-skill figures average them over DEFAULT_STACK_WINDOW (60 s, noted as assumed); each skill
averages them over its own rotation (its cycle time), or 60 s for skills that stay on. Talents are built twice each time:
first to find the attack interval (their ASPD changes it), then with it.
- Summary: min DPS is with no skill; max DPS is the best active skill that lasts (timed, until retreat, ammo, always on,
timed deployment; not instant, OTHER or once-per-battle skills); average DPS is the best rotation average, or no skill if
none is better (so deployment skills never name the average). Each comes per target and in total, naming its skill ("" for
none). Min/max DEF, RES and HP use the same skills as max DPS. Healing (min/avg/max HPS) is only given for HEAL
operators. status combines every skill's and talent's status (full, partial, none).
- Skill levels: a skill that doesn't have the wanted level or mastery (a summon's 1-level skill, or one without masteries)
uses its nearest level, noted as assumed.
- Not modelled: modules, operator talents and skills that buff their own summons (Kal'tsit's skills that buff Mon3tr
are read as her own changes), SP gained from talents, and the chance in a skill's own wording ("15% chance to increase
ATK to 200%").

#### Summons

- `summon_ids(operator_value)` lists an operator's summons from data.displayTokenDict and each skill's overrideTokenKey
(some are only in one of them), with the number of the skill that deploys each (or None).
- `build_summon_profile(...)` profiles a summon at its owner's phase and level. It gets the owner's potential or trust
only if its own data lists stat bonuses for them; none does (checked 2026-11-04: 797 deployables have neither, and the
35 with trust key frames list 0 for every stat), so they're 0, noted as assumed.
- A summon's skills line up with its owner's by slot (Skadi the Corrupting Heart's Seaborn has one skill for each of
hers; Nearl the Radiant Knight's sword only has skill 3, for her S3), so skill N uses the owner's skill level and skill N
mastery. Summon skills are counted whenever they'd be active on their own; in game, skill N is only used while the
owner's skill N is (noted as assumed). Some summons list their skills as null (Mon3tr).
- `build_summon_profiles(operator_value, deployables, ...)` builds every summon an operator has from a {deployable id:
value} mapping, logging and skipping missing ones (SilverAsh the Reignfrost's token_10057_svash2_eagle).


## Project Structure

The project has several folders used to organize files.

The code is an installable Python package with two top-level packages under src/: arkdata, a generic Arknights library,
and arknights_calc, this program. The project is installed once with `pip install -e .[dev]` (an editable install, so
code changes take effect without reinstalling), which also installs the `arknights-calc` command.

### src/arkdata

arkdata is a generic Arknights library that any Arknights tool could use. It knows nothing about this program, its
files, or its command line, and must never import from arknights_calc (tests/arknights_calc/test_package_boundaries.py
checks this).
- calculations/: damage.py (DamageType, damage formulas), operator_stats.py, enemy_stats.py, trust.py, stage.py (enemies
and spawn counts in a stage), matchups.py (operator vs. enemy DPS and kill times), and for skills (see Calculations):
blackboard.py (a skill's named values and readable text), skills.py (Skill, StatModifiers, DamagePart, and
apply_stat_value(s), shared with talents), skill_rotation.py (SkillRotation), attack_targets.py (Targets), talents.py
(Talent, TalentGroup, the talent override file) and operator_profile.py (OperatorProfile, ProfileSummary, summons).
- data/: data files that ship with the package: talent_conditions.csv (see Talent Override File).
- sources/: hella_api.py (HellaAPIClient: the HellaAPI client, with timeouts and retries), hella_cache.py (HellaCache:
the disk cache in front of the client, see Program Flow) and account.py (arkprts login and roster retrieval).

### src/arknights_calc

arknights_calc is this program. It uses arkdata and adds everything specific to this program:
- cli.py: the command line (`arknights-calc`, or `python -m arknights_calc` through __main__.py).
- settings.py: load_settings() returns a Settings object (account email, folder paths, HellaAPI client and cache
settings, and the roster age limit). Nothing is loaded at import time; settings are created once in cli.main() and
passed to whatever needs them. Optional .env settings: HELLA_API_URL, HELLA_CACHE_DAYS (default 14) and
ROSTER_MAX_AGE_DAYS (default 7).
- logging_setup.py: logging to the console and logs/arknights.log.
- storage.py: the Storage class reads and writes every file in ref/ and io/, so file names, formats and required columns
are defined in one place.
- flows/: one module per step of the program (roster.py, stats.py and matchups.py). Flows only orchestrate: they load
inputs through Storage, call arkdata, save outputs through Storage, and print results.

### Imports

Every module and test imports project code by its full package name (e.g.
`from arkdata.calculations.damage import damage_per_second`, `from arknights_calc.storage import Storage`). sys.path is
never modified; the editable install makes both packages importable. mutmut strips the leading `src.` from module names,
so its names match these imports.

### ref Folder

The ref folder stores outputs designed to store information for use by the program or for future runs: raw_data.json,
roster.csv (every operator in the roster), roster_info.json (when the roster was saved), operator_stats_output.csv, and
the HellaAPI cache in ref/cache (state.json, and per resource an index.json plus one JSON file per document). The ref folder is gitignored,
since it holds personal account data.

### io Folder

The io folder stores outputs designed to be viewed by the user: roster.csv and operator_stats_output.csv (copies of the
ones in ref), and the stage_{stage_id}_enemy_stats.csv and stage_{stage_id}_matchups.csv files. Files may be placed in both ref and io.
The io folder is gitignored, since its files are regenerated on every run.

### tests Folder

The tests folder stores the pytest suite, split into tests/arkdata and tests/arknights_calc to mirror the packages. The
tests use fake game data built by tests/factories.py and never call the HellaAPI or the Arknights servers.
tests/fixtures/skills and tests/fixtures/talents hold trimmed real HellaAPI skill and talent data, and
tests/fixtures/operators trimmed whole operators and summons (Blaze, Exusiai, Projekt Red, Liskarm, Kal'tsit and Mon3tr)
for operator profiles, with their expected figures worked out by hand in the tests (each file names its source). They're
loaded with load_skill_fixture, load_talent_fixture and load_operator_fixture; make_operator_value builds a fake operator.

### Other

Auxiliary files such as the .gitignore, README.md, CLAUDE.md, pyproject.toml (dependencies, the `arknights-calc` command,
and pytest, mutmut, ruff and mypy configuration), .pre-commit-config.yaml and .env file aren't sorted in any folder, and
are stored in the main folder of the project. The GitHub Actions workflow is in .github/workflows/ci.yml. Logs are written
to the logs folder.


## Version Control

### Repository

The project is stored in a private GitHub repository, Ras555/Arknights-Mobile---Analysis-and-Scripts, with the remote
named origin and main as the default branch.

The following are gitignored and must never be committed: the .env file (account email), .venv, the logs folder, the ref
and io folders (personal account data and generated outputs), Homecoming Operator Comparison.xlsx, and cache folders
such as __pycache__, .pytest_cache, .mypy_cache, .ruff_cache and mutants. Once committed, a file stays in the repository history even if it is
ignored later, so check `git status` before every commit.

### Git Configuration

The commit author (user.name and user.email) is set in this repository's own .git/config, not with `git config --global`.
The same computer also has work repositories (Azure DevOps) that use a different identity, so global git settings must not
be changed. core.autocrlf is enabled, so files have Windows (CRLF) line endings on disk and Unix (LF) line endings in the
repository.

### Workflow

- Changes are made on a separate branch (e.g. `feature/<short-description>`), never committed directly to main.
- Each branch is reviewed (`git diff main...<branch>`) and approved by the project owner before it is merged into main.
- Code changes still require explicit approval before they are made, as described in the project instructions.
- Committing, pushing, merging, and anything that rewrites history (force push, rebase of pushed commits, reset) are only
done when the project owner asks for them.
- Commit messages should briefly describe what changed and why.


## Principles

### Testing and Verification

In order to reduce debugging costs and ensure the program functionality remains verified, a test suite should be created
and maintained in the tests folder. The test suite should cover boundary conditions and invalid inputs as well. Mutation
testing should also be implemented to ensure the test suite maintains a high coverage. Mutation testing is configured in
pyproject.toml; lines containing `logger.info(` are excluded from mutation since only their text would change, while
warning, error, print and raise lines are still mutated.

### Tooling

Development tools are pinned in the `dev` extra of pyproject.toml and configured there too:
- ruff (lint only, never used to reformat code, so the code keeps its own layout): pycodestyle (E, W), pyflakes (F),
import sorting (I), bugbear (B), pyupgrade (UP) and simplify (SIM) rules, with a 130 character line limit.
- mypy: type checks src/ (not the tests), with pandas-stubs for pandas types.
- pytest and mutmut, as described above.

.pre-commit-config.yaml runs ruff (with safe auto-fixes) and mypy on every commit, using the tools installed in the
.venv. .github/workflows/ci.yml runs ruff, mypy and pytest on every push and pull request, and mutation testing after
every push to main (or on demand). All code must pass ruff check, mypy and pytest before it is merged into main.

### Logging

Errors and important events should be recorded in logs and printed to standard output to ensure the program is behaving
as it should and make it easier to trace back errors that occur. Interactive prompts and result tables meant for the user
are printed directly rather than logged.

### Modularization

Whenever possible the program should reuse functions from arkdata rather than writing code again. Code that would be
useful to any Arknights tool belongs in arkdata; code specific to this program's steps, files or output belongs in
arknights_calc.
Flow classes take their dependencies (API fetch functions, login, user input) as arguments so they can be replaced with
fakes in tests.


## Program Flow

The program is run with `arknights-calc <step>` (or `python -m arknights_calc <step>`), where the step is roster, stats,
matchups, or all (which runs every step in order). The matchups step also accepts a --stage argument with the stage id,
and --refresh downloads all HellaAPI data again instead of only what changed.

### Initialization

Upon initialization the program should perform the following tasks:
- Load the settings (account email from .env, and the ref, io and logs folder paths) with load_settings()
- Set up logging to standard output and to logs/arknights.log
- Create a Storage for the ref and io folders and pass it to each step

The stats and matchups steps (when run on their own) first log a warning suggesting `arknights-calc roster` if the saved
roster is older than ROSTER_MAX_AGE_DAYS. They never log in by themselves.

### HellaAPI Cache Sync

Before the stats and matchups steps (once per run), the HellaAPI cache in ref/cache is brought up to date:
1. Fetch `api/about`. If its index and hash match the update the cache was last synced to, and the last full check was
within HELLA_CACHE_DAYS, stop: everything is read from the cache.
2. Otherwise, for operators, enemies and deployables: on the first run, download the whole collection. After that, fetch the
collection's listing (canon, keys and meta only) and compare each document's meta.hash with the cache. Download changed
and new documents one by one, or the whole collection if more than 40 changed, and delete removed ones.
A collection with no index yet (e.g. deployables, the first time the program runs after they were added) is downloaded
even if the HellaAPI update hasn't changed.
3. For stages, which are cached one at a time as they're used: if any are cached, compare them against the stage
listing and drop the changed ones, so they are downloaded again the next time they're used.
4. Record the update in state.json. If any document failed to download, the old copy is kept and the update isn't
recorded, so the next run checks again.

`--refresh` downloads every operator, enemy and deployable again and drops all cached stages. If the HellaAPI can't be reached, a
warning is logged and the program uses what's cached. Failed requests are retried up to 3 times (waiting 1 s, then 2 s)
when the failure may be temporary (timeouts, dropped connections, HTTP 429 and 5xx).

### Roster Step

1. Log into the Arknights servers with the email in USER_EMAIL (without printing or saving the login token).
2. Retrieve the player's operator roster and convert each operator's favor points into a trust percentage. Favor points
above the maximum (25570) log an error and are capped at a trust of 200. The roster columns are id, potential, level,
phase, trust, skill_level, skill_1_mastery to skill_3_mastery (blank for skills the operator doesn't have) and
selected_skill (1 to 3, blank without skills).
3. Retrieve the player's raw account data and save it to ref/raw_data.json.
4. Save every operator in the roster to ref/roster.csv and io/roster.csv, recording when and how many in
ref/roster_info.json, and print how many operators are at each Elite level (phase).

### Stats Step

1. Read the roster from ref/roster.csv. If there isn't one yet, log an error asking for the roster step to be run first.
(Rosters saved before 2026-11-03 were saved as ref/elite2_operators.csv, which is no longer read; rerun the roster step.)
If the roster has no skill mastery columns (saved before they were added), log a warning suggesting the roster step be
rerun, and carry on.
2. For each operator in the roster (at any phase), retrieve its game data from the HellaAPI cache and calculate its stats
at its current phase, level, potential and trust, its basic attack damage type and its DPS. Operators that fail are logged and skipped. The damage
type is looked up by operator id in OPERATOR_DAMAGE_TYPE_OVERRIDES, then by sub profession (archetype) in
SUB_PROFESSION_DAMAGE_TYPES (both in src/arkdata/calculations/operator_stats.py). If the archetype isn't listed, a warning is logged and the
damage type is guessed from the operator's trait description; the archetype should then be added to the table. Every
archetype in the HellaAPI data as of 2026-11-03 is listed. Basic attacks are physical unless the archetype's trait says
otherwise.
3. Save the results to ref/operator_stats_output.csv and io/operator_stats_output.csv.

### Matchups Step

1. Read the operator stats from ref/operator_stats_output.csv. If the file doesn't exist yet, log an error asking for
the stats step to be run first. If it is missing any of the columns the matchups
need (REQUIRED_OPERATOR_COLUMNS in src/arkdata/calculations/matchups.py), log an error listing them and asking for the stats step
to be rerun. The phase column is also required.
2. Keep only the Elite 2 (phase 2) operators, so the matchup tables stay a readable size. If there are none, log a
warning and stop.
3. Ask for a stage id if one wasn't given, retrieve the stage from the HellaAPI cache, and ask which variant to use if the stage
has more than one.
4. Retrieve the stats of each enemy in the stage's enemyDbRefs (from the HellaAPI cache) at its level (skipping stage-defined enemies) and count how
many of each enemy the stage's waves spawn.
5. For every operator and enemy pair, calculate the DPS each deals to the other and the time each takes to kill the other.
6. Save the enemy stats and matchups to io/stage_{stage_id}_enemy_stats.csv and io/stage_{stage_id}_matchups.csv, and
print the enemy stats and DPS tables.
