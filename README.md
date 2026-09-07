# Kedra pipeline

This repository runs the Scrapy ingestion app and the transformation app as a
single Dagster code location. The deployment follows Dagster's official
[Docker Compose guide](https://docs.dagster.io/deployment/oss/deployment-options/docker):
the webserver and daemon share a control-plane image with no user code, the
code server has its own image, PostgreSQL stores Dagster state, and the daemon
launches every run in a separate container.

## Start the deployment

Docker Desktop must be running with Linux containers enabled.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The local endpoints are:

- Dagster UI: <http://localhost:3000>
- MinIO API: <http://localhost:9000>
- MinIO console: <http://localhost:9001>
- MongoDB: `mongodb://localhost:27017`

Edit `.env` before starting to change credentials, host ports, collection and
bucket names, or the default Scrapy partition size. The checked-in values are
development defaults; use secret-managed credentials outside local development.

## Environment variables

All of these live in `.env` (copied from `.env.example`). Compose passes the
application ones into the container that runs the spider and the transformation.

Dagster:

| Variable                    | Default              | What it does                                |
| --------------------------- | -------------------- | ------------------------------------------- |
| `DAGSTER_POSTGRES_USER`     | `dagster`            | User for the Dagster metadata database.     |
| `DAGSTER_POSTGRES_PASSWORD` | `dagster`            | Password for the Dagster metadata database. |
| `DAGSTER_POSTGRES_DB`       | `dagster`            | Name of the Dagster metadata database.      |
| `DAGSTER_POSTGRES_HOST`     | `dagster_postgresql` | Host of the Dagster metadata database.      |
| `DAGSTER_WEB_PORT`          | `3000`               | Host port for the Dagster UI.               |

MongoDB (metadata):

| Variable                   | Default                   | What it does                                                                               |
| -------------------------- | ------------------------- | ------------------------------------------------------------------------------------------ |
| `MONGO_URI`                | `mongodb://mongodb:27017` | Connection string. Use `mongodb://localhost:27017` when running the spider outside Docker. |
| `MONGO_DATABASE`           | `kedrascraper`            | Database used by the spider and the transformation.                                        |
| `MONGO_COLLECTION`         | `decisions`               | Landing collection the spider writes to.                                                   |
| `MONGO_CURATED_COLLECTION` | `decisions_curated`       | Curated collection the transformation writes to.                                           |
| `MONGO_PORT`               | `27017`                   | Host port MongoDB is published on.                                                         |

MinIO (files):

| Variable               | Default             | What it does                                                                         |
| ---------------------- | ------------------- | ------------------------------------------------------------------------------------ |
| `MINIO_ENDPOINT`       | `http://minio:9000` | S3 endpoint. Use `http://localhost:9000` when running the spider outside Docker.     |
| `MINIO_ROOT_USER`      | `minioadmin`        | MinIO user. Passed to the app as `MINIO_ACCESS_KEY`.                                 |
| `MINIO_ROOT_PASSWORD`  | `minioadmin`        | MinIO password. Passed to the app as `MINIO_SECRET_KEY`.                             |
| `MINIO_BUCKET`         | `decisions`         | Landing bucket for the raw files. Created on startup by the `createbuckets` service. |
| `MINIO_CURATED_BUCKET` | `curated`           | Curated bucket for the transformed files.                                            |
| `MINIO_REGION`         | `us-east-1`         | Region passed to the S3 client.                                                      |
| `MINIO_API_PORT`       | `9000`              | Host port for the MinIO API.                                                         |
| `MINIO_CONSOLE_PORT`   | `9001`              | Host port for the MinIO console.                                                     |

Application:

| Variable              | Default | What it does                                                                                                                                                                 |
| --------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PARTITION_SIZE`      | `1m`    | Size of each date window the spider splits the range into. Accepts `y`, `m`, `w`, `d`, for example `7d` or `2y 1m`. `ARCHITECTURE.md` explains why one month is the default. |
| `DISABLE_SCRAPY_LOGS` | `false` | When `true`, hides every `scrapy.*` and `twisted.*` log record so only the application logs are left. Useful when the throttle and log-stats noise makes a run hard to read. |

## Run the two assets

In the Dagster UI, open **Assets**, select `landing_zone` and `curated_zone`,
and choose **Materialize selected**.

```yaml
ops:
  landing_zone:
    config:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
  curated_zone:
    config:
      start_date: "2024-01-01"
      end_date: "2024-01-31"
```

`landing_zone` writes source metadata/files to MongoDB and the `decisions`
bucket. After it succeeds, `curated_zone` writes transformed metadata/files to
the curated collection and bucket.

## Debugging a run

All application logs are written as one JSON object per line, so a run can be
filtered with a text search instead of being read top to bottom. In Dagster the
spider output is piped into the run logs of `landing_zone`.
Every failure log carries an `event` field, so you can look up exactly why
something is missing:

| `event`          | Meaning                                                                                                                                                                              | Useful fields                                                              |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| `records_missed` | A search request failed after its retries, so a whole partition/body cell is incomplete. Logged once when it fails, and again at the end of the run with how many records were lost. | `trace_id`, `url`, `code`, `error`, `missed_records`                       |
| `download_fail`  | The decision document could not be downloaded, or the HTML page had no body to fall back on. The item is still saved with its metadata.                                              | `url`, `code`, `error`                                                     |
| `dropped_item`   | The pipeline threw the item away: a missing `title` or `link`, or a MinIO/MongoDB write that failed.                                                                                 | `record` (when a field is missing), and the message says which step failed |

The `trace_id` on a `records_missed` event links the request that failed to the
line at the end of the run saying how many records that cell lost, so you can
always trace a gap in the counts back to the URL that caused it.

Every run also ends with a `Run summary` line, which is the quickest way to see
whether anything went wrong:

It reports the total records the site advertised, how many were scraped, how
many were missed, how many were dropped, how many document downloads failed, and
the database counters (`created_db_items`, `updated_db_items` and
`duplicate_title_entries`).

If the Scrapy internals get in the way, set `DISABLE_SCRAPY_LOGS=true` to keep
only the application logs. Leave it `false` when you want to see the AutoThrottle
delays and the retry attempts.

