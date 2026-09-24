from collections import Counter
import logging
from datetime import datetime, timezone
from scrapy.exceptions import DropItem

import pymongo
from pymongo import AsyncMongoClient

logger = logging.getLogger(__name__)


class DbClient:
    def __init__(self, settings):
        self.mongo_uri = settings.get("MONGO_URI")
        self.mongo_db = settings.get("MONGO_DATABASE")
        self.mongo_collection = settings.get("MONGO_COLLECTION")
        self.stats = None
        self.saved_to_db_keys = set()
        self.duplicate_title_entries = Counter()

    async def open(self):
        self.client = AsyncMongoClient(self.mongo_uri)
        self.collection = self.client[self.mongo_db][self.mongo_collection]
        try:
            await self.collection.create_index("title", unique=True)
        except pymongo.errors.OperationFailure as exc:
            # 85/86 = an equivalent 'title' index already exists under another name; anything else is unexpected.
            if exc.code in (85, 86):
                logger.warning("Reusing existing 'title' index: %s", exc)
            else:
                raise

    async def close(self):
        await self.client.close()

    async def find_one(self, query, projection=None):
        return await self.collection.find_one(query, projection)

    async def save_to_db(self, title, document, upsert=False):
        try:
            result = await self.collection.update_one(
                {"title": title},
                {"$set": document},
                upsert=upsert,
            )
        except Exception:
            logger.error(
                "Dropping record - failed to save record in DB : %s",
                title,
                extra={"event": "dropped_item"},
            )
            raise

        # current run saved entries
        if title in self.saved_to_db_keys:
            self.duplicate_title_entries[title] += 1
            if self.stats is not None:
                self.stats.set_value(
                    "duplicate_title_entries",
                    {t: c for t, c in self.duplicate_title_entries.items()},
                )
            return result

        self.saved_to_db_keys.add(title)
        if self.stats is not None:
            stat_name = (
                "created_db_items"
                if result.upserted_id is not None
                else "updated_db_items"
            )
            self.stats.inc_value(stat_name)
        return result

    @staticmethod
    def document(adapter):
        doc = adapter.asdict()
        doc.pop("file_bytes", None)
        doc["last_seen"] = datetime.now(timezone.utc)
        return doc
