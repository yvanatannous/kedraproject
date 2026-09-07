import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from dagster import AssetExecutionContext, Config, asset

from .transform import load_config, run_transformation

# Scrapy project root (contains scrapy.cfg): <repo>/kedrascraper
SCRAPY_PROJECT_DIR = Path(__file__).resolve().parents[2] / "kedrascraper"


class DateRangeConfig(Config):
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD


@asset
def landing_zone(context: AssetExecutionContext, config: DateRangeConfig) -> dict:
    """Ingestion: run the Scrapy spider to populate the landing bucket + collection."""
    cmd = [
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "kedra",
        "-a",
        f"start_date={config.start_date}",
        "-a",
        f"end_date={config.end_date}",
        "-s",
        "LOG_FILE=",  # route logs to the pipe instead of scrapy.log
    ]
    context.log.info("Running %s (cwd=%s)", " ".join(cmd), SCRAPY_PROJECT_DIR)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        cmd,
        cwd=SCRAPY_PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    for line in process.stdout:
        context.log.info(line.rstrip())
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Scrapy ingestion failed (exit {process.returncode})")
    return {"start_date": config.start_date, "end_date": config.end_date}


@asset(deps=[landing_zone])
def curated_zone(context: AssetExecutionContext, config: DateRangeConfig) -> dict:
    """Transformation: clean HTML, recompute hash, rename to identifier.ext, write curated zone."""
    start = date.fromisoformat(config.start_date)
    end = date.fromisoformat(config.end_date)
    stats = run_transformation(start, end, load_config(), log=context.log)
    context.add_output_metadata(stats)
    return stats
