# Architecture

The pipeline has two Dagster assets. `landing_zone` runs the Scrapy spider and writes the raw
files to MinIO and the metadata to MongoDB. `curated_zone` cleans those files and writes them to
the curated bucket and collection.

The spider splits the work into cells. One cell is one date partition for one decision body
(there are 4 bodies). For each cell it runs the search, walks the result pages, and then
downloads either the linked document (PDF/DOC/DOCX) or the decision HTML.

## Date partition size

The default is `PARTITION_SIZE = "1m"` (one month). It can be changed with an env variable and
accepts values like `"2y 1m 10d"`.

I picked one month for three reasons:

1. The search is paginated, not bulk. A small window means each cell is only a few pages, so a
   cell is cheap to lose and cheap to retry.
2. It makes failures easy to locate. Each cell stores how many records the site said it had and
   how many were actually scraped, so at the end of the run I can report exactly which month and
   which body lost records, with a `trace_id`. The re-run is then just that one cell.
3. It fits how the site publishes. A month is big enough to avoid a lot of empty requests and
   small enough that no cell has too many pages.

## Retries and rate limiting

Retries are on for `429`, `408` and `5xx` (`500/502/503/504/522/524`), with `RETRY_TIMES = 5`.
Those are the transient cases: server errors, timeouts and rate limiting. Other 4xx errors are
not retried because retrying them would not help.

For rate limiting I use AutoThrottle (start delay 1s, max delay 30s, target concurrency 2) with
`CONCURRENT_REQUESTS_PER_DOMAIN = 4` as a hard cap. The delay adjusts to the response times of
the site, so it slows down on its own when the site struggles instead of me guessing a fixed
delay. I also keep `ROBOTSTXT_OBEY = True` and a User-Agent with a contact address.

If the retries run out, the crawl does not stop. The errbacks handle it: a failed search marks
that cell as incomplete, and a failed document download still yields the item so the metadata is
kept and the failure is counted. Nothing disappears quietly. The summary at the end of the run
prints records found, scraped, missed and dropped.

## Deduplication

There are three levels.

1. Requests. Scrapy's default filter drops repeated search and pagination URLs. Detail and
   download requests use `dont_filter=True`, because the same document can legitimately be
   reached from more than one cell.
2. Records. MongoDB has a unique index on `title` and every write is an
   `update_one(..., upsert=True)`. So re-running an overlapping date range updates the records
   instead of duplicating them, which makes the pipeline safe to re-run.
3. Content. Each file is hashed with SHA-256. If the stored `file_hash` and `link` are both
   unchanged, I skip the upload and keep the existing `file_path`. Unchanged documents only cost
   one Mongo read.

One thing to note: I used `title` as the identifier. If the site publishes two different cases
with the same title, they end up as one record and the file is overwritten. That is why the
number of successfully scraped items can be higher than the number of records in the database.
The run reports these under `duplicate_title_entries`.
