import re
import uuid
from collections import Counter
from datetime import date, timedelta
from typing import NamedTuple
from urllib.parse import parse_qs, urlencode, urlparse

import scrapy
from dateutil.relativedelta import relativedelta
from scrapy import signals
from scrapy.spidermiddlewares.httperror import HttpError

from kedrascraper.items import DatePartitionItem, DecisionItem
from kedrascraper.logging_config import get_logger

logger = get_logger(__name__)


class PartitionBodyKey(NamedTuple):
    """Identifies one search cell: a body within a single date partition."""

    body: str
    from_date: str
    to_date: str


class KedraSpider(scrapy.Spider):
    name = "kedra"
    allowed_domains = ["workplacerelations.ie"]

    SEARCH_URL = "https://www.workplacerelations.ie/en/search/"

    BODIES = [
        {"value": "2", "label": "Employment Appeals Tribunal"},
        {"value": "1", "label": "Equality Tribunal"},
        {"value": "3", "label": "Labour Court"},
        {"value": "15376", "label": "Workplace Relations Commission"},
    ]

    # Document types served as downloadable files; anything else is stored as HTML.
    FILE_EXTENSIONS = {"pdf", "doc", "docx"}

    def __init__(self, start_date, end_date, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # STATS
        self.records_found = 0
        self.document_download_failed = 0
        self.found_by_body_partition = Counter()
        self.scraped_by_body_partition = Counter()
        self.errored_partitions = {}

        # INPUT
        self.start_date = self._parse_date(start_date, "start_date")
        self.end_date = self._parse_date(end_date, "end_date")
        if self.start_date > self.end_date:
            raise ValueError(
                f"start_date {self.start_date} is after end_date {self.end_date}."
            )

    # ITEM SCRAPED SIGNAL
    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        partition_size = crawler.settings.get("PARTITION_SIZE")
        spider.partition_size = spider._parse_period(partition_size)
        crawler.signals.connect(spider._on_item_scraped, signal=signals.item_scraped)
        logger.info(
            "Spider initialised",
            extra={
                "start_date": spider.start_date.isoformat(),
                "end_date": spider.end_date.isoformat(),
                "partition_size": partition_size,
            },
        )
        return spider

    def _on_item_scraped(self, item, response, spider):
        body = getattr(item, "body", None)
        partition = getattr(item, "partition_date", None)
        if body and partition:
            key = PartitionBodyKey(
                body, partition.partition_start, partition.partition_end
            )
            self.scraped_by_body_partition[key] += 1

    # START
    async def start(self):
        partitions = list(self._partitions())
        total_partitions = len(partitions)
        total_bodies = len(self.BODIES)
        for partition_index, (period_start, period_end) in enumerate(
            partitions, start=1
        ):
            from_date = self._format_date(period_start)
            to_date = self._format_date(period_end)
            for body_index, body in enumerate(self.BODIES, start=1):
                logger.info(
                    "Processing partition %d/%d of body %d/%d",
                    partition_index,
                    total_partitions,
                    body_index,
                    total_bodies,
                    extra={
                        "partition_num": partition_index,
                        "partition_total": total_partitions,
                        "body_num": body_index,
                        "body_total": total_bodies,
                        "body_name": body["label"],
                        "from_date": from_date,
                        "to_date": to_date,
                    },
                )

                query = urlencode(
                    {
                        "decisions": 1,
                        "from": from_date,
                        "to": to_date,
                        "body": body["value"],
                    }
                )
                url = f"{self.SEARCH_URL}?{query}"

                yield scrapy.Request(
                    url,
                    callback=self.parse_page,
                    errback=self._on_search_error,
                    cb_kwargs={
                        "body": body,
                        "from_date": from_date,
                        "to_date": to_date,
                        "partition_index": partition_index,
                        "total_partitions": total_partitions,
                        "body_index": body_index,
                        "total_bodies": total_bodies,
                    },
                )

    def parse_page(
        self,
        response,
        body,
        from_date,
        to_date,
        partition_index,
        total_partitions,
        body_index,
        total_bodies,
    ):
        page = parse_qs(urlparse(response.url).query).get("pageNumber", ["1"])[0]

        if page == "1":
            total_records = self._parse_total_records(response)

            logger.info(
                "Total records found for partition %d/%d for body %d/%d : %d",
                partition_index,
                total_partitions,
                body_index,
                total_bodies,
                total_records,
                extra={
                    "partition_num": partition_index,
                    "partition_total": total_partitions,
                    "body_num": body_index,
                    "body_total": total_bodies,
                    "body_name": body["label"],
                    "from_date": from_date,
                    "to_date": to_date,
                },
            )

            self.records_found += total_records
            # total_records is constant across a partition's pages, so assign rather than accumulate.
            self.found_by_body_partition[
                PartitionBodyKey(body["label"], from_date, to_date)
            ] = total_records

        page_records = response.css("div.item-list.search-list li.each-item")

        logger.info(
            "Scraping records of partition %d/%d for body %d/%d - page : %s - records found on page %d",
            partition_index,
            total_partitions,
            body_index,
            total_bodies,
            page,
            len(page_records),
            extra={
                "partition_num": partition_index,
                "partition_total": total_partitions,
                "body_num": body_index,
                "body_total": total_bodies,
                "body": body["label"],
                "from_date": from_date,
                "to_date": to_date,
                "page": page,
                "page_records_found": len(page_records),
            },
        )

        for row in page_records:
            href = row.css("div.bottom-ref a.btn::attr(href)").get()
            title = row.css("h2.title::attr(title)").get()
            item = DecisionItem(
                title=title,
                date=row.css("span.date::text").get(),
                description=" ".join(row.css("p.description::text").getall()).strip(),
                link=href,
                body=body["label"],
                partition_date=DatePartitionItem(
                    partition_start=from_date, partition_end=to_date
                ),
            )
            if not href or not title:
                yield item
                continue
            yield response.follow(
                href,
                callback=self.download_document,
                cb_kwargs={
                    "item": item,
                    "body": body,
                    "from_date": from_date,
                    "to_date": to_date,
                    "partition_index": partition_index,
                    "total_partitions": total_partitions,
                    "body_index": body_index,
                    "total_bodies": total_bodies,
                    "page": page,
                },
                errback=self._on_download_error,
                dont_filter=True,
            )

        next_page = response.css("nav.pages a.next::attr(href)").get()

        if next_page:
            yield response.follow(
                next_page,
                callback=self.parse_page,
                errback=self._on_search_error,
                cb_kwargs={
                    "body": body,
                    "from_date": from_date,
                    "to_date": to_date,
                    "partition_index": partition_index,
                    "total_partitions": total_partitions,
                    "body_index": body_index,
                    "total_bodies": total_bodies,
                },
            )

    def download_document(
        self,
        response,
        item,
        body,
        from_date,
        to_date,
        partition_index,
        total_partitions,
        body_index,
        total_bodies,
        page,
    ):
        logger.info(
            "Downloading document with URl : %s for record case : %s",
            response.url,
            item.title,
            extra={
                "item": item,
                "partition_num": partition_index,
                "partition_total": total_partitions,
                "body_num": body_index,
                "body_total": total_bodies,
                "body": body["label"],
                "from_date": from_date,
                "to_date": to_date,
                "page": page,
            },
        )
        download_href = response.css("a.download::attr(href)").get()
        if download_href:
            yield scrapy.Request(
                response.urljoin(download_href),
                callback=self._save_file,
                cb_kwargs={"item": item},
                errback=self._on_download_error,
                dont_filter=True,
            )
            return
        item.content_type = "html"
        item.file_bytes = (response.css("body").get() or response.text).encode("utf-8")
        if not item.file_bytes:
            self.document_download_failed += 1
            logger.error(
                "Downloading document failed for item: %s",
                item.title,
                extra={
                    "event": "download_fail",
                    "url": response.url,
                    "code": None,
                    "error": "No body element found in html document",
                },
            )
        yield item

    def closed(self, reason):
        total_records_missed = 0
        for key, trace_id in self.errored_partitions.items():
            found = self.found_by_body_partition.get(key, 0)
            scraped = self.scraped_by_body_partition.get(key, 0)
            total_records_missed += found - scraped
            logger.error(
                "Records missed for %s [%s to %s]: %d/%d",
                key.body,
                key.from_date,
                key.to_date,
                found - scraped,
                found,
                extra={
                    "event": "records_missed",
                    "trace_id": trace_id,
                    "missed_records": found - scraped,
                },
            )
        stats = self.crawler.stats.get_stats()
        logger.info(
            "Run summary",
            extra={
                "successfully_scraped_records_total": stats.get(
                    "item_scraped_count", 0
                ),
                "records_missed": total_records_missed,
                "records_dropped": stats.get("item_dropped_count", 0),
                "total_records_found": self.records_found,
                "document_download_fails": self.document_download_failed,
                "db_stats": {
                    "created_db_items": stats.get("created_db_items", 0),
                    "updated_db_items": stats.get("updated_db_items", 0),
                    "duplicate_title_entries": stats.get("duplicate_title_entries", {}),
                },
            },
        )

    def _on_search_error(self, failure):
        request = failure.request

        cb = request.cb_kwargs
        key = PartitionBodyKey(cb["body"]["label"], cb["from_date"], cb["to_date"])
        trace_id = str(uuid.uuid4())
        self.errored_partitions[key] = trace_id

        logger.error(
            "Search request errored - Records missed for %s [%s to %s]",
            key.body,
            key.from_date,
            key.to_date,
            extra={
                "event": "records_missed",
                "trace_id": trace_id,
                "url": request.url,
                "code": self._failure_status(failure),
                "error": repr(failure.value),
            },
        )

    def _on_download_error(self, failure):
        self.document_download_failed += 1
        request = failure.request
        item = request.cb_kwargs.get("item")
        logger.error(
            "Downloading document failed for item: %s",
            item.title,
            extra={
                "event": "download_fail",
                "url": request.url,
                "code": self._failure_status(failure),
                "error": repr(failure.value),
            },
        )
        yield item

    # scraper utils
    def _partitions(self):
        """Yield (start, end) date pairs, one per partition_size window within the range."""
        current = self.start_date
        while current <= self.end_date:
            next_start = current + self.partition_size
            period_end = min(next_start - timedelta(days=1), self.end_date)
            yield current, period_end
            current = next_start

    def _save_file(self, response, item):
        item.content_type = self._file_extension(response)
        item.file_bytes = response.body
        yield item

    def _file_extension(self, response):
        path = urlparse(response.url).path.lower()
        if "." in path:
            ext = path.rsplit(".", 1)[-1]
            if ext in self.FILE_EXTENSIONS:
                return ext
        content_type = (
            response.headers.get("Content-Type", b"").decode(errors="ignore").lower()
        )
        if "pdf" in content_type:
            return "pdf"
        if "wordprocessingml" in content_type:
            return "docx"
        if "msword" in content_type:
            return "doc"
        return "bin"

    @staticmethod
    def _parse_date(value, field):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {field} (expected YYYY-MM-DD): {exc}") from exc

    @staticmethod
    def _parse_period(value):
        """Parse a period string like '2y 1m 10d' into a relativedelta."""
        units = {"y": "years", "m": "months", "w": "weeks", "d": "days"}
        matches = re.findall(r"(\d+)\s*([ymwd])", value.lower())
        if not matches:
            raise ValueError(
                f"Invalid PARTITION_SIZE setting (expected e.g. '2y 1m'): {value!r}"
            )
        kwargs = {}
        for number, unit in matches:
            kwargs[units[unit]] = kwargs.get(units[unit], 0) + int(number)
        return relativedelta(**kwargs)

    @staticmethod
    def _parse_total_records(response):
        total_result_text = response.xpath("string(//div[@class='searchhead'])").get()
        match = re.search(r"of\s+([\d,]+)\s+results", total_result_text or "")
        return int(match.group(1).replace(",", "")) if match else 0

    @staticmethod
    def _format_date(value):
        return f"{value.day}/{value.month}/{value.year}"

    @staticmethod
    def _failure_status(failure):
        return failure.value.response.status if failure.check(HttpError) else None
