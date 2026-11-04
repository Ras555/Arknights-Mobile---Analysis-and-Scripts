#File Documentation
'''
File: hella_cache.py
Description: Keeps HellaAPI documents on disk so repeat runs don't need the
    network, and brings them up to date only when the HellaAPI's data has
    changed.
Created: 2026-11-02
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: -

Last Modified: 2026-11-03
Last Modified by: Claude
'''

# Imports

import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from arkdata.sources.hella_api import (
    HellaAPIClient,
    HellaAPIError,
    _deployable_data_from,
    _enemy_data_from,
    _operator_data_from,
    _stage_variants_from,
)

logger = logging.getLogger(__name__)

# Constants

# Collections downloaded whole on the first run and kept fully up to date.
# Deployables are summons, traps and devices (832 documents on 2026-11-03).
BULK_RESOURCES = ("operator", "enemy", "deployable")
# Collections cached one document at a time, as they are used.
ON_DEMAND_RESOURCES = ("stage",)
# Top-level fields that identify a document and its version, without its (large) value.
LISTING_FIELDS = ["canon", "keys", "meta"]

DEFAULT_MAX_AGE = timedelta(days=14)
DEFAULT_BULK_THRESHOLD = 40

STATE_FILE_NAME = "state.json"
INDEX_FILE_NAME = "index.json"


def _write_json(path: Path, data) -> None:
    '''
    Saves data to path as JSON, creating the folder if needed.

    Args:
        path: The file to write.
        data: Anything json.dump can write.
    '''
    # Write to a temporary file and rename it, so an interrupted write never leaves a broken file.
    # os.replace swaps the file in one step, so path always holds either the
    # old contents or the new ones, never half of the new ones.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(temporary_path, path)


def _read_json(path: Path):
    '''
    Returns the parsed JSON in path, or None if it is missing or unreadable.

    A broken cache file is treated like a missing one, so the data is simply
    downloaded again instead of the run failing.

    Args:
        path: The file to read.

    Returns:
        The parsed JSON, or None.
    '''
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    # A missing file is normal (e.g. the first run), so it isn't logged.
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable cache file %s: %s", path, exc)
        return None


def _document_hash(document: dict) -> str | None:
    '''
    Returns a document's content hash from its "meta", or None if it has none.

    The hash changes whenever the document's content does, so comparing it
    with the cached hash shows whether the cached copy is out of date.
    '''
    return (document.get("meta") or {}).get("hash")


def _canon_of(document: dict, fallback: str | None = None) -> str:
    '''
    Returns a document's main id: its "canon", else its first key, else fallback.

    The canon names the document's cache file, so every document needs one.

    Args:
        document: A HellaAPI document.
        fallback: The id to use if the document has neither.

    Returns:
        The document's canon id.

    Raises:
        HellaAPIError: If the document has no canon, no keys and no fallback.
    '''
    canon = document.get("canon") or next(iter(document.get("keys") or []), None) or fallback
    if not canon:
        raise HellaAPIError(f"HellaAPI document has no id: {str(document)[:200]}")
    return canon


