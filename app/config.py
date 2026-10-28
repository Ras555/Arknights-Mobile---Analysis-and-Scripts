#File Documentation
'''
File: config.py
Description: Loads the .env file and exposes the paths and settings used
    by the rest of the app package.
Created: 2026-10-28
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import os

from dotenv import load_dotenv

APP_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
LIB_DIR = os.path.join(PROJECT_ROOT, 'lib')

# ref/ stores outputs that scripts read back on later runs, io/ stores
# outputs meant to be viewed by the user. Some files are written to both.
REF_DIR = os.path.join(PROJECT_ROOT, 'ref')
IO_DIR = os.path.join(PROJECT_ROOT, 'io')

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, '.env'), override=True)

USER_EMAIL = os.environ.get('USER_EMAIL')

RAW_DATA_FILE = os.path.join(REF_DIR, 'raw_data.json')
ELITE2_OPERATORS_FILE = os.path.join(REF_DIR, 'elite2_operators.csv')
OPERATOR_STATS_FILE = os.path.join(REF_DIR, 'operator_stats_output.csv')
OPERATOR_STATS_VIEW_FILE = os.path.join(IO_DIR, 'operator_stats_output.csv')

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'arknights.log')
