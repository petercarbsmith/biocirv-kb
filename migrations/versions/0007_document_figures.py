"""Add figure chunks flag and the document_figures table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS is_figure BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        "CREATE TABLE IF NOT EXISTS document_figures ("
        "id VARCHAR(32) PRIMARY KEY, "
        "document_id VARCHAR(32) NOT NULL REFERENCES documents(id) ON DELETE CASCADE, "
        "chunk_id VARCHAR(32) REFERENCES chunks(id) ON DELETE SET NULL, "
        "figure_index INTEGER NOT NULL, "
        "figure_label VARCHAR(64), "
        "caption TEXT NOT NULL DEFAULT '', "
        "page_number INTEGER, "
        "bbox JSONB, "
        "image_uri TEXT, "
        "image_sha256 VARCHAR(64), "
        "width INTEGER, "
        "height INTEGER, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "CONSTRAINT ux_document_figures_doc_idx UNIQUE (document_id, figure_index))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_figures_document_id "
        "ON document_figures (document_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_figures_chunk_id "
        "ON document_figures (chunk_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_figures")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS is_figure")
