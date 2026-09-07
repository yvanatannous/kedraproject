import logging
import logging.config

from pythonjsonlogger import jsonlogger


class NoScrapyFilter(logging.Filter):
    def filter(self, record):
        return not record.name.startswith(("scrapy", "twisted"))


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,

    "filters": {
        "no_scrapy": {"()": NoScrapyFilter},
    },

    "formatters": {
        "json": {
            "()": jsonlogger.JsonFormatter,
            "format": (
                "%(asctime)s "
                "%(levelname)s "
                "%(name)s "
                "%(message)s"
            ),
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },

    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}


def configure_logging(disable_scrapy=True):
    LOGGING_CONFIG["handlers"]["console"]["filters"] = ["no_scrapy"] if disable_scrapy else []
    logging.config.dictConfig(LOGGING_CONFIG)


def get_logger(name):
    return logging.getLogger(name)
