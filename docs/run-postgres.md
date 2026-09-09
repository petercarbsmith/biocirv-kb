---
title: Configure Postgres backend
description: Choose and operate a supported PostgreSQL server with pgvector for Sci RAG Kit.
---

# Configure Postgres backend

Choose Docker, a local server, or Cloud SQL, then connect Sci RAG Kit to PostgreSQL with pgvector.
Use a disposable database for destructive tests.

<div class="srag-meta-strip">
  <div><strong>You'll build</strong>A running PostgreSQL 16 to 18 with pgvector</div>
  <div><strong>You'll need</strong>A project checkout and one server source</div>
  <div><strong>Time</strong>About 5 minutes locally, longer for first Cloud startup</div>
  <div><strong>Tested with</strong>v0.5</div>
</div>

## Before you start

| Requirement | Why | Check |
|---|---|---|
| A Sci RAG Kit checkout or generated project | `make setup` and the helper scripts live in it | `ls Makefile` |
| One supported environment manager | It supplies the command runner | uv, pixi, conda, or venv + pip was selected during setup |
| PostgreSQL 16 through 18 with pgvector | The application, migrations, and tests all need it | `psql --version` and `CREATE EXTENSION vector` |

`SCI_RAG_DB_BACKEND` selects the backend used by `make db-up`, `make db-down`, and `make setup`. Set it to `docker` for the Compose service or `local` for `scripts/local_postgres.py`.
Every project retains its supported backends, with one default selected by the environment manager. `SCI_RAG_DATABASE_URL` controls the application. `SCI_RAG_TEST_DATABASE_URL` controls the destructive test suite. Selecting a backend does not rewrite either URL.

## Recommended defaults

Docker is the template default and matches the PostgreSQL 16 service in CI. Generated pixi and conda projects default to `local` because their manifests bundle PostgreSQL and pgvector from conda-forge.

Any environment manager can select `local` when PostgreSQL 16 through 18 and pgvector are on `PATH`, including through Postgres.app. Advanced setup can retain the optional Cloud helper. Quick keeps the default and removes the helper.

| Environment manager | Default value | What launches | Also selectable |
|---|---|---|---|
| uv | `docker` | the Compose service | `local`, `cloud` |
| pixi | `local` | the bundled conda-forge server | `docker`, `cloud` |
| conda | `local` | the bundled conda-forge server | `docker`, `cloud` |
| venv + pip | `docker` | the Compose service | `local`, `cloud` |

`cloud` is selectable only in projects that retained the Cloud helper.

## Run Postgres in Docker

If you use the template checkout with no backend override, run:

```console title="Terminal"
$ make setup
```

This synchronizes dependencies, starts the selected backend, and applies migrations. With the default backend, Compose starts on port `5433`.

Generated pixi or conda projects default to the bundled server. To use Compose instead:

```console title="Terminal"
$ SCI_RAG_DB_BACKEND=docker make setup
```

Stop the backend when finished:

```console title="Terminal"
$ make db-down
```

<div class="srag-checkpoint" markdown>
**Checkpoint: the database is reachable**

Run `uv run sci-rag doctor`. The database and schema checks should report
healthy. An empty corpus is fine at this point.
</div>

## Run Postgres from conda-forge

Generated pixi and conda projects declare `postgresql` and `pgvector` in the manifest. Their Makefile sets `SCI_RAG_DB_BACKEND=local`, so `make setup` starts `scripts/local_postgres.py`:

```console title="Terminal"
$ make setup
```

The helper keeps data under `.pgdata/`, listens on loopback, and uses trust authentication. This is the machine-local development server. Do not use it for deployment.

```console title="Terminal"
$ make db-down
```

<div class="srag-checkpoint" markdown>
**Checkpoint: the server came from the selected manager**

`ls .pgdata` should show the data directory. `uv run sci-rag doctor` should
report a healthy database and current schema.
</div>

## Point at a system PostgreSQL

`local` runs `scripts/local_postgres.py` with the PostgreSQL installation on `PATH`, either the bundled conda-forge build or a user-installed system server. Any environment manager can use it when `initdb`, `pg_ctl`, and `psql` from PostgreSQL 16 through 18 are available.

Postgres.app is supported on macOS. Add its versioned `bin` directory to `PATH`, then run:

```console title="Terminal"
$ SCI_RAG_DB_BACKEND=local make setup
```

The helper creates the `sci_rag` database and enables pgvector. To use an existing compatible server, set the application URL and apply migrations:

```dotenv title="~/.env"
SCI_RAG_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/sci_rag
```

```console title="Terminal"
$ uv sync
$ uv run sci-rag db upgrade
```

<div class="srag-checkpoint" markdown>
**Checkpoint: the schema is on the intended server**

`uv run sci-rag doctor` should report the expected host and a current schema.
An exported URL takes precedence over the value in `.env`.
</div>




## Use a disposable test database

The integration and server fixtures drop and recreate application tables in `SCI_RAG_TEST_DATABASE_URL`, then truncate them between tests. Point that URL at a disposable database. A skipped database suite does not pass.

```dotenv title="~/.env"
SCI_RAG_TEST_DATABASE_URL=postgresql+asyncpg://sci_rag:sci_rag@localhost:5433/sci_rag_test
```

## Confirm the supported versions

Supported servers are PostgreSQL 16 through 18. CI proves 16 through the
container service and the Docker-free workflow proves the current conda-forge
resolution, PostgreSQL 18, on Linux and Apple silicon. [ADR 0008](adr/0008-supported-postgresql-versions.md)
records the range and its reversal conditions.

## Next steps

- Ingest the demo corpus and inspect retrieval: [Quickstart](quickstart.md)
- Diagnose a database that will not start: [Troubleshooting](troubleshooting.md)
- Back up, snapshot, and restore a corpus: [Operate a live corpus](operations.md)
