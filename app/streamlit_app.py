"""BioCirV Knowledge Base — Streamlit chat interface.

A single-file Streamlit app that lets beta testers have a ChatGPT-style
conversation with the BioCirV knowledge base. No installation or API key
required for testers — secrets are injected server-side.

Deploy to Streamlit Community Cloud by pointing it at app/streamlit_app.py.
Set BIOCIRV_API_URL and BIOCIRV_API_KEY in the Streamlit Cloud secrets dashboard.

For local development, create app/.env (gitignored) with:
    BIOCIRV_API_URL=https://sci-rag-xy45yfiqaq-uc.a.run.app
    BIOCIRV_API_KEY=<your-key>
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration — secrets with .env fallback for local development
# ---------------------------------------------------------------------------

try:
    API_URL: str = st.secrets["BIOCIRV_API_URL"]
    API_KEY: str = st.secrets["BIOCIRV_API_KEY"]
except (KeyError, FileNotFoundError):
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
    except ImportError:
        pass
    API_URL = os.environ.get("BIOCIRV_API_URL", "")
    API_KEY = os.environ.get("BIOCIRV_API_KEY", "")

if not API_URL or not API_KEY:
    st.error(
        "This app is not configured correctly. "
        "Please contact the BioCirV team to get access."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Profile mapping (plain English → API profile name)
# ---------------------------------------------------------------------------

PROFILE_LABELS = ["Quick", "Deep"]
PROFILE_MAP = {"Quick": "interactive", "Deep": "deep"}

# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------


def call_answer_api(
    query: str,
    profile: str,
    top_k: int = 8,
    year_min: int | None = None,
    year_max: int | None = None,
) -> dict:
    """POST /v1/answer and return the parsed JSON response dict.

    Raises httpx.HTTPStatusError for non-2xx responses and
    httpx.TimeoutException when the request exceeds the timeout.
    """
    payload: dict = {
        "query": query,
        "profile": profile,
        "top_k": top_k,
        "stream": False,
        "year_min": year_min,
        "year_max": year_max,
    }
    # Remove None values so the API uses its defaults
    payload = {k: v for k, v in payload.items() if v is not None}

    response = httpx.post(
        f"{API_URL}/v1/answer",
        json=payload,
        headers={"X-API-Key": API_KEY},
        timeout=90.0,  # deep profile can be slow; give it room
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Plain-language error translation
# ---------------------------------------------------------------------------

_ERROR_MESSAGES = {
    401: "This app is not authorized to reach the knowledge base. Please contact the BioCirV team.",
    403: "This app is not authorized to reach the knowledge base. Please contact the BioCirV team.",
    422: "Your question couldn't be processed. Try rephrasing it.",
}

_DEGRADED_WARNING = (
    "Some sources may be missing from this answer. "
    "Results are still valid but may be incomplete."
)

_TIMEOUT_MESSAGE = (
    "This question took too long to answer. "
    "Try 'Quick' mode or a more specific question."
)

_SERVER_ERROR_MESSAGE = (
    "The knowledge base is temporarily unavailable. Please try again in a moment."
)


def translate_error(exc: Exception) -> str:
    """Return a tester-friendly error string for any exception from call_answer_api."""
    if isinstance(exc, httpx.TimeoutException):
        return _TIMEOUT_MESSAGE
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in _ERROR_MESSAGES:
            return _ERROR_MESSAGES[code]
        if 500 <= code < 600:
            return _SERVER_ERROR_MESSAGE
        return _SERVER_ERROR_MESSAGE
    return _SERVER_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# Citation rendering
# ---------------------------------------------------------------------------


def render_citation_line(idx: int, citation: dict) -> str:
    """Format a single citation as a numbered plain-text line."""
    title = citation.get("title") or "Untitled"
    # Prefer a formatted citation string if the API provides one
    formatted = citation.get("citation") or citation.get("formatted_citation") or ""
    license_label = citation.get("license_class") or citation.get("license") or ""

    parts = [f"**[{idx}]** {title}"]
    if formatted:
        parts.append(f"  \n{formatted}")
    if license_label and license_label not in ("unknown", ""):
        parts.append(f"  \n*{license_label}*")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BioCirV Knowledge Base",
    page_icon="🌿",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("Options")

    profile_label: str = st.radio(
        "Answer profile",
        options=PROFILE_LABELS,
        index=1,  # default: Deep
        help="Quick returns faster answers; Deep searches more sources.",
    )

    if st.button("Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    with st.expander("Advanced filters", expanded=False):
        year_min_input: int | None = st.number_input(
            "Earliest publication year",
            min_value=1900,
            max_value=2100,
            value=None,
            step=1,
            placeholder="Any",
        )
        year_max_input: int | None = st.number_input(
            "Latest publication year",
            min_value=1900,
            max_value=2100,
            value=None,
            step=1,
            placeholder="Any",
        )

    with st.expander("About this tool", expanded=False):
        st.markdown(
            """
