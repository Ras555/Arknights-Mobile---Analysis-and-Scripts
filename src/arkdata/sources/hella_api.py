#File Documentation
'''
File: hella_api.py
Description: A client for retrieving Arknights operator, stage, enemy, and
    deployable (summon and device) game data from the HellaAPI (https://awedtan.ca), with timeouts and
    retries.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Constants

HELLA_API_BASE_URL = "https://awedtan.ca/api"
REQUEST_TIMEOUT_SECONDS = 10
# Whole collections are large (all operators is about 33 MB), so they get a longer timeout.
BULK_TIMEOUT_SECONDS = 120
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 1.0

# HTTP status codes worth retrying: too many requests, and server-side errors.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HellaAPIError(Exception):
    '''
    Raised when the HellaAPI cannot be reached or returns unexpected data.
    '''


# The _*_from helpers check that a document's "value" has the shape the
# calculations expect, so a change on the HellaAPI's side fails here with a
# clear HellaAPIError instead of a KeyError somewhere deeper in the program.

def _operator_data_from(value, operator_key: str) -> dict:
    '''
    Returns an operator document value's "data" dict.

    Args:
        value: The operator document's "value" field.
        operator_key: The operator requested, for the error message.

    Returns:
        The operator's game data.

    Raises:
        HellaAPIError: If value has no "data" field.
    '''
    try:
        return value["data"]
    except (KeyError, TypeError) as exc:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for operator '{operator_key}'") from exc


def _stage_variants_from(value, stage_id: str) -> list:
    '''
    Returns a stage document's value, after checking it's a non-empty list.

    Args:
        value: The stage document's "value" field.
        stage_id: The stage requested, for the error message.

    Returns:
        The stage's variants.

    Raises:
        HellaAPIError: If value isn't a non-empty list.
    '''
    if not isinstance(value, list) or not value:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for stage '{stage_id}'")
    return value


def _enemy_data_from(value, enemy_id: str) -> dict:
    '''
    Returns an enemy document's value, after checking it has "excel" and
    "levels".

    Args:
        value: The enemy document's "value" field.
        enemy_id: The enemy requested, for the error message.

    Returns:
        The enemy's game data.

    Raises:
        HellaAPIError: If value is missing either field.
    '''
    if not isinstance(value, dict) or "excel" not in value or "levels" not in value:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for enemy '{enemy_id}'")
    return value


def _deployable_data_from(value, deployable_id: str) -> dict:
    '''
    Returns a deployable document's value, after checking it has a "data" dict.

    Unlike operators, the whole value is returned rather than just "data",
    because a deployable's skill levels are kept beside it, in "skills".

    Args:
        value: The deployable document's "value" field.
        deployable_id: The deployable requested, for the error message.

    Returns:
        The deployable's game data: "data" (stats and phases, in the same
        format as an operator's), "skills", and "range".

    Raises:
        HellaAPIError: If value has no "data" dict.
    '''
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise HellaAPIError(f"Unexpected HellaAPI response shape for deployable '{deployable_id}'")
    return value


class HellaAPIClient:
    '''
    Retrieves data from the HellaAPI. Failed requests are retried up to
    `retries` times in total, waiting retry_delay, then twice as long, and so
    on between attempts. Only failures that may be temporary are retried
    (timeouts, dropped connections, HTTP 429 and 5xx); others fail at once.

    sleep is injectable so tests don't have to wait.
    '''

    def __init__(
        self, base_url: str = HELLA_API_BASE_URL, timeout: float = REQUEST_TIMEOUT_SECONDS,
        bulk_timeout: float = BULK_TIMEOUT_SECONDS, retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS, sleep: Callable[[float], None] = time.sleep,
    ):
        '''
        Args:
            base_url: The HellaAPI's address, up to and including "/api".
            timeout: Seconds to wait for a normal request.
            bulk_timeout: Seconds to wait for a whole collection.
            retries: Total attempts per request (1 means no retries).
            retry_delay: Seconds to wait before the first retry.
            sleep: The function used to wait between attempts.

        Raises:
            ValueError: If retries is less than 1.
        '''
        # With no attempts, get_json's loop would never run.
        if retries < 1:
            raise ValueError(f"retries must be at least 1, got {retries}")
        # Paths are joined with "/", so a trailing "/" would double it.
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.bulk_timeout = bulk_timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.sleep = sleep

    def get_json(self, path: str, params: dict | None = None, timeout: float | None = None):
        '''
        Fetches {base_url}/{path}?{params} and returns the parsed JSON,
        retrying temporary failures. Raises HellaAPIError if every attempt fails.

        Args:
            path: The path after base_url, e.g. "enemy/enemy_1007_slime".
            params: Query parameters. A list value repeats the parameter
                (e.g. {"include": ["canon", "keys"]} gives
                include=canon&include=keys).
            timeout: Seconds to wait; None uses the client's timeout.

        Returns:
            The parsed JSON (a dict or list, depending on the endpoint).

        Raises:
            HellaAPIError: If the request fails with an error that isn't
                worth retrying, every attempt fails, or the response isn't
                valid JSON.
        '''
        url = f"{self.base_url}/{path}"
        if params:
            # doseq=True writes list values as repeated parameters.
            url += "?" + urllib.parse.urlencode(params, doseq=True)

        for attempt in range(1, self.retries + 1):
            error: Exception  # set by whichever except block catches a retryable failure
            try:
                with urllib.request.urlopen(url, timeout=timeout or self.timeout) as response:
                    raw = response.read()
            # HTTPError is a subclass of URLError, so it must be caught first.
            # The server answered with an error status, which is only worth
            # retrying for the codes in RETRYABLE_STATUS_CODES.
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS_CODES or attempt == self.retries:
                    raise HellaAPIError(f"Failed to reach HellaAPI at {url}: {exc}") from exc
                error = exc
            # The server couldn't be reached, or the connection broke part way
            # through. These are usually temporary, so they're always retried.
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
                if attempt == self.retries:
                    raise HellaAPIError(f"Failed to reach HellaAPI at {url}: {exc}") from exc
                error = exc
            else:
                # The request succeeded. Invalid JSON isn't retried, since
                # asking again would most likely give the same response.
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise HellaAPIError(f"HellaAPI returned invalid JSON for {url}: {exc}") from exc

            # Only reached after a retryable failure. Wait longer after each
            # one (exponential backoff), so a struggling server gets a break.
            delay = self.retry_delay * 2 ** (attempt - 1)
            logger.warning('HellaAPI request to %s failed (%s); retrying in %s s (attempt %d of %d)',
                           url, error, delay, attempt + 1, self.retries)
            self.sleep(delay)
        # The last attempt always returns or raises above; this line only
        # tells mypy the function can't end without returning.
        raise AssertionError("unreachable")  # pragma: no cover

    def fetch_about(self) -> dict:
        '''
        Returns the HellaAPI's latest data update: {"index", "hash", "date",
        "message", ...}. index and hash change on every data update.

        Returns:
            The latest update's details.

        Raises:
            HellaAPIError: If the request fails or the response has no index.
        '''
        about = self.get_json("about")
        # The endpoint may return a list of updates, newest first.
        if isinstance(about, list) and about:
            about = about[0]
        if not isinstance(about, dict) or "index" not in about:
            raise HellaAPIError("Unexpected HellaAPI response shape for about")
        return about

    def fetch_document(self, resource: str, key: str) -> dict:
        '''
        Returns the whole document for one key: {"keys", "value", ...}. Bulk
        and single documents may also include "canon" (the main id) and
        "meta" (update index and content hash).

        Args:
            resource: The resource type, e.g. "operator", "stage" or "enemy".
            key: Any of the document's keys (its id or a version of its name).

        Returns:
            The whole document.

        Raises:
            HellaAPIError: If the request fails or the document has no value.
        '''
        # quote() escapes characters (e.g. spaces in names) that aren't
        # allowed in a URL path.
        document = self.get_json(f"{resource}/{urllib.parse.quote(key)}")
        if not isinstance(document, dict) or "value" not in document:
            raise HellaAPIError(f"Unexpected HellaAPI response shape for {resource} '{key}'")
        return document

    def fetch_collection(self, resource: str, include: list | None = None) -> list:
        '''
        Returns every document of a resource type, using the bulk timeout.
        include limits each document to the given top-level fields (e.g.
        ["canon", "keys", "meta"]); the HellaAPI ignores nested field paths.

        Args:
            resource: The resource type, e.g. "operator" or "enemy".
            include: The top-level fields to keep; None keeps everything.

        Returns:
            Every document of that resource type.

        Raises:
            HellaAPIError: If the request fails or the response isn't a list.
        '''
        params = {"include": include} if include else None
        documents = self.get_json(resource, params=params, timeout=self.bulk_timeout)
        if not isinstance(documents, list):
            raise HellaAPIError(f"Unexpected HellaAPI response shape for the {resource} collection")
        return documents

    def fetch_operator_data(self, operator_key: str) -> dict:
        '''
        Returns an operator's "data" dict (stats, phases, skills, talents,
        potentialRanks, and favorKeyFrames). operator_key may be the coded id
        (e.g. "char_002_amiya") or a version of the printed name (e.g. "Amiya").

        Raises:
            HellaAPIError: If the request fails or the document is malformed.
        '''
        return _operator_data_from(self.fetch_document("operator", operator_key)["value"], operator_key)

    def fetch_stage_data(self, stage_id: str) -> list:
        '''
        Returns a list of stage variants (e.g. normal and challenge mode), each
        a dict with "excel" (stage metadata) and "levels" (map, waves, and
        enemyDbRefs).

        Raises:
            HellaAPIError: If the request fails or the document is malformed.
        '''
        return _stage_variants_from(self.fetch_document("stage", stage_id)["value"], stage_id)

    def fetch_enemy_data(self, enemy_id: str) -> dict:
        '''
        Returns a dict with "excel" (name, description, damageType) and
        "levels" (per-level stats, under levels["Value"]).

        Raises:
            HellaAPIError: If the request fails or the document is malformed.
        '''
        return _enemy_data_from(self.fetch_document("enemy", enemy_id)["value"], enemy_id)

    def fetch_deployable_data(self, deployable_id: str) -> dict:
        '''
        Returns a deployable's (summon, trap or device) game data: "data",
        "skills", and "range". deployable_id may be its coded id (e.g.
        "token_10002_kalts_mon3tr") or a version of its name.

        Raises:
            HellaAPIError: If the request fails or the document is malformed.
        '''
        return _deployable_data_from(self.fetch_document("deployable", deployable_id)["value"], deployable_id)


# Module-level functions: single attempts with no retries, kept for callers
# that don't need a configured client.

def _fetch_value(resource: str, key: str, base_url: str = HELLA_API_BASE_URL):
    '''
    Fetch {base_url}/{resource}/{key} and return the response's "value" field.

    Raises:
        HellaAPIError: If the request fails or the response has no value.
    '''
    # url is only built for the error message; the client builds its own.
    url = f"{base_url}/{resource}/{urllib.parse.quote(key)}"
    payload = HellaAPIClient(base_url, retries=1).get_json(f"{resource}/{urllib.parse.quote(key)}")
    try:
        return payload["value"]
    except (KeyError, TypeError) as exc:
        raise HellaAPIError(f"Unexpected HellaAPI response shape for {resource} '{key}' from {url}") from exc


def fetch_operator_data(operator_key: str, base_url: str = HELLA_API_BASE_URL) -> dict:
    '''
    Fetch an operator's game data from the HellaAPI (one attempt). See
    HellaAPIClient.fetch_operator_data.
    '''
    return _operator_data_from(_fetch_value("operator", operator_key, base_url), operator_key)


def fetch_stage_data(stage_id: str, base_url: str = HELLA_API_BASE_URL) -> list:
    '''
    Fetch a stage's game data from the HellaAPI (one attempt). See
    HellaAPIClient.fetch_stage_data.
    '''
    return _stage_variants_from(_fetch_value("stage", stage_id, base_url), stage_id)


def fetch_enemy_data(enemy_id: str, base_url: str = HELLA_API_BASE_URL) -> dict:
    '''
    Fetch an enemy's game data from the HellaAPI (one attempt). See
    HellaAPIClient.fetch_enemy_data.
    '''
    return _enemy_data_from(_fetch_value("enemy", enemy_id, base_url), enemy_id)


def fetch_deployable_data(deployable_id: str, base_url: str = HELLA_API_BASE_URL) -> dict:
    '''
    Fetch a deployable's game data from the HellaAPI (one attempt). See
    HellaAPIClient.fetch_deployable_data.
    '''
    return _deployable_data_from(_fetch_value("deployable", deployable_id, base_url), deployable_id)