class HellaCache:
    '''
    A disk cache in front of a HellaAPIClient, laid out under cache_dir as:
        state.json                  the HellaAPI update ("about") the cache was last synced to, and when
        <resource>/index.json       {"aliases": {key: canon}, "hashes": {canon: content hash}}
        <resource>/<canon>.json     one HellaAPI document each

    sync() is called once per run. If the HellaAPI's update (from /about) is
    unchanged and the last check is newer than max_age, nothing else is
    fetched. Otherwise each bulk collection's listing (ids and content hashes
    only) is compared with the cache, and only changed documents are
    re-downloaded: one by one when there are at most bulk_threshold of them,
    or as a whole collection when there are more. Cached stages whose hash
    changed are dropped, to be re-downloaded the next time they're used.

    The fetch_* methods return the same data as HellaAPIClient's, from the
    cache when possible; documents not in the cache are downloaded and added.

    now is injectable so tests can control the time.
    '''

    def __init__(
        self, client: HellaAPIClient, cache_dir: Path, max_age: timedelta = DEFAULT_MAX_AGE,
        bulk_threshold: int = DEFAULT_BULK_THRESHOLD, now: Callable[[], datetime] = datetime.now,
    ):
        '''
        Args:
            client: The HellaAPIClient used for every download.
            cache_dir: The folder the cache is kept in (created when needed).
            max_age: How long a sync is trusted before the cache is checked
                again, even if the HellaAPI's update hasn't changed.
            bulk_threshold: The most changed documents downloaded one by one;
                above this, the whole collection is downloaded instead.
            now: The function giving the current time.
        '''
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.max_age = max_age
        self.bulk_threshold = bulk_threshold
        self.now = now

    # Paths

    def _state_path(self) -> Path:
        '''
        Returns the path of state.json.
        '''
        return self.cache_dir / STATE_FILE_NAME

    def _index_path(self, resource: str) -> Path:
        '''
        Returns the path of a resource's index.json.
        '''
        return self.cache_dir / resource / INDEX_FILE_NAME

    def _document_path(self, resource: str, canon: str) -> Path:
        '''
        Returns the path of a cached document's file.
        '''
        # Ids can contain characters that aren't allowed in file names (e.g.
        # "/" or ":"), so everything except letters, digits and -_. is escaped.
        return self.cache_dir / resource / f"{urllib.parse.quote(canon, safe='-_.')}.json"

    # Index and documents

    def _load_index(self, resource: str) -> dict:
        '''
        Returns a resource's index: {"aliases": {key: canon}, "hashes": {canon: hash}}.

        A missing or broken index gives an empty one, which makes the cache
        download that resource's documents again.
        '''
        index = _read_json(self._index_path(resource))
        if not isinstance(index, dict):
            return {"aliases": {}, "hashes": {}}
        # Fill in either part if it's missing, so callers can rely on both.
        index.setdefault("aliases", {})
        index.setdefault("hashes", {})
        return index

    def _save_index(self, resource: str, index: dict) -> None:
        '''
        Saves a resource's index to its index.json.
        '''
        _write_json(self._index_path(resource), index)

    @staticmethod
    def _add_aliases(index: dict, canon: str, keys) -> None:
        '''
        Points the canon and each of the document's keys at the canon in
        the index, so the document can be found by any of them.

        Args:
            index: The resource's index (changed in place).
            canon: The document's canon id.
            keys: The document's keys (e.g. its printed names), or None.
        '''
        # The lowercase copy lets lookups ignore case (e.g. "amiya" finds "Amiya").
        for key in [canon, *(keys or [])]:
            index["aliases"][key] = canon
            index["aliases"][key.lower()] = canon

    def _save_document(self, resource: str, index: dict, document: dict, canon: str, content_hash: str | None) -> None:
        '''
        Saves a document's file and records its hash and keys in the index.
        The index itself is saved separately, once all changes are made.

        Args:
            resource: The resource type, e.g. "operator".
            index: The resource's index (changed in place).
            document: The HellaAPI document to save.
            canon: The document's canon id.
            content_hash: The hash to record for it.
        '''
        _write_json(self._document_path(resource, canon), document)
        index["hashes"][canon] = content_hash
        self._add_aliases(index, canon, document.get("keys"))

    def _remove_document(self, resource: str, index: dict, canon: str) -> None:
        '''
        Deletes a document's file and removes it from the index.
        '''
        self._document_path(resource, canon).unlink(missing_ok=True)
        index["hashes"].pop(canon, None)
        # Remove every alias of the document too, so no lookup leads to the deleted file.
        index["aliases"] = {key: value for key, value in index["aliases"].items() if value != canon}

    # Syncing

    def _is_up_to_date(self, state, about: dict) -> bool:
        '''
        Returns whether the cache can be used without checking anything else.

        Args:
            state: The contents of state.json (None if it's missing).
            about: The HellaAPI's latest update, from fetch_about.

        Returns:
            True if the cache was synced to the same update within max_age.
        '''
        # A missing or broken state file means there's no record of a sync.
        if not isinstance(state, dict) or not isinstance(state.get("about"), dict):
            return False
        same_update = state["about"].get("index") == about.get("index") and state["about"].get("hash") == about.get("hash")
        # max_age is a backstop: even if the update looks the same, the cache
        # is checked again once the last check is old enough.
        try:
            checked_at = datetime.fromisoformat(state["checked_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return same_update and self.now() - checked_at <= self.max_age

    def _has_every_bulk_collection(self) -> bool:
        '''
        Returns whether every collection in BULK_RESOURCES has been
        downloaded (has an index).
        '''
        return all(self._index_path(resource).exists() for resource in BULK_RESOURCES)

    def sync(self, refresh: bool = False) -> None:
        '''
        Brings the cache up to date with the HellaAPI. With refresh, every
        bulk collection is downloaded again and cached stages are dropped.
        Raises HellaAPIError if the HellaAPI can't be reached; the cache is
        then left as it was and can still be used.

        Args:
            refresh: True to download everything again, whatever has changed.

        Raises:
            HellaAPIError: If the HellaAPI can't be reached.
        '''
        about = self.client.fetch_about()
        # How many documents couldn't be downloaded during this sync.
        failed = 0

        if refresh:
            logger.info("Refreshing the HellaAPI cache: downloading all data again")
            for resource in BULK_RESOURCES:
                self._download_collection(resource)
            for resource in ON_DEMAND_RESOURCES:
                self._clear_collection(resource)
        # A bulk collection without an index (e.g. one added to BULK_RESOURCES
        # since the last sync) is downloaded even when nothing else changed.
        elif self._is_up_to_date(_read_json(self._state_path()), about) and self._has_every_bulk_collection():
            logger.info("HellaAPI data unchanged since the last check (update %s); using the cache", about.get("index"))
            return
        else:
            logger.info("Checking the HellaAPI cache against update %s", about.get("index"))
            for resource in BULK_RESOURCES:
                # With no index (e.g. the first run), there's nothing to
                # compare against, so the whole collection is downloaded.
                if self._index_path(resource).exists():
                    failed += self._update_collection(resource)
                else:
                    self._download_collection(resource)
            for resource in ON_DEMAND_RESOURCES:
                self._update_cached_documents(resource)

        if failed:
            # Not recording this update as synced makes the next run check again.
            logger.warning("%d documents could not be updated; the HellaAPI cache will be checked again next run", failed)
            return
        # Record the update the cache now matches, so the next run can skip syncing.
        _write_json(self._state_path(), {"about": about, "checked_at": self.now().isoformat()})

    def _download_collection(self, resource: str) -> None:
        '''
        Downloads every document of a resource and replaces its cached copies.
        '''
        logger.info("Downloading every %s from the HellaAPI (this can take a minute)", resource)
        documents = self.client.fetch_collection(resource)
        # The index is rebuilt from scratch, so documents no longer in the
        # collection drop out of it. The old index is only used to find
        # their files, which are then deleted.
        old_index = self._load_index(resource)
        index: dict = {"aliases": {}, "hashes": {}}
        for document in documents:
            self._save_document(resource, index, document, _canon_of(document), _document_hash(document))
        for canon in set(old_index["hashes"]) - set(index["hashes"]):
            self._document_path(resource, canon).unlink(missing_ok=True)
        self._save_index(resource, index)
        logger.info("Cached %d %s documents", len(documents), resource)

    def _listing_changes(self, resource: str, index: dict) -> tuple[dict, list, list]:
        '''
        Returns (listing by canon, changed or new canons, removed canons),
        comparing the collection's current content hashes with the cache.

        Only each document's ids and hash are downloaded (LISTING_FIELDS),
        which is far smaller than the whole collection.

        Args:
            resource: The resource type, e.g. "operator".
            index: The resource's index.

        Returns:
            The listing, as {canon: listed document}; the canons that are
            new, changed, or missing their file; and the canons that are
            cached but no longer in the collection.
        '''
        listing = {_canon_of(document): document for document in self.client.fetch_collection(resource, include=LISTING_FIELDS)}
        changed = [
            canon for canon, document in listing.items()
            if index["hashes"].get(canon) != _document_hash(document) or not self._document_path(resource, canon).exists()
        ]
        removed = [canon for canon in index["hashes"] if canon not in listing]
        return listing, changed, removed

    def _update_collection(self, resource: str) -> int:
        '''
        Re-downloads changed documents and removes deleted ones. Returns how
        many documents could not be downloaded.

        Args:
            resource: The resource type, e.g. "operator".

        Returns:
            The number of changed documents that couldn't be downloaded.
        '''
        index = self._load_index(resource)
        listing, changed, removed = self._listing_changes(resource, index)

        # Past a point, one large download is quicker than many small ones
        # (e.g. after a game update that changes most operators).
        if len(changed) > self.bulk_threshold:
            logger.info("%d %s documents changed; downloading the whole collection", len(changed), resource)
            self._download_collection(resource)
            return 0

        failed = 0
        for canon in changed:
            try:
                document = self.client.fetch_document(resource, canon)
            except HellaAPIError as exc:
                # Keep the old copy (if any) and its old hash, so the next sync tries again.
                logger.error("Could not update %s %s in the HellaAPI cache: %s", resource, canon, exc)
                failed += 1
                continue
            # Store the listing's hash, since that's what the next sync compares.
            self._save_document(resource, index, document, canon, _document_hash(listing[canon]))
        for canon in removed:
            self._remove_document(resource, index, canon)
        # Refresh every document's aliases from the listing, so a new key
        # works even for a document whose content didn't change.
        for canon, document in listing.items():
            self._add_aliases(index, canon, document.get("keys"))
        self._save_index(resource, index)
        logger.info("Updated %d, removed %d %s documents in the HellaAPI cache", len(changed) - failed, len(removed), resource)
        return failed

    def _update_cached_documents(self, resource: str) -> None:
        '''
        Drops a resource's cached documents that have changed or been removed.
        '''
        # Only documents already in the cache are checked; changed ones are dropped and re-downloaded when next used.
        index = self._load_index(resource)
        # Nothing cached means nothing to check, so the listing isn't downloaded.
        if not index["hashes"]:
            return
        _, changed, removed = self._listing_changes(resource, index)
        # changed also lists every document that isn't cached yet; those are
        # left alone, since they're only downloaded when used.
        stale = [canon for canon in changed if canon in index["hashes"]] + removed
        for canon in stale:
            self._remove_document(resource, index, canon)
        self._save_index(resource, index)
        if stale:
            logger.info("Dropped %d changed %s documents from the HellaAPI cache", len(stale), resource)

    def _clear_collection(self, resource: str) -> None:
        '''
        Removes every cached document of a resource.
        '''
        index = self._load_index(resource)
        for canon in list(index["hashes"]):
            self._remove_document(resource, index, canon)
        self._save_index(resource, index)

    # Fetching

    def fetch_document(self, resource: str, key: str) -> dict:
        '''
        Returns the document for key from the cache, or downloads it from the
        HellaAPI and adds it to the cache. key may be the canon id or any of
        the document's keys (e.g. a printed name).

        Args:
            resource: The resource type, e.g. "operator".
            key: The document's canon id or any of its keys.

        Returns:
            The whole document.

        Raises:
            HellaAPIError: If the document isn't cached and can't be downloaded.
        '''
        index = self._load_index(resource)
        # Try the key as given, then ignoring case.
        canon = index["aliases"].get(key) or index["aliases"].get(key.lower())
        if canon is not None:
            # If the file is missing or broken, fall through and download it.
            document = _read_json(self._document_path(resource, canon))
            if isinstance(document, dict):
                return document

        # Not cached (or unreadable), so download it and add it to the cache.
        document = self.client.fetch_document(resource, key)
        canon = _canon_of(document, fallback=canon or key)
        self._save_document(resource, index, document, canon, _document_hash(document))
        # The HellaAPI may accept a key that isn't in the document's keys
        # (e.g. another version of a name), so it's added as an alias too.
        index["aliases"][key] = canon
        self._save_index(resource, index)
        return document

    def fetch_operator_data(self, operator_key: str) -> dict:
        '''
        Same as HellaAPIClient.fetch_operator_data, using the cache.
        '''
        return _operator_data_from(self.fetch_document("operator", operator_key)["value"], operator_key)

    def fetch_stage_data(self, stage_id: str) -> list:
        '''
        Same as HellaAPIClient.fetch_stage_data, using the cache.
        '''
        return _stage_variants_from(self.fetch_document("stage", stage_id)["value"], stage_id)

    def fetch_enemy_data(self, enemy_id: str) -> dict:
        '''
        Same as HellaAPIClient.fetch_enemy_data, using the cache.
        '''
        return _enemy_data_from(self.fetch_document("enemy", enemy_id)["value"], enemy_id)

    def fetch_deployable_data(self, deployable_id: str) -> dict:
        '''
        Same as HellaAPIClient.fetch_deployable_data, using the cache.
        '''
        return _deployable_data_from(self.fetch_document("deployable", deployable_id)["value"], deployable_id)
