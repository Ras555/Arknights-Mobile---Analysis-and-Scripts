#File Documentation
'''
File: roster.py
Description: Implements the roster step: log into the Arknights servers,
    save the account's raw data, and save the Elite 2 operator roster for
    the stats step to read.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-02
Last Modified by: Claude
'''

# Imports

import logging
from datetime import datetime, timedelta

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
        '''
        Args:
            email: The Yostar account's email address.
            storage: The Storage the roster is saved through.
            login: The function used to log in.
        '''
        self.email = email
        self.storage = storage
        self.login = login

    async def run(self) -> pd.DataFrame:
        '''
        Logs in, saves the raw account data and the Elite 2 roster, and
        prints the roster.

        Returns:
            The Elite 2 operators, as saved.

        Raises:
            ValueError: If no account email is set.
        '''
        # Checked before logging in, so a missing setting fails straight away
        # rather than after waiting on an email code.
        if not self.email:
            raise ValueError('No account email set. Add USER_EMAIL to the .env file.')

        client = await self.login(self.email)
        # Close the connection even if retrieving the data fails, so it isn't
        # left open (which aiohttp warns about).
        try:
            operators = await retrieve_operator_info(client)
            raw_data = await retrieve_raw_data(client)
        finally:
            await client.network.close()

        # The raw data keeps everything the roster table leaves out (e.g.
        # skills and modules).
        raw_data_file = self.storage.save_raw_data(raw_data)
        logger.info('Saved raw account data to %s', raw_data_file)

        # Only Elite 2 operators are kept, since those are the ones the stats
        # and matchups steps compare.
        elite2_ops = filter_operators(operator_df=operators, min_phase=ELITE_2_PHASE, max_phase=ELITE_2_PHASE)
        elite2_file = self.storage.save_elite2_operators(elite2_ops)
        logger.info('Saved %d Elite 2 operators to %s', len(elite2_ops), elite2_file)
        print(elite2_ops.to_string(index=False))
        return elite2_ops


def check_roster_age(storage, max_age: timedelta, now: datetime | None = None) -> timedelta | None:
    '''
    Logs a warning suggesting `arknights-calc roster` if the saved roster is
    older than max_age. Never logs in by itself, since that needs an email code
    and logs the player out of the game. Returns the roster's age, or None if
    there is no saved roster.

    Args:
        storage: The Storage the roster was saved through.
        max_age: How old the roster can be before a refresh is suggested.
        now: The current time; None uses datetime.now() (tests pass one in).

    Returns:
        The roster's age, or None.
    '''
    saved_at = storage.roster_saved_at()
    # No saved roster isn't warned about here; the step that needs it will
    # fail with its own message.
    if saved_at is None:
        return None
    age = (now or datetime.now()) - saved_at
    if age > max_age:
        logger.warning('Your roster was last updated %d days ago (%s). Run `arknights-calc roster` to refresh it.',
                       age.days, saved_at.date().isoformat())
    return age
