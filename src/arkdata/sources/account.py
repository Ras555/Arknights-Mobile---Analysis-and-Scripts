#File Documentation
'''
File: account.py
Description: Logs into the Arknights (Yostar EN) servers with arkprts,
    retrieves a player's operator roster, and converts/filters that roster.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import logging

import arkprts
import pandas as pd

from arkdata.calculations.trust import calculate_trust

logger = logging.getLogger(__name__)

# Constants

# Columns of the roster DataFrame built by format_operator_data.
ROSTER_COLUMNS = ['id', 'potential', 'level', 'phase', 'trust', 'skill_level']


async def login(email) -> arkprts.Client:
    '''
    Logs into the Yostar EN server with an email code (arkprts prompts for
    the code sent to the email) and returns an authenticated client.
    '''
    auth = arkprts.YostarAuth("en")
    await auth.login_with_email_code(email)
    return arkprts.Client(auth=auth, assets=False)

def format_operator_data(operator_data: dict) -> pd.DataFrame:
    '''
    Converts arkprts' {char_key: character} roster into a DataFrame with
    one row per operator: id, potential, level, phase, trust, skill_level.
    '''
    operator_data = [{'id': op.char_id, 'potential': op.potential_rank,
        'level': op.level, 'phase': op.evolve_phase, 'trust': calculate_trust(op.favor_point), 'skill_level': op.main_skill_lvl}
        for op in operator_data.values()]
    return pd.DataFrame(operator_data, columns=ROSTER_COLUMNS)


async def retrieve_operator_info(client: arkprts.Client) -> pd.DataFrame:
    '''
    Retrieves the logged in player's operator roster as a DataFrame.
    '''
    data = await client.get_data()
    operators = data.troop.chars
    logger.info('Retrieved %d operators from the Arknights servers', len(operators))
    return format_operator_data(operators)


async def retrieve_raw_data(client: arkprts.Client) -> dict:
    '''
    Retrieves the logged in player's full raw account data.
    '''
    return await client.get_raw_data()


def filter_operators(operator_df: pd.DataFrame, min_phase = None, max_phase = None, min_level = None, max_level = None, min_potential = None, max_potential = None, rarity = None) -> pd.DataFrame:
    '''
    Returns the rows of operator_df within every given (inclusive) phase,
    level, and potential bound. Bounds left as None aren't applied.
    rarity is not supported yet, since the roster doesn't include it.
    '''
    # The mask shares operator_df's index so it lines up with any row labels,
    # not just the default 0, 1, 2, ... numbering.
    mask = pd.Series(True, index=operator_df.index)
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
