"""MongoDB access for the Landing Zone -> Curated Zone transformation.

Decision dates are stored as free-text strings (the scraper persists whatever the
source page rendered), so this client also owns the server-side date parsing that
selects a run's range.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import pymongo

logger = logging.getLogger(__name__)

DATE_FORMATS = ("%d/%m/%Y",)


class DbClient:
    """Reads landing-zone decisions and upserts curated ones.

    ``source`` is the landing collection, ``target`` the curated one; both live in
    the same database.
    """

    def __init__(self, cfg: dict):
        self.mongo_uri = cfg["mongo_uri"]
        self.mongo_db = cfg["mongo_db"]
        self.source_collection = cfg["source_collection"]
        self.target_collection = cfg["target_collection"]
        self.client = pymongo.MongoClient(self.mongo_uri)
        self.source = self.client[self.mongo_db][self.source_collection]
        self.target = self.client[self.mongo_db][self.target_collection]

    def open(self):
        """Fail fast if Mongo is unreachable, and ensure the curated index exists."""
        try:
            self.client.admin.command("ping")
        except pymongo.errors.PyMongoError as exc:
            logger.error("Cannot reach MongoDB at %s: %s", self.mongo_uri, exc)
            raise RuntimeError(
                f"Cannot reach MongoDB at {self.mongo_uri}: {exc}. "
                "Is Mongo running? Start it with `docker compose up -d`."
            ) from exc
        try:
            self.target.create_index("identifier", unique=True)
        except pymongo.errors.OperationFailure as exc:
            # 85/86 = an equivalent 'identifier' index already exists under another
            # name; anything else is unexpected.
            if exc.code in (85, 86):
                logger.warning("Reusing existing 'identifier' index: %s", exc)
            else:
                raise

    def close(self):
        self.client.close()

    @staticmethod
    def parsed_date_expr() -> dict:
        """Parse the stored ``date`` string server-side.

        ``$dateFromString`` needs an explicit format and, with ``onError``, yields
        null rather than failing when the value does not match, so the accepted
        formats are tried in order. ``date`` is stored verbatim from the scraped
        page, so it is trimmed first.
        """
        trimmed = {"$trim": {"input": {"$ifNull": ["$date", ""]}}}
        expr = None
        for fmt in reversed(DATE_FORMATS):
            attempt = {"$dateFromString": {"dateString": trimmed, "format": fmt, "onError": None}}
            expr = attempt if expr is None else {"$ifNull": [attempt, expr]}
        return expr

    @classmethod
    def date_range_filter(cls, start: date, end: date) -> dict:
        """Match decisions whose ``date`` falls in ``[start, end]`` inclusive.

        Parsing happens on the server, so only matching documents are transferred.
        Note that ``$expr`` cannot use an index: this is still a collection scan.
        """
        start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        return {
            "$expr": {
                "$let": {
                    "vars": {"parsed": cls.parsed_date_expr()},
                    "in": {
                        "$and": [
                            {"$ne": ["$$parsed", None]},
                            {"$gte": ["$$parsed", start_dt]},
                            {"$lte": ["$$parsed", end_dt]},
                        ]
                    },
                }
            }
        }

    def find_in_date_range(self, start: date, end: date):
        """Cursor over landing-zone decisions inside the run's date range."""
        return self.source.find(self.date_range_filter(start, end))

    def count_unparsable_dates(self) -> int:
        """Decisions whose ``date`` matches no accepted format.

        These can never satisfy a range filter, so they would be skipped silently.
        """
        return self.source.count_documents({"$expr": {"$eq": [self.parsed_date_expr(), None]}})

    def save_curated(self, identifier: str, metadata: dict):
        """Upsert one curated decision, keyed on ``identifier``."""
        try:
            return self.target.update_one(
                {"identifier": identifier},
                {"$set": metadata},
                upsert=True,
            )
        except pymongo.errors.PyMongoError as exc:
            logger.error("Failed to save curated record %s: %s", identifier, exc)
            raise
