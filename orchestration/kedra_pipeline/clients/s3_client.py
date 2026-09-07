"""MinIO/S3 access for the Landing Zone -> Curated Zone transformation.

Landing-zone objects record their location as a single ``bucket/key`` string (see
the scraper's upload pipeline), so this client owns both halves of that
convention: decoding it on read and rebuilding it on write.
"""
from __future__ import annotations

import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class S3Client:
    """Reads landing-zone objects and writes curated-zone objects.

    ``bucket`` is the curated (write) bucket. Reads instead take their bucket from
    the ``file_path`` recorded on each decision, because the landing zone is
    organised per scrape target and is not necessarily a single bucket.
    """

    def __init__(self, cfg: dict):
        self.endpoint = cfg["endpoint"]
        self.access_key = cfg["access_key"]
        self.secret_key = cfg["secret_key"]
        self.region = cfg["region"]
        self.bucket = cfg["curated_bucket"]
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )

    def open(self):
        """Fail fast if MinIO is unreachable or the curated bucket is missing."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            raise RuntimeError(
                f"MinIO bucket '{self.bucket}' check failed (HTTP {status}): {exc}. "
                "Verify the bucket exists and credentials are correct "
                "(the docker-compose 'createbuckets' service provisions it)."
            ) from exc
        except BotoCoreError as exc:
            logger.error("Cannot reach MinIO at %s: %s", self.endpoint, exc)
            raise RuntimeError(
                f"Cannot reach MinIO at {self.endpoint}: {exc}. "
                "Is MinIO running? Start it with `docker compose up -d`."
            ) from exc

    @staticmethod
    def split_path(file_path: str) -> tuple[str, str]:
        """Split a stored ``bucket/key`` path. The key itself may contain slashes."""
        bucket, _, key = file_path.partition("/")
        return bucket, key

    def path_for(self, key: str) -> str:
        """Inverse of :meth:`split_path`, for the curated bucket."""
        return f"{self.bucket}/{key}"

    def get_object(self, bucket: str, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to read %s/%s: %s", bucket, key, exc)
            return None

    def get_by_path(self, file_path: str) -> bytes | None:
        """Read the object a decision's ``file_path`` points at."""
        return self.get_object(*self.split_path(file_path))

    def copy_object(self, src_bucket: str, src_key: str, key: str) -> str | None:
        try:
            self.client.copy_object(
                Bucket=self.bucket,
                Key=key,
                CopySource={"Bucket": src_bucket, "Key": src_key},
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Failed to copy %s/%s to %s/%s: %s",
                src_bucket,
                src_key,
                self.bucket,
                key,
                exc,
            )
            return None
        return self.path_for(key)

    def copy_by_path(self, file_path: str, key: str) -> str | None:
        """Copy the object a decision's ``file_path`` points at into the curated bucket."""
        src_bucket, src_key = self.split_path(file_path)
        return self.copy_object(src_bucket, src_key, key)

    def put_object(self, key: str, body: bytes) -> str | None:
        """Write to the curated bucket, returning the stored ``bucket/key`` path."""
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
        except (ClientError, BotoCoreError) as exc:
            logger.error("Failed to upload to %s/%s: %s", self.bucket, key, exc)
            return None
        return self.path_for(key)
