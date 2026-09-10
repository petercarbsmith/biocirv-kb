# BioCirV Scientific Knowledge Base

This project implements a Retrieval-Augmented Generation (RAG) system for scientific knowledge related to **Agriculture**, **Circular Bioeconomy**, and **Biomass Valorization** in California's Central Valley.

It is built using the [Sci RAG Kit](https://github.com/sustainability-software-lab/sci-rag-kit) and is deployed on Google Cloud Platform (GCP).

## 🌍 Cloud Infrastructure

The knowledge base is hosted on GCP for production-grade reliability and accessibility.

*   **Project ID:** `biocirv-470318`
*   **Location:** `us-central1`
*   **Service URL:** [https://sci-rag-xy45yfiqaq-uc.a.run.app](https://sci-rag-xy45yfiqaq-uc.a.run.app)
*   **MCP Endpoint:** `https://sci-rag-xy45yfiqaq-uc.a.run.app/mcp`
*   **Corpus Storage:** `gs://biocirv-470318-sci-rag-corpus` (Google Cloud Storage)
*   **Database:** Cloud SQL (PostgreSQL 16 + pgvector)
    *   **Connection Name:** `biocirv-470318:us-central1:sci-rag-db`

## 🧠 Knowledge Graph Schema

The concept graph is built by extracting structured entities and relationships from the ingested PDF reports and papers.

### Entity Types
*   **Agriculture**: Farming practices, crop production, and livestock.
*   **Economic Contribution**: Financial impacts on local economies (output, employment).
*   **Biomass**: Organic fuel or industrial materials (woody, grass).
*   **Protocol**: Scientific experiments or procedures (e.g., Proximate analysis).
*   **Process**: End-to-end actions (e.g., Fermentation, Hydrolysis).
*   **Equipment**: Machinery used in protocols (e.g., XRF analyzer).

### Relationship Types
*   `CONTRIBUTES_TO`: An entity contributes to an economic outcome or process.
*   `SUPPORTED_BY`: A process or outcome is supported by a specific factor.
*   `USES`: A process or protocol uses specific equipment or materials.
*   `CONDUCTED_BY`: A protocol or study is conducted by an organization or individual.

## 🛠 Management & Operations

Infrastructure and corpus updates are managed via the local `Makefile` (ignored by git).

### Adding New Documents
1.  Place new PDF/Markdown files in `data/raw/`.
2.  **Draft Manifest:** Run `make draft` to scan for new files and update `data/corpus.jsonl`.
3.  **Review Rights:** Open `data/corpus.jsonl` and ensure `license_class` is set to `public`.
4.  **Sync & Process:** Run `make all`. This command:
    *   Syncs local data to the GCS bucket.
    *   Triggers the Cloud Run Ingestion job.
    *   Rebuilds the Knowledge Graph and Communities.

### Local Development
The project uses `uv` for dependency management.
*   **Check setup:** `uv run sci-rag doctor`
*   **Local query:** `uv run sci-rag answer "your question"`

## 🔑 Access Control
*   **API Key:** Access to the hosted service requires a header: `X-API-Key: team-key`.
*   **Scopes:** The default key includes `retrieval:query`, `retrieval:answer`, and `corpus:read`.
