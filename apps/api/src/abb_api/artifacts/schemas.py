"""Artifact API shapes (the API contract, not the table rows)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal["diff", "stdout", "stderr", "file", "text", "other"]


class ArtifactOut(BaseModel):
    id: str = Field(description="`art_<ULID>`; reference it from events as `artifact://<id>`.")
    run_id: str
    project_id: str
    kind: ArtifactKind
    name: str | None = None
    media_type: str
    size_bytes: int
    sha256: str = Field(description="Hex SHA-256 of the stored bytes.")
    created_at: datetime


class ArtifactChunk(BaseModel):
    id: str
    offset: int = Field(description="Byte offset of the first byte of `content`.")
    next_offset: int | None = Field(
        description="Pass as `offset` for the next chunk; null at the end."
    )
    total_bytes: int
    content: str = Field(
        description="UTF-8 text (invalid bytes replaced). Always data, never a document."
    )
