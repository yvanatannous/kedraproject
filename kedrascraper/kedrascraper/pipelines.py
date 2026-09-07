"""Item pipeline that deduplicates decision files by content hash, uploads changed
files to MinIO (S3), and upserts their metadata in MongoDB keyed on ``title``.

See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html
"""

import hashlib
import logging

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

from kedrascraper.clients.db_client import DbClient
from kedrascraper.clients.s3_client import S3Client

logger = logging.getLogger(__name__)


class StoragePipeline:
    """Hashes each file, uploads changed content to MinIO, and upserts metadata in Mongo (keyed on title)."""

    def __init__(self, settings):
        self.db_client = DbClient(settings)
        self.s3_client = S3Client(settings)

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls(crawler.settings)
        pipeline.db_client.stats = crawler.stats
        return pipeline

    def open_spider(self):
        self.db_client.open()
        self.s3_client.open()

    def close_spider(self):
        self.db_client.close()

    def process_item(self, item):
        adapter = ItemAdapter(item)
        title = adapter.get("title")
        link = adapter.get("link")
        file_bytes = adapter.get("file_bytes")

        missing_field = self._validate_required_fields(adapter, ("title", "link"))
        if missing_field:
            logger.error(
                "Dropping record - missing %s field",
                missing_field,
                extra={"event": "dropped_item", "record": item},
            )
            raise DropItem(f"Missing {missing_field} for item: {item}")

        try:
            if file_bytes:
                self._process_file(adapter, file_bytes, title, link)

            self.db_client.save_to_db(
                title, self.db_client.document(adapter), upsert=True
            )
        except Exception as _exc:
            raise DropItem(f"Failed to process item: {item}")

        return item

    def _process_file(self, adapter, file_bytes, title, link):
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        adapter["file_hash"] = file_hash
        try:
            existing = self.db_client.find_one(
                {"title": title},
                {"_id": 0, "file_hash": 1, "link": 1, "file_path": 1},
            )
        except Exception:
            logger.error(
                "Dropping record - failed to fetch existing record for title: %s",
                title,
                extra={"event": "dropped_item"},
            )
            raise

        if (
            existing is not None
            and existing.get("file_hash") == file_hash
            and existing.get("link") == link
        ):
            adapter["file_path"] = existing.get("file_path")
            logger.info(
                "File data did not change - skipping upload: %s at %s",
                title,
                adapter["file_path"],
            )
            return

        key = self.s3_client.object_key(adapter)
        self.s3_client.put_object(key, file_bytes)
        adapter["file_path"] = f"{self.s3_client.bucket}/{key}"
        logger.info("Uploaded %s to %s", title, adapter["file_path"])

    @staticmethod
    def _validate_required_fields(adapter, required_fields):
        for field_name in required_fields:
            if not adapter.get(field_name):
                return field_name
        return None
