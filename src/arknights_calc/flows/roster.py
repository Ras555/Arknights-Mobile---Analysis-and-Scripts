#File Documentation
'''
File: roster.py
Description: Implements the roster step: log into the Arknights servers,
    save the account's raw data, and save the Elite 2 operator roster for
    the stats step to read.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-10-29
Last Modified by: Claude
'''

# Imports

import logging

import pandas as pd

from arkdata.sources.account import filter_operators, login, retrieve_operator_info, retrieve_raw_data

logger = logging.getLogger(__name__)

# Constants

# Phase (elite level) of the operators saved for the stats step.
ELITE_2_PHASE = 2


class RosterProcessor:
    '''
    Retrieves the player's roster (CLAUDE.md Program Flow, roster step) and
    saves it through storage.

    login is an async function taking an email and returning an arkprts
    client. It is injectable so tests can avoid a real login.
    '''

    def __init__(self, email, storage, login=login):
        self.email = email
        self.storage = storage
        self.login = login

    async def run(self) -> pd.DataFrame:
        if not self.email:
            raise ValueError('No account email set. Add USER_EMAIL to the .env file.')

        client = await self.login(self.email)
        try:
            operators = await retrieve_operator_info(client)
            raw_data = await retrieve_raw_data(client)
        finally:
            await client.network.close()

        raw_data_file = self.storage.save_raw_data(raw_data)
        logger.info('Saved raw account data to %s', raw_data_file)

        elite2_ops = filter_operators(operator_df=operators, min_phase=ELITE_2_PHASE, max_phase=ELITE_2_PHASE)
        elite2_file = self.storage.save_elite2_operators(elite2_ops)
        logger.info('Saved %d Elite 2 operators to %s', len(elite2_ops), elite2_file)
        print(elite2_ops.to_string(index=False))
        return elite2_ops
