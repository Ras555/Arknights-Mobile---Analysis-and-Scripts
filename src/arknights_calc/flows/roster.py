#File Documentation
'''
File: roster.py
Description: Implements the roster step: log into the Arknights servers,
    save the account's raw data, and save the full operator roster for
    the stats step to read.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import logging
from datetime import datetime, timedelta

import pandas as pd

from arkdata.sources.account import login, retrieve_operator_info, retrieve_raw_data

logger = logging.getLogger(__name__)


def summarize_roster(roster: pd.DataFrame) -> str:
    '''
    Returns a one line summary of the roster: how many operators it has, and
    how many are at each phase (elite level).

    Args:
        roster: The roster, with a phase column.

    Returns:
        e.g. "194 operators (Elite 0: 133, Elite 1: 23, Elite 2: 38)".
    '''
    operator_word = 'operator' if len(roster) == 1 else 'operators'
    if roster.empty:
        return f'0 {operator_word}'
    # sort_index lists the phases in order (Elite 0, 1, 2) rather than by count.
    counts = roster['phase'].astype(int).value_counts().sort_index()
    phases = ', '.join(f'Elite {phase}: {count}' for phase, count in counts.items())
    return f'{len(roster)} {operator_word} ({phases})'


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
        Logs in, saves the raw account data and the full roster, and prints
        a summary of the roster.

        Returns:
            The roster, as saved.

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

        # Every operator is saved; the steps that only want some of them
        # (e.g. matchups uses Elite 2) filter for themselves.
        roster_file, roster_view_file = self.storage.save_roster(operators)
        logger.info('Saved %d operators to %s and %s', len(operators), roster_file, roster_view_file)
        # A summary instead of the whole table, since a full roster can be
        # hundreds of rows; the full list is in the io/ copy.
        print(f'Roster: {summarize_roster(operators)}')
        return operators


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
