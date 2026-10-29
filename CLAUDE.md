# Arknights Data Analysis

## Project Description

Arknights Damage and Stat Calculation is a set of utility scripts for the Arknights mobile game. The program retrieves the
player's operator roster from the Arknights (Yostar EN) servers, retrieves operator, stage and enemy game data from the
HellaAPI, and calculates each operator's stats and damage per second (DPS). It then compares those operators against the
enemies in a chosen stage to show how much damage each operator deals to, and takes from, each enemy, making it easier
to decide which operators to bring to a stage.

## Context

### Arknights Servers

The player's account data is retrieved from the Arknights (Yostar EN) servers with the arkprts library. Logging in uses an
email code: the account email is stored in the USER_EMAIL environment variable, a login code is emailed to it, and arkprts
asks for the code in the console. Of importance to this program are each operator's char_id, potential_rank, level,
evolve_phase, favor_point (raw trust points, converted to a trust percentage) and main_skill_lvl.

### HellaAPI

> An Arknights EN game data API. Data is fetched from HellaAssets and official game servers, lightly massaged into a nicer format, and stored in a MongoDB database. Made with Express and self-hosted (RIP Cyclic). Also an under construction personal project.

The project can be downloaded from git and hosted locally, or accessed from the hosted https://awedtan.ca site.

Various game data can be accessed from the api, but of particular interest are the operator, stage, and enemy info.

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
                            "requiredPotentialRank": _requiredPotential_,
                            "name": _talentName_,
                            "description": _talentDescription_,
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
and spawn counts in a stage) and matchups.py (operator vs. enemy DPS and kill times).
- sources/: hella_api.py (the HellaAPI client) and account.py (arkprts login and roster retrieval).

### src/arknights_calc

arknights_calc is this program. It uses arkdata and adds everything specific to this program:
- cli.py: the command line (`arknights-calc`, or `python -m arknights_calc` through __main__.py).
- settings.py: load_settings() returns a Settings object (account email and folder paths). Nothing is loaded at import
time; settings are created once in cli.main() and passed to whatever needs them.
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
elite2_operators.csv and operator_stats_output.csv. The ref folder is gitignored, since it holds personal account data.

### io Folder

The io folder stores outputs designed to be viewed by the user: operator_stats_output.csv (a copy of the one in ref), and
the stage_{stage_id}_enemy_stats.csv and stage_{stage_id}_matchups.csv files. Files may be placed in both ref and io.
The io folder is gitignored, since its files are regenerated on every run.

### tests Folder

The tests folder stores the pytest suite, split into tests/arkdata and tests/arknights_calc to mirror the packages. The
tests use fake game data built by tests/factories.py and never call the HellaAPI or the Arknights servers.

### Other

Auxiliary files such as the .gitignore, README.md, CLAUDE.md, pyproject.toml (dependencies, the `arknights-calc` command,
and pytest and mutmut configuration) and .env file aren't sorted in any folder, and are stored in the main folder of the
project. Logs are written to the logs
folder.


## Version Control

### Repository

The project is stored in a private GitHub repository, Ras555/Arknights-Mobile---Analysis-and-Scripts, with the remote
named origin and main as the default branch.

The following are gitignored and must never be committed: the .env file (account email), .venv, the logs folder, the ref
and io folders (personal account data and generated outputs), Homecoming Operator Comparison.xlsx, and cache folders
such as __pycache__, .pytest_cache and mutants. Once committed, a file stays in the repository history even if it is
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
matchups, or all (which runs every step in order). The matchups step also accepts a --stage argument with the stage id.

### Initialization

Upon initialization the program should perform the following tasks:
- Load the settings (account email from .env, and the ref, io and logs folder paths) with load_settings()
- Set up logging to standard output and to logs/arknights.log
- Create a Storage for the ref and io folders and pass it to each step

### Roster Step

1. Log into the Arknights servers with the email in USER_EMAIL.
2. Retrieve the player's operator roster and convert each operator's favor points into a trust percentage. Favor points
above the maximum (25570) log an error and are capped at a trust of 200.
3. Retrieve the player's raw account data and save it to ref/raw_data.json.
4. Filter the roster to Elite 2 (phase 2) operators and save them to ref/elite2_operators.csv.

### Stats Step

1. Read the roster from ref/elite2_operators.csv.
2. For each operator, retrieve its game data from the HellaAPI and calculate its stats at its current phase, level,
potential and trust, its basic attack damage type and its DPS. Operators that fail are logged and skipped. The damage
type is looked up by operator id in OPERATOR_DAMAGE_TYPE_OVERRIDES, then by sub profession (archetype) in
SUB_PROFESSION_DAMAGE_TYPES (both in src/arkdata/calculations/operator_stats.py). If the archetype isn't listed, a warning is logged and the
damage type is guessed from the operator's trait description; the archetype should then be added to the table.
3. Save the results to ref/operator_stats_output.csv and io/operator_stats_output.csv.

### Matchups Step

1. Read the operator stats from ref/operator_stats_output.csv. If the file is missing any of the columns the matchups
need (REQUIRED_OPERATOR_COLUMNS in src/arkdata/calculations/matchups.py), log an error listing them and asking for the stats step
to be rerun.
2. Ask for a stage id if one wasn't given, retrieve the stage from the HellaAPI, and ask which variant to use if the stage
has more than one.
3. Retrieve the stats of each enemy in the stage's enemyDbRefs at its level (skipping stage-defined enemies) and count how
many of each enemy the stage's waves spawn.
4. For every operator and enemy pair, calculate the DPS each deals to the other and the time each takes to kill the other.
5. Save the enemy stats and matchups to io/stage_{stage_id}_enemy_stats.csv and io/stage_{stage_id}_matchups.csv, and
print the enemy stats and DPS tables.
