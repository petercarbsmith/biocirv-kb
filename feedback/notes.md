# Sci-RAG Kit Beta Tester Notes: BioCirV Implementation

This document records the onboarding experience, technical hurdles, and lessons learned during the initial implementation of the `sci-rag-kit` for the **BioCirV Knowledge Base**.

## 1. Executive Summary
We successfully implemented a standalone Knowledge Base (`biocirv-kb`) containing ~200 pages of bioeconomy research PDFs. The system utilizes a **Hybrid RAG** architecture (Vertex AI semantic search + CBORG Concept Graph) and provides a grounded, citation-backed interface for research querying.

## 2. Process Timeline
1.  **Environment Setup**: Installed `sci-rag-kit` via `pipx` and scaffolded a new project using `sci-rag new --defaults`.
2.  **Credentialing**: Navigated a multi-provider setup:
    *   **Embeddings**: Configured Vertex AI (`gemini-embedding-001`) via an existing institutional GCP project.
    *   **Generation**: Configured CBORG (`gpt-4o`) via an OpenAI-compatible provider.
3.  **Parsing Upgrade**: Installed the `docling` extra and modified `pyproject.toml` to enable vision-aware scientific PDF parsing.
4.  **Ingestion**: Processed 9 documents into 500 semantic chunks.
5.  **Graph Build**: Extracted 291 entities and 231 relationships; built 26 hierarchical communities.
6.  **Verification**: Conducted a retrieval ablation study (hit@10: 1.0) and successfully executed cross-document reasoning queries.
7.  **Deployment**: Created a dedicated GitHub repository and an Agent Skill for natural language querying.

## 3. Hurdles & Errors (Lessons for Developers)

### A. Model Naming Ambiguity (Vertex AI)
*   **Issue**: `sci-rag-kit` defaults to `gemini-3.6-flash`. This returned a `404 NOT_FOUND` on Vertex AI.
*   **Lesson**: Institutional GCP projects often have limited access to "preview" or unversioned model aliases. 
*   **Fix**: Manually downgraded to `gemini-1.5-flash` in `.env`. The template might benefit from a `sci-rag doctor --probe` that suggests fallback models.

### B. Network Authorization (CBORG IPv6)
*   **Issue**: Initial generation calls to CBORG failed with a `403 Permission Denied`. 
*   **Root Cause**: The user's terminal was connecting via **IPv6**, but the CBORG portal's "one-click authorization" only whitelisted the **IPv4** address.
*   **Fix**: Copied the IPv6 address from the `doctor` traceback and manually authorized it on the CBORG portal.

### C. Dependency Management (Optional Extras)
*   **Issue**: `uv sync --extra docling` failed initially because `docling` was not defined in the scaffolded `pyproject.toml`.
*   **Lesson**: The `sci-rag new` template should include common scientific extras (like `docling` and `openai`) in the base `pyproject.toml` even if they aren't installed by default, so users can "opt-in" without manual TOML edits.

### D. Async Exit Noise
*   **Issue**: Clean CLI runs frequently finished with `RuntimeError: generator didn't stop after athrow()`.
*   **Context**: This appears to be a cleanup timing issue in the `httpx`/`httpcore` stack during rapid teardown of parallel connections. It is non-fatal but confusing for new users.

## 4. Beta Tester Recommendations
1.  **Interactive Graph Preview**: Users want an immediate "vibe check" of the graph. A simple `sci-rag graph plot` command that outputs a static PNG or interactive HTML would be highly valuable.
2.  **Schema Visualizer**: A way to visualize the `domain.yaml` hierarchy before committing to a token-heavy graph extraction.
3.  **Default Agent Skill**: Pre-scaffold a `.agents/skills/` directory so AI agents (Claude, Zoo, etc.) can "wake up" inside the repo and know how to query it immediately.

## 5. Metadata
- **Project**: biocirv-kb
- **Base Directory**: `~/sci-rag-test/biocirv-kb`
- **Remote**: `https://github.com/petercarbsmith/biocirv-kb`
- **Date**: 2026-09-09
