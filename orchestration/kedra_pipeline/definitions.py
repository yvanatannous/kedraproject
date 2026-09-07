from dagster import Definitions

from .assets import curated_zone, landing_zone

defs = Definitions(assets=[landing_zone, curated_zone])
