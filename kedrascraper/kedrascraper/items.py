# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

from dataclasses import dataclass, field


@dataclass
class DatePartitionItem:
    partition_start: str
    partition_end: str


@dataclass
class DecisionItem:
    title: str
    date: str
    description: str
    link: str
    body: str
    partition_date: DatePartitionItem
    content_type: str | None = None
    file_hash: str | None = None
    file_path: str | None = None
    file_bytes: bytes | None = field(
        default=None, repr=False
    )  # transient; not persisted
