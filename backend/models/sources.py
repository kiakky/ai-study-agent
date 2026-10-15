"""Provider-independent source metadata and conservative extraction results."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime


class SourceMetadata(BaseModel):
    model_config=ConfigDict(extra='forbid')
    course_id: str | None = None
    resource_id: str
    parent_id: str | None = None
    title: str
    kind: str
    media_type: str | None = None
    source_url: str | None = None
    created_at: Any = None
    modified_at: Any = None
    raw_metadata: dict[str,Any] = Field(default_factory=dict)


class SourceSegment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    locator: str
    kind: Literal['text','table','image','notes'] = 'text'
    original_text: str = ''
    cleaned_text: str = ''
    cells: list[list[str | None]] | None = None
    layout: dict[str,Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    model_config=ConfigDict(extra='forbid')
    media_type: str
    status: Literal['extracted','partial','unsupported','requires_multimodal','failed']
    original_text: str = ''
    cleaned_text: str = ''
    segments: list[SourceSegment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    original_sha256: str


class CourseQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    course_id: str = Field(min_length=1,max_length=200)


class SourceQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id: str
    refresh: bool = False


class SearchQuery(CourseQuery):
    query: str = Field(min_length=1,max_length=1000)


class EvidenceQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id: str
    revision_id: str | None = None
    locator: str | None = None
