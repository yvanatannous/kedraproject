"""Landing Zone -> Curated Zone transformation.

Reads decision metadata + files from the landing MinIO bucket, cleans HTML content
(keeping only ``div.col-sm-9``), renames each file to ``<identifier>.<ext>`` where the
identifier is the decision link slug, recomputes the hash for transformed HTML, and
writes the files + metadata to the curated bucket and collection.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .clients.db_client import DATE_FORMATS, DbClient
from .clients.s3_client import S3Client

logger = logging.getLogger(__name__)

HTML_TYPES = {"html"}
CONTENT_SELECTOR = "div.col-sm-9"

def load_config() -> dict:
    return {
        "mongo_uri": os.environ.get("MONGO_URI", "mongodb://localhost:27017"),
        "mongo_db": os.environ.get("MONGO_DATABASE", "kedrascraper"),
        "source_collection": os.environ.get("MONGO_COLLECTION", "decisions"),
        "target_collection": os.environ.get("MONGO_CURATED_COLLECTION", "decisions_curated"),
        "endpoint": os.environ.get("MINIO_ENDPOINT", "http://localhost:9000"),
        "access_key": os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        "secret_key": os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        "region": os.environ.get("MINIO_REGION", "us-east-1"),
        "curated_bucket": os.environ.get("MINIO_CURATED_BUCKET", "curated"),
    }


def _link_slug(link: str | None) -> str:
    name = urlparse(link or "").path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _clean_html(raw: bytes) -> bytes:
    soup = BeautifulSoup(raw.decode("utf-8", errors="ignore"), "lxml")
    node = soup.select_one(CONTENT_SELECTOR)
    content = str(node) if node else soup.get_text(" ", strip=True)
    return content.encode("utf-8")


def run_transformation(start: date, end: date, cfg: dict | None = None, log=None) -> dict:
    cfg = cfg or load_config()
    log = log or logger
    db = DbClient(cfg)
    db.open()
    s3 = S3Client(cfg)
    s3.open()

    stats = {"matched": 0, "transformed": 0, "copied": 0, "skipped": 0, "unparsable_dates": 0, "failed": 0}
    try:
        stats["unparsable_dates"] = db.count_unparsable_dates()
        if stats["unparsable_dates"]:
            log.warning(
                "%d document(s) have a date matching none of %s; they cannot match any "
                "range and will never be curated.",
                stats["unparsable_dates"],
                ", ".join(DATE_FORMATS),
            )

        for doc in db.find_in_date_range(start, end):
            stats["matched"] += 1

            file_path = doc.get("file_path")
            
            if not file_path:
                stats["skipped"] += 1
                log.warning("No file_path for %s; skipping", doc.get("title"))
                continue

            ext = doc.get("content_type") or "bin"
            is_html = ext in HTML_TYPES

            identifier = doc.get("identifier") or _link_slug(doc.get("link"))
            new_key = f"{identifier}.{ext}"

            if is_html:
                raw = s3.get_by_path(file_path)

                if raw is None:
                    stats["failed"] += 1
                    continue

                new_bytes = _clean_html(raw)
                new_hash = hashlib.sha256(new_bytes).hexdigest()
                new_path = s3.put_object(new_key, new_bytes)
            else:
                # Content is unchanged, so copy server-side instead of
                # downloading and re-uploading the same bytes.
                new_hash = doc.get("file_hash")
                new_path = s3.copy_by_path(file_path, new_key)

            if new_path is None:
                stats["failed"] += 1
                continue

            stats["transformed" if is_html else "copied"] += 1

            metadata = {k: v for k, v in doc.items() if k != "_id"}
            metadata.update(
                {
                    "identifier": identifier,
                    "file_path": new_path,
                    "file_hash": new_hash,
                    "source_file_path": file_path,
                    "transformed_at": datetime.now(timezone.utc),
                }
            )
            db.save_curated(identifier, metadata)
            log.info("Curated %s -> %s", file_path, new_path)
    finally:
        db.close()

    log.info("Transformation summary: %s", stats)
    return stats


def _parse_args():
    parser = argparse.ArgumentParser(description="Transform landing-zone decisions into the curated zone.")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    run_transformation(date.fromisoformat(args.start), date.fromisoformat(args.end))


if __name__ == "__main__":
    main()
