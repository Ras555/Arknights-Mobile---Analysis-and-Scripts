#File Documentation
'''
File: account.py
Description: Logs into the Arknights (Yostar EN) servers with arkprts,
    retrieves a player's operator roster, and converts/filters that roster.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import logging
from collections.abc import Mapping

import arkprts
import pandas as pd

from arkdata.calculations.trust import calculate_trust

logger = logging.getLogger(__name__)

# Constants

# Mastery (specialization) level of each of an operator's skills, in skill
# order: 0 = not mastered, up to 3. Blank when the operator has fewer skills.
MASTERY_COLUMNS = ['skill_1_mastery', 'skill_2_mastery', 'skill_3_mastery']
# The roster's skill columns: the masteries, plus which skill (1-3) is
# selected for deployment. Blank for operators without skills.
SKILL_COLUMNS = [*MASTERY_COLUMNS, 'selected_skill']
# Columns of the roster DataFrame built by format_operator_data.
ROSTER_COLUMNS = ['id', 'potential', 'level', 'phase', 'trust', 'skill_level', *SKILL_COLUMNS]


async def login(email) -> arkprts.Client:
    '''
    Logs into the Yostar EN server with an email code (arkprts sends the code
    to the email and asks for it in the console) and returns an authenticated
    client.

    Gets the login token and logs in with it as two steps, rather than using
    arkprts' login_with_email_code, which prints the account's login token to
    the console. The token gives full access to the account, so it is never
    printed or stored.

    Tokens also aren't saved for reuse: Yostar allows one login per account
    at a time, so logging into the game revokes any token saved here.

    Args:
        email: The email address of the Yostar account.

    Returns:
        An arkprts.Client logged into that account.
    '''
    auth = arkprts.YostarAuth("en")
    # stdin=True makes arkprts prompt for the emailed code in the console.
    channel_uid, token = await auth.get_token_from_email_code(email, stdin=True)
    await auth.login_with_token(channel_uid, token)
    # assets=False skips downloading the game's asset data, which this
    # program doesn't use (game data comes from the HellaAPI instead).
    return arkprts.Client(auth=auth, assets=False)

def _skill_columns(op) -> dict:
    '''
    Returns an operator's SKILL_COLUMNS: each skill's mastery level and the
    selected skill.

    Args:
        op: One arkprts character, with skills (each with specialize_level)
            and default_skill_index.

    Returns:
        {column: value}, with None for skills the operator doesn't have.
    '''
    skills = op.skills or []
    columns = {}
    for i, column in enumerate(MASTERY_COLUMNS):
        columns[column] = skills[i].specialize_level if i < len(skills) else None
    # default_skill_index counts from 0 (and is -1 without skills), while the
    # game numbers skills from 1, so it's shifted to match the mastery columns.
    selected = op.default_skill_index
    columns['selected_skill'] = selected + 1 if 0 <= selected < len(skills) else None
    return columns


def format_operator_data(operator_data: Mapping) -> pd.DataFrame:
    '''
    Converts arkprts' {char_key: character} roster into a DataFrame with
    one row per operator: id, potential, level, phase, trust, skill_level,
    each skill's mastery level, and the selected skill.

    Args:
        operator_data: arkprts' troop.chars mapping of the player's operators.

    Returns:
        A DataFrame with the ROSTER_COLUMNS. Trust is converted from raw
        favor points to a percentage; potential is 0-indexed (0 = none).
    '''
    # The column names here are this program's; the attribute names on the
    # right are arkprts'.
    rows = [{'id': op.char_id, 'potential': op.potential_rank,
        'level': op.level, 'phase': op.evolve_phase, 'trust': calculate_trust(op.favor_point), 'skill_level': op.main_skill_lvl,
        **_skill_columns(op)}
        for op in operator_data.values()]
    # columns= keeps the columns (and their order) even for an empty roster.
    df = pd.DataFrame(rows, columns=ROSTER_COLUMNS)
    # Int64 (pandas' integer type that allows blanks) keeps the skill columns
    # as whole numbers; otherwise a blank would turn them into 2.0, 3.0, ...
    df[SKILL_COLUMNS] = df[SKILL_COLUMNS].astype('Int64')
    return df


async def retrieve_operator_info(client: arkprts.Client) -> pd.DataFrame:
    '''
    Retrieves the logged in player's operator roster as a DataFrame.

    Args:
        client: A logged in client, as returned by login.

    Returns:
        The roster, as formatted by format_operator_data.
    '''
    data = await client.get_data()
    operators = data.troop.chars
    logger.info('Retrieved %d operators from the Arknights servers', len(operators))
    return format_operator_data(operators)


async def retrieve_raw_data(client: arkprts.Client) -> dict:
    '''
    Retrieves the logged in player's full raw account data.

    Args:
        client: A logged in client, as returned by login.

    Returns:
        The account data as the game servers return it (unparsed JSON).
    '''
    return await client.get_raw_data()


def filter_operators(
    operator_df: pd.DataFrame, min_phase = None, max_phase = None, min_level = None, max_level = None,
    min_potential = None, max_potential = None, rarity = None,
) -> pd.DataFrame:
    '''
    Returns the rows of operator_df within every given (inclusive) phase,
    level, and potential bound. Bounds left as None aren't applied.
    rarity is not supported yet, since the roster doesn't include it.

    Args:
        operator_df: The roster, as returned by format_operator_data.
        min_phase, max_phase: Bounds on the operator's phase.
        min_level, max_level: Bounds on the operator's level.
        min_potential, max_potential: Bounds on the operator's potential.
        rarity: Not used yet.

    Returns:
        The rows that pass every given bound (operator_df is not changed).
    '''
    # The mask shares operator_df's index so it lines up with any row labels,
    # not just the default 0, 1, 2, ... numbering.
    mask = pd.Series(True, index=operator_df.index)
    # Each bound narrows the mask. astype(int) guards against columns read
    # back from a CSV file as another type.
    if min_phase is not None:
        mask &= operator_df['phase'].astype(int) >= min_phase
    if max_phase is not None:
        mask &= operator_df['phase'].astype(int) <= max_phase
    if min_level is not None:
        mask &= operator_df['level'].astype(int) >= min_level
    if max_level is not None:
        mask &= operator_df['level'].astype(int) <= max_level
    if min_potential is not None:
        mask &= operator_df['potential'].astype(int) >= min_potential
    if max_potential is not None:
        mask &= operator_df['potential'].astype(int) <= max_potential
    # if rarity is not None:
    #     mask &= operator_df['rarity'].astype(int) == rarity
    filtered_df = operator_df[mask]
    return filtered_df
