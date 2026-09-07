import logging
import hashlib

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self, settings):
        self.endpoint = settings.get("MINIO_ENDPOINT")
        self.access_key = settings.get("MINIO_ACCESS_KEY")
        self.secret_key = settings.get("MINIO_SECRET_KEY")
        self.bucket = settings.get("MINIO_BUCKET")
        self.region = settings.get("MINIO_REGION")

    def open(self):
        try:
            self.client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
            )
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

    def put_object(self, key, body):
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "Dropping record - failed to upload file to bucket: %s",
                key,
                extra={"event": "dropped_item"},
            )
            raise

    @staticmethod
    def object_key(adapter):
        body = (adapter.get("body") or "unknown").replace(" ", "-").lower()
        ext = adapter.get("content_type")
        slug = hashlib.sha1(adapter.get("title").encode()).hexdigest()
        return f"{body}/{slug}.{ext}"
