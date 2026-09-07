import os
from kedrascraper.utils import Utils
from kedrascraper.logging_config import configure_logging

BOT_NAME = "kedrascraper"

SPIDER_MODULES = ["kedrascraper.spiders"]
NEWSPIDER_MODULE = "kedrascraper.spiders"

ADDONS = {}

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Concurrency and throttling settings
CONCURRENT_REQUESTS_PER_DOMAIN = 4

# Retries: cover transient 5xx, timeouts/connection drops,S and 429 (rate limited).
RETRY_ENABLED = True
RETRY_TIMES = 5
RETRY_HTTP_CODES = [429, 500, 502, 503, 504, 408, 522, 524]

# Adaptive rate limiting: speeds up while the server is healthy, backs off when it slows.
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = True

# Search is a stateless GET; skip cookie handling for a small throughput win.
COOKIES_ENABLED = False

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    "kedrascraper.pipelines.StoragePipeline": 300,
}

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "kedrascraper")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "decisions")


MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "decisions")
MINIO_REGION = os.environ.get("MINIO_REGION", "us-east-1")

PARTITION_SIZE = os.environ.get("PARTITION_SIZE", "1m")


# LOGS
LOG_ENABLED = False
DISABLE_SCRAPY_LOGS = Utils.env_bool("DISABLE_SCRAPY_LOGS", False)

configure_logging(disable_scrapy=DISABLE_SCRAPY_LOGS)
