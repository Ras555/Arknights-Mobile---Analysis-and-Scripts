#File Documentation
'''
File: roster_flow.py
Description: Implements the roster step: log into the Arknights servers,
    save the account's raw data, and save the Elite 2 operator roster for
    the stats step to read.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import json
import logging
import os

import pandas as pd

from lib.arknights_account import login, retrieve_operator_info, retrieve_raw_data, filter_operators

from app import config

logger = logging.getLogger(__name__)

# Constants

# Phase (elite level) of the operators saved for the stats step.
ELITE_2_PHASE = 2


class RosterProcessor:
    '''
    Retrieves the player's roster (CLAUDE.md Program Flow, roster step).

    login is an async function taking an email and returning an arkprts
    client. It is injectable so tests can avoid a real login.
    '''

    def __init__(self, email=None, raw_data_file=None, elite2_operators_file=None, login=login):
        self.email = email if email is not None else config.USER_EMAIL
        self.raw_data_file = raw_data_file or config.RAW_DATA_FILE
        self.elite2_operators_file = elite2_operators_file or config.ELITE2_OPERATORS_FILE
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

        self._save_raw_data(raw_data)
        logger.info('Saved raw account data to %s', self.raw_data_file)

        elite2_ops = filter_operators(operator_df=operators, min_phase=ELITE_2_PHASE, max_phase=ELITE_2_PHASE)
        self._save_csv(elite2_ops, self.elite2_operators_file)
        logger.info('Saved %d Elite 2 operators to %s', len(elite2_ops), self.elite2_operators_file)
        print(elite2_ops.to_string(index=False))
        return elite2_ops

    def _save_raw_data(self, raw_data):
        os.makedirs(os.path.dirname(self.raw_data_file), exist_ok=True)
        with open(self.raw_data_file, 'w') as f:
            f.write(json.dumps(raw_data, indent=4))

    @staticmethod
    def _save_csv(df, file_path):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        df.to_csv(file_path, index=False)
