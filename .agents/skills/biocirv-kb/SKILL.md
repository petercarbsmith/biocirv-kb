# biocirv-kb Skill

Use this skill to query the BioCirV Knowledge Base, which contains ~200 pages of high-fidelity parsed PDFs regarding the California bioeconomy, biomass conversion protocols (proximate analysis, XRF testing, fermentation), and regional economic impact reports (Merced, San Joaquin, and Stanislaus counties).

## Knowledge Base Scope
- **Feedstocks**: Agricultural residues, woody biomass, milk production.
- **Protocols**: Ash determination (ASTM E1755-01), Moisture analysis, Volatile matter (ASTM E872-24), X-ray Fluorescence (XRF), HPLC sugar analysis, Lignin quantification.
- **Regions**: North San Joaquin Valley (NSJV), Merced County, San Joaquin County, Stanislaus County.
- **Economics**: Direct economic output, multiplier effects, employment impacts of agriculture.

## Usage

To ask a scientific question and get a grounded answer with citations:

```bash
cd /Users/pjsmitty301/sci-rag-test/biocirv-kb && uv run sci-rag answer "YOUR QUESTION HERE"
```

To retrieve raw evidence passages without generating a natural language answer:

```bash
cd /Users/pjsmitty301/sci-rag-test/biocirv-kb && uv run sci-rag retrieve "YOUR QUESTION HERE" --profile interactive
```

## Configuration
- **Embeddings**: Semantic search via Vertex AI (`gemini-embedding-001`).
- **Generation**: Reasoning and citations via CBORG (`gpt-4o`).
- **Parsing**: High-fidelity vision-aware parsing via `docling`.
