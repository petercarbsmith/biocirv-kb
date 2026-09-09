# Repository instructions for coding agents

## Scope and precedence

This file applies to the entire repository. A more deeply nested `AGENTS.md`, if one is added
later, may refine these instructions for its subtree. Direct user instructions take precedence
over repository guidance.

Treat this file as an operating contract, not as a substitute for the project documentation.
Before changing an unfamiliar subsystem, read the nearest relevant source of truth:

- [README.md](README.md) for the product overview and supported workflows.
- [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution bar.
- [docs/architecture.md](docs/architecture.md) for module boundaries and extension seams.
- [docs/methodology.md](docs/methodology.md) for the scientific and retrieval specification.
- [docs/adr/](docs/adr/) for accepted architectural decisions.
- [.github/workflows/ci.yml](.github/workflows/ci.yml) for the authoritative CI commands.

If documentation and implementation disagree, do not silently choose whichever is convenient.
Determine which one represents intended current behavior, make the smallest coherent correction,
and call out the discrepancy in the handoff or pull request.

## Mission and quality bar

Sci RAG Kit is a Python template for scientific retrieval-augmented generation. It ingests
scientific documents, builds a Postgres-native knowledge graph, performs scoped hybrid retrieval,
generates cited answers, evaluates those answers, and exposes the same behavior through CLI, REST,
and MCP surfaces.

Changes must preserve more than functional output. They must preserve traceability, scientific
honesty, access-control boundaries, citation provenance, reproducibility, and graceful but visible
degradation. Prefer a narrow, well-tested change over a broad cleanup. Never make a metric look
better by weakening a threshold, hiding a failed stage, broadening retrieval scope, or changing the
evaluation target without explicit justification and evidence.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/sci_rag/config.py` | `SCI_RAG_*` runtime settings and credential selection |
| `src/sci_rag/domain.py` and `domain/` | Domain ontology, prompts, retrieval tuning, and evaluation seeds |
| `src/sci_rag/db/` and `migrations/` | SQLAlchemy models, async engine, and Alembic schema history |
| `src/sci_rag/ingest/` | Parsing, structure-aware chunking, manifests, deduplication, and ingestion |
| `src/sci_rag/embed/` and `src/sci_rag/llm/` | Provider interfaces and implementations |
| `src/sci_rag/graph/` | Entity and relationship extraction plus community summaries |
| `src/sci_rag/retrieve/` | Retrieval stages, scope enforcement, fusion, routing, and reranking |
| `src/sci_rag/answer/` | Grounded answer generation, streaming events, and citations |
| `src/sci_rag/evals/` | Retrieval metrics, blind judging, calibration, diffs, and reports |
| `src/sci_rag/server/` | Shared service facade, FastAPI routes, auth, schemas, and MCP tools |
| `src/sci_rag/cli/` | Typer command wiring |
| `tests/unit/` | Offline, database-free behavior and pure logic tests |
| `tests/integration/` | PostgreSQL/pgvector pipeline and data-lifecycle tests |
| `tests/server/` | REST and MCP contract tests over the shared service |
| `tests/docs/` | Presentation checks measured in a browser against the built site |
| `data/demo/` | Synthetic CC0 fixture corpus and manifest |
| `docs/` | User guides, methodology, operations, benchmarks, plans, and ADRs |
| `infra/terraform/` | Optional Google Cloud deployment |

Generated or local-only state belongs in the existing ignored locations such as `.env`, `.venv/`,
`eval_results/`, `site/`, Terraform state, and `data/raw/`, `data/interim/`, or `data/processed/`.
Do not commit secrets, private corpora, downloaded papers, credentials, state files, or local caches.

## Work protocol

1. Inspect `git status`, the relevant code, its tests, and recent history before editing. Preserve
   unrelated user changes and do not reformat unrelated files.
2. Identify the public contract and the architectural invariant affected by the task. For bugs,
   reproduce the failure before changing code when practical.
3. Implement the smallest complete change through an existing facade or extension seam. Avoid new
   infrastructure, dependencies, or abstractions unless the existing design cannot express the
   requirement.
4. Add or update tests at the lowest layer that proves the behavior. Add integration or contract
   coverage when the behavior crosses a database, REST, MCP, CLI, or migration boundary.
5. Update user-facing documentation in the same change when behavior, configuration, output,
   operations, or extension points change. Add an ADR for a new or reversed architectural decision.
6. Run focused checks during iteration, then the appropriate full checks before handoff. Report
   exactly what ran, what was skipped, and why.
7. Review the final diff for accidental artifacts, secrets, unsupported claims, and changes outside
   the requested scope. Commit, push, create pull requests, or deploy only when the task authorizes
   those actions.

Do not claim success from a passing unit test when the changed contract lives at a broader layer.
Do not fix unrelated failures without explaining and scoping the additional work.

## Environment and setup

The supported Python versions are 3.11 and 3.12. Use `uv` for dependency and command execution.
Do not replace `uv.lock` workflows with ad hoc `pip` environments.

```bash
uv sync
make db-up
uv run sci-rag db upgrade
```

`make setup` synchronizes dependencies, starts the backend selected by
`SCI_RAG_DB_BACKEND`, and applies migrations. Docker is the easiest CI-parity setup and the
template default. `local` supports the bundled conda-forge server and any supported system
PostgreSQL, including Postgres.app; `cloud` uses the optional Cloud SQL development helper. Copy
`.env.example` to `.env` only when local runtime configuration is needed. Never print credential
values or include them in logs, fixtures, snapshots, or commits. Prefer
`SCI_RAG_EMBEDDING_PROVIDER=local-hash` for offline development. Real Google credentials are
required only for model-backed embeddings, graph extraction, HyDE, community summaries, and
generated answers.

Tests force the deterministic local embedder, a 64-dimensional test vector, and blank Google
credentials before importing the package. Keep that import-order guarantee intact because vector
dimension is bound when the database models load.

### Database safety

Integration and server tests are destructive to the database named by
`SCI_RAG_TEST_DATABASE_URL`: the session fixture drops and recreates application tables, and
per-test fixtures truncate them. Use only a disposable test database. Never point this variable at
a development corpus, shared database, staging instance, or production instance.

The CI test database is PostgreSQL 16 with pgvector and is named `sci_rag_test`. Local integration
tests skip, with a diagnostic message, when their database is unavailable. A skipped integration
suite is not evidence that database behavior passed. Create a disposable test database for Docker,
local, or Cloud SQL and put only its URL in `SCI_RAG_TEST_DATABASE_URL` before claiming the
database-backed suites passed.

Supported servers are PostgreSQL 16 through 18, per
[docs/adr/0008-supported-postgresql-versions.md](docs/adr/0008-supported-postgresql-versions.md).
The `ci.yml` service proves 16; `docker-free-postgres.yml` proves whatever conda-forge resolves
inside the range, which is 18 today, on linux-64 and osx-arm64. Do not add version-specific SQL
without checking both ends. `scripts/local_postgres.py` runs a server from conda-forge for the pixi
and conda paths; it is a development database on loopback with trust authentication and is never a
deployment path.

## Commands and validation

Run commands from the repository root unless a command explicitly changes directories.

### Fast feedback

```bash
# One test module
uv run pytest tests/unit/test_chunker.py -q

# One test or related group
uv run pytest tests/unit/test_chunker.py -q -k "table"

# Unit tests only
uv run pytest tests/unit -q

# Lint, format check, or type check
make lint
make typecheck
```

Use a test path and `-k` expression relevant to the change. Do not default to disabling warnings,
capturing less output, or updating snapshots merely to make a failure disappear.

### Baseline repository check

```bash
make check
```

This runs Ruff lint and format checks over `src` and `tests`, mypy over `sci_rag`, and pytest. Start
the disposable PostgreSQL service first when integration coverage matters.

### Exact Python CI parity

CI additionally checks `examples` and `scripts`, verifies the lockfile, runs every configured
pre-commit hook, enforces coverage, and runs Python checks on both Python 3.11 and 3.12:

```bash
uv lock --check
uv run ruff check src tests examples scripts
uv run ruff format --check src tests examples scripts
uv run mypy
uv run pytest -q --cov=sci_rag --cov-report=term --cov-fail-under=78
uvx pre-commit run --all-files --show-diff-on-failure
```

Agents normally need one supported local Python version; GitHub Actions supplies the version
matrix. Do not lower the coverage floor to land a change.

### Conditional checks

Run the checks that match the affected surface:

```bash
# Container or packaging changes
docker build -t sci-rag-kit:agent-check .
docker run --rm sci-rag-kit:agent-check sci-rag --help

# Terraform changes
terraform -chdir=infra/terraform fmt -check -diff
terraform -chdir=infra/terraform init -backend=false -input=false
terraform -chdir=infra/terraform validate

```

CI checks internal Markdown links in offline mode. Keep repository links relative and verify renamed
or added documentation targets. For documentation-only changes, run the relevant pre-commit and
link checks at minimum; run `make check` when feasible and disclose any intentionally omitted check.

Changes to `docs/stylesheets/` also need the rendered-geometry guard, because a stylesheet defect
is a number on a painted page rather than anything the Markdown-level tests can see:

```bash
# Presentation checks against the built site. Needs `make docs` first.
make docs-geometry
```

That target installs a browser, so it is separate from `make docs` and from `make check`. The tests
skip with instructions when the browser or `site/` is missing, which means a skipped run is not
evidence that presentation passed. CI runs them in the `docs` job.

Credentialed commands such as `make demo-cloud`, `make benchmark`, and judged answer evaluation are
not routine validation. Run them only when the task requires them and the environment is explicitly
configured for external model calls.

## Architecture invariants

### Keep domain semantics out of Python

Domain semantics belong in `domain/domain.yaml`, `domain/prompts/`, and the evaluation JSONL files.
A user configuring the kit for their field should not need to edit package code. General reusable capability
belongs in `src/sci_rag/`; corpus-specific ontology, prompt wording, and ground truth do not.

Prompt templates use `string.Template` with `$UPPER_CASE` slots. Preserve required slots, validate
rendering, and keep JSON examples free from unnecessary escaping. Changes to judge prompts must
preserve the separation between grounding and correctness.

### Draft the domain files, do not hand-write them

A user configuring this kit for a field has four files to produce: `domain/domain.yaml`,
`data/corpus.jsonl`, `domain/eval_seed_questions.jsonl`, and light edits to
`domain/prompts/*.md`. If you are working inside a generated project and about to
author any of them from scratch, stop and run the drafter instead. It is grounded in
the documents already on disk, and its output is validated through the same pydantic
models the loaders use, which hand-written YAML is not.

```bash
uv run sci-rag draft manifest --folder data/raw
uv run sci-rag draft ontology --from-corpus     # or --refine
uv run sci-rag draft questions --count 10
uv run sci-rag draft prompts entity_extraction
```

Every drafter also accepts `--print-prompt`, which renders the corpus-grounded prompt
on stdout, and `--from-file`, which reads a reply back through identical validation.
Use that pair when no model credentials are configured: render the prompt, answer it
yourself, and feed the answer back rather than writing the file directly. It is the
same validation either way.

Three rules the drafters enforce, which apply to you as well:

- `license_class` is never inferred. Every drafted manifest row is `unknown`, and a
  rights decision is the user's, not yours and not a model's.
- Drafted seed questions carry a `drafted` tag. Evaluation reports repeat it until a
  domain expert removes it. Do not remove the tag on a user's behalf, and do not
  quote a metric from a report that still carries the warning.
- `domain/eval_calibration_labels.jsonl` is hand-labeled by design. Generating it
  would make the judge calibrate against itself.

`sci-rag doctor` reports domain coherence: ontology size and naming, seed-question
grounding against the ingested corpus, how many questions are still unreviewed, and
manifest paths and rights. Run it after changing anything in `domain/`.

`sci-rag build` is the documented path from documents to a queryable corpus: ingest,
then graph extraction and communities when a credential exists. Keep it thin; it
composes the same helpers `ingest`, `graph extract`, and `graph communities` use, and
a new pipeline step belongs in those commands first.

### Keep the command surface scannable

Every top-level command and command group names one of the `rich_help_panel` values
defined in `src/sci_rag/cli/main.py` (start here, build your knowledge base, ask
questions, measure quality, serve, maintain). A one-line help string is read before
any documentation: one action, present tense, and no term the glossary would have to
define. Longer help belongs in the docstring body, where there is room to explain.

### Depend on facades and shared seams

Application code should use public facades such as `Retriever.retrieve()`, `AnswerEngine`, and
`RagService` rather than reaching into retrieval-stage SQL or transport internals. REST routes and
MCP tools must call the same `RagService` behavior so authentication, scope, citations, errors, and
results cannot drift between surfaces. If a service contract changes, test both front doors where
applicable.

New providers should implement the existing `EmbeddingProvider` or `LLMClient` interfaces. New
parsers should produce the shared block model. New retrieval mechanisms belong behind the
retrieval facade and require trace output plus an ablation path.

### Preserve async concurrency boundaries

Database and retrieval paths are asyncio end to end. Concurrent retrieval stages require separate
SQLAlchemy async sessions because asyncpg cannot multiplex one session. Shared query embeddings may
be awaited by multiple stages, but cancellation or timeout in one stage must not invalidate another
stage's work. Avoid blocking I/O inside async request paths.

### Enforce scope before ranking

Retrieval access scope is a correctness and rights boundary, not a presentation filter.

- Apply license, source, metadata, and exclusion conditions inside every stage's SQL before ordering
  and limiting.
- An explicitly empty license allowlist must return no results before embeddings or database work.
- Treat `unknown` redistribution rights as unsafe. Never broaden default external access.
- Community summaries combine evidence before request scope is known, so scoped requests must not
  use them.
- Graph traversal, HyDE, reranking, resolution, and new retrieval stages must not reintroduce
  excluded documents indirectly.

Every scope change needs adversarial tests that prove excluded material cannot affect candidates or
answers, including bounded candidate pools and alternate retrieval layers.

### Degrade visibly and fail closed

A slow or failed retrieval layer may contribute no candidates while the request continues, but its
trace must say `timeout`, `error`, `empty`, `skipped`, or `disabled` as appropriate. Do not catch an
exception and silently pretend the stage succeeded. Traces must remain content-free: include stage,
status, duration, and counts, but never query text, chunk text, API keys, or private document data.

For authentication, authorization, licensing, and destructive corpus operations, fail closed. Open
server mode is intended for local development only and must continue to warn loudly.

### Keep model output untrusted

Validate LLM JSON shapes, ontology types, relationship endpoints, judge scores, citations, and other
structured output before persistence or use. Malformed model output should be rejected or recorded
as a visible failure according to the existing contract, never guessed into validity. Request-level
LLM keys must remain ephemeral and must never be stored or logged.

### Preserve scientific honesty and provenance

- Generated claims must be grounded in retrieved sources and carry stable numbered citations.
- When the allowed corpus has no answer, say so instead of filling gaps from model priors.
- HyDE output is a retrieval probe only. It is never evidence and must never be shown or cited as a
  source.
- Retrieval reports need their corpus fingerprint and git commit. Committed benchmark claims need a
  reproducible snapshot or equivalent corpus identity.
- Grounding evaluation sees the question, generated answer, and retrieved sources, but not the
  reference answer. Correctness evaluation is a separate reference-based pass.
- Malformed judge responses are failures, not zero-cost coercions. Human calibration data must be
  described honestly, including whether labels are expert or non-expert.
- Keep at least one `unanswerable` honesty probe in real evaluation sets.

For changes to chunking, retrieval, routing, graph extraction, reranking, prompts, fusion weights,
or evaluation behavior, run a before/after retrieval ablation on the same corpus and include both
results in the pull request. Use `uv run sci-rag eval diff` when comparing saved runs. Do not claim
improvement from unmatched corpora, different snapshots, or one favorable aggregate metric.

### Keep schema and embedding changes deliberate

PostgreSQL is the operational store for documents, chunks, vectors, full-text indexes, entities,
relationships, and communities. Do not introduce a second vector or graph database without an ADR
that revisits the accepted design.

Keep SQLAlchemy models and Alembic migrations consistent. A persisted schema change needs a new
migration and migration-path validation, not only `Base.metadata.create_all()` coverage. Back up
real data before migrations or bulk corpus operations, and verify restores with `sci-rag doctor`
and corpus digests as described in [docs/operations.md](docs/operations.md).

The embedding dimension is a schema property and defaults to 1536 to remain within the pgvector
HNSW index limit. Changing the provider, model, version stamp, or dimension requires an explicit
re-embedding plan; a dimension change also requires a migration. Never permit mixed or silently
truncated vector dimensions.

## Implementation conventions

- Target Python 3.11 syntax and keep public/package code fully typed. Mypy disallows untyped
  function definitions in `sci_rag`.
- Ruff is authoritative for imports, lint, modernization, simplification, and formatting. The line
  length is 100; let the formatter decide layout.
- Follow existing dependency-injection patterns. Accept optional settings, providers, clients, or
  session factories where tests need deterministic substitutes.
- Write docstrings that explain why a module or non-obvious seam has its shape. Write for a
  scientist encountering the code for the first time.
- Use plain language, avoid unexplained jargon, and do not use em dashes in repository prose.
- Keep logs structured with `structlog`. Log operational metadata, not secrets or content.
- Preserve stable REST error codes and RFC 9457 `application/problem+json` responses. Preserve
  `X-Request-ID` behavior across success and error paths.
- Non-streaming answers and streaming SSE responses must aggregate the same typed event stream.
- Avoid speculative framework layers. The project deliberately has no task queue, cache service,
  graph sidecar, or plugin framework.

New dependencies require a clear benefit, compatibility with Python 3.11 and 3.12, and an updated
`uv.lock`. Keep heavyweight functionality optional when possible, following the Docling and local
reranker extras.

## Test design

Tests should be deterministic, offline by default, and behavior-focused.

- Put pure transforms, validation, routing, fusion, scope construction, and report logic in unit
  tests.
- Use integration tests for SQL semantics, migrations, pgvector behavior, transactions, graph
  cleanup, ingestion, and end-to-end retrieval.
- Use server tests for auth scopes, rate limits, error envelopes, request IDs, SSE, REST schemas, and
  MCP parity.
- Use `LocalHashEmbedder` and mock `LLMClient` implementations unless a test is explicitly marked
  and documented as credentialed.
- Reproduce rights and scope bugs with both an eligible and an ineligible document. Assert absence,
  not just the presence of a desired result.
- Assert trace status and degradation metadata when exercising timeouts, skipped layers, malformed
  model output, or fallbacks.
- For a bug fix, add a regression test that fails for the original reason before applying the fix.

Do not weaken assertions, widen timing tolerances without evidence, or replace realistic boundary
tests with mocks solely to get green results. If an integration test skips because PostgreSQL is
unavailable, state that limitation explicitly.

## Documentation, API, and release discipline

Update documentation whenever a user can observe a change to a command, configuration variable,
manifest field, API schema, MCP tool, prompt slot, report format, deployment step, or failure mode.
Keep README, API reference, examples, and implementation terminology consistent.

Documentation changes follow [docs/STYLE.md](docs/STYLE.md): it assigns every page a type, states
the shape each type takes, and lists the words this project does not use. `tests/unit/test_docs_style.py`
enforces the checkable half and runs in `make check`. A new page in `mkdocs.yml` has to be classified
in that guide's page-type table or the build fails.

Architectural changes require an ADR with context, decision, consequences, and reversal conditions.
Do not edit historical ADRs to make a new decision appear old; supersede them explicitly. Do not
hand-edit generated benchmark tables without updating the underlying reproducible report inputs.

The project is pre-1.0 but still follows the compatibility promises in
[docs/VERSIONING.md](docs/VERSIONING.md). Treat public Python imports, CLI commands, REST schemas,
MCP tool names, environment variables, domain-file shapes, and report formats as contracts. Note
breaking changes clearly and provide a migration path.

## Pull requests and completion criteria

Follow [.github/pull_request_template.md](.github/pull_request_template.md). Use a concise,
conventional title consistent with history, such as `feat(retrieve): ...`, `fix(server): ...`,
`test(evals): ...`, or `docs: ...`. Keep commits focused and explain why the change is needed.

A change is complete only when all applicable statements are true:

- The final diff is narrow, readable, and free of secrets, private data, caches, and generated junk.
- The requested behavior is covered at the correct test layer, including failure and rights-boundary
  cases.
- Focused tests pass, applicable full checks pass, and skipped checks are disclosed.
- User-visible behavior and operational changes are documented.
- Retrieval or evaluation changes include comparable before/after evidence.
- Schema changes include migrations and migration-path evidence.
- REST and MCP remain aligned behind `RagService` where the shared contract changed.
- Scientific claims, metrics, citations, and benchmark statements are supported by reproducible
  evidence.
- The handoff or pull request summarizes the change, verification, residual risk, and any follow-up
  work without overstating what was tested.

When blocked by missing credentials, unavailable infrastructure, or a decision that would expand
scope materially, stop at the safe boundary and ask for direction. Never fabricate test evidence,
live-service results, credentials, citations, benchmark improvements, or deployment status.