**BioCirV Knowledge Base** is a searchable collection of scientific literature
on agriculture, circular bioeconomy, and biomass valorization. It covers topics
such as crop residue management, bioenergy feedstocks, composting, anaerobic
digestion, and related policy and life-cycle analysis.

This beta interface lets you ask questions in plain English and receive answers
grounded in the indexed documents, with numbered citations so you can trace
every claim back to its source.

Questions or feedback? Email the BioCirV team.
            """
        )

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("🌿 BioCirV Knowledge Base — Beta")
st.caption("Agriculture · Circular Bioeconomy · Biomass Valorization")

# ---------------------------------------------------------------------------
# Render existing chat history
# ---------------------------------------------------------------------------

for message in st.session_state.messages:
    role: str = message["role"]
    content: str = message["content"]
    citations: list = message.get("citations") or []
    degraded: list = message.get("degraded") or []

    with st.chat_message(role):
        st.markdown(content)

        if role == "assistant":
            if degraded:
                st.warning(_DEGRADED_WARNING)

            if citations:
                with st.expander(f"Sources ({len(citations)})"):
                    for i, citation in enumerate(citations, start=1):
                        st.markdown(render_citation_line(i, citation))
                        if i < len(citations):
                            st.divider()

# ---------------------------------------------------------------------------
# Chat input and response handling
# ---------------------------------------------------------------------------

prompt: str | None = st.chat_input("Ask a question about the corpus…")

if prompt:
    # Append and display the user message immediately
    st.session_state.messages.append(
        {"role": "user", "content": prompt, "citations": None, "degraded": []}
    )
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call the API and render the assistant response
    with st.chat_message("assistant"):
        api_profile = PROFILE_MAP[profile_label]
        year_min = int(year_min_input) if year_min_input is not None else None
        year_max = int(year_max_input) if year_max_input is not None else None

        with st.spinner("Searching the knowledge base…"):
            try:
                data = call_answer_api(
                    query=prompt,
                    profile=api_profile,
                    year_min=year_min,
                    year_max=year_max,
                )
                answer_text: str = data.get("answer") or ""
                citations_raw: list = data.get("citations") or []
                degraded_stages: list = data.get("degraded_stages") or []

                st.markdown(answer_text)

                if degraded_stages:
                    st.warning(_DEGRADED_WARNING)

                if citations_raw:
                    with st.expander(f"Sources ({len(citations_raw)})"):
                        for i, citation in enumerate(citations_raw, start=1):
                            st.markdown(render_citation_line(i, citation))
                            if i < len(citations_raw):
                                st.divider()

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": answer_text,
                        "citations": citations_raw,
                        "degraded": degraded_stages,
                    }
                )

            except Exception as exc:
                error_msg = translate_error(exc)
                st.error(error_msg)
                # Store the error as an assistant message so history is coherent
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": f"*{error_msg}*",
                        "citations": [],
                        "degraded": [],
                    }
                )
