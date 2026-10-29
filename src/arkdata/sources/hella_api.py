#File Documentation
'''
File: hella_api.py
Description: A thin client for retrieving Arknights operator, stage, and
    enemy game data from the HellaAPI (https://awedtan.ca).
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-28
Last Modified by: Claude
'''

# Imports

import json
import urllib.error
import urllib.parse
import urllib.request

# Constants

HELLA_API_BASE_URL = "https://awedtan.ca/api"
REQUEST_TIMEOUT_SECONDS = 10


class HellaAPIError(Exception):
    '''
    Raised when the HellaAPI cannot be reached or returns unexpected data.
    '''


def _fetch_value(resource: str, key: str, base_url: str = HELLA_API_BASE_URL):
    '''
    Fetch {base_url}/{resource}/{key} and return the response's "value" field.
    '''
    encoded_key = urllib.parse.quote(key)
    url = f"{base_url}/{resource}/{encoded_key}"

    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise HellaAPIError(f"Failed to reach HellaAPI at {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HellaAPIError(f"HellaAPI returned invalid JSON for {url}: {exc}") from exc

    try:
        return payload["value"]
    except (KeyError, TypeError) as exc:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for {resource} '{key}'") from exc


def fetch_operator_data(operator_key: str, base_url: str = HELLA_API_BASE_URL) -> dict:
    '''
    Fetch an operator's game data from the HellaAPI.

    operator_key may be the operator's coded id (e.g. "char_002_amiya") or
    a version of the operator's printed name (e.g. "Amiya").

    Returns the operator's "data" dict, containing its stats, phases,
    skills, talents, potentialRanks, and favorKeyFrames.
    '''
    value = _fetch_value("operator", operator_key, base_url)
    try:
        return value["data"]
    except (KeyError, TypeError) as exc:
        raise HellaAPIError(
            f"Unexpected HellaAPI response shape for operator '{operator_key}'"
        ) from exc


def fetch_stage_data(stage_id: str, base_url: str = HELLA_API_BASE_URL) -> list:
    '''
    Fetch a stage's game data from the HellaAPI.

    Returns a list of stage variants (e.g. normal and challenge mode), each
    a dict with "excel" (stage metadata) and "levels" (map, waves, and
    enemyDbRefs).
    '''
    value = _fetch_value("stage", stage_id, base_url)
    if not isinstance(value, list) or not value:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for stage '{stage_id}'")
    return value


def fetch_enemy_data(enemy_id: str, base_url: str = HELLA_API_BASE_URL) -> dict:
    '''
    Fetch an enemy's game data from the HellaAPI.

    Returns a dict with "excel" (name, description, damageType) and
    "levels" (per-level stats, under levels["Value"]).
    '''
    value = _fetch_value("enemy", enemy_id, base_url)
    if not isinstance(value, dict) or "excel" not in value or "levels" not in value:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for enemy '{enemy_id}'")
    return value
