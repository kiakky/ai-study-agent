"""Source-grounded practice contracts. No identity or Calendar execution fields."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvidenceSelection(Strict):
    reference: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0, le=400000, strict=True)
    length: int = Field(default=6000, ge=100, le=6000, strict=True)


class PracticeRequest(Strict):
    title: str = Field(min_length=1, max_length=160)
    topic: str = Field(min_length=1, max_length=500)
    sources: list[EvidenceSelection] = Field(min_length=1, max_length=4,
        description='Already-read learning passages that EXPLAIN the concepts. Also include assessment scope when relevant. Never select only a teaching plan or topic list.')
    assessment_sources: list[EvidenceSelection] = Field(default_factory=list,max_length=2,
        description='For practice matching an actual school test: read its teaching plan/test description and select that citation here to establish tested topics. Empty for generic practice from notes or a named concept.')
    question_count: int = Field(default=5, ge=1, le=10, strict=True)
    requirements: str = Field(default='', max_length=3500,
        description='Quiz scope and exclusions. Telegram supplies the actual latest student request automatically.')


class MaterialInput(Strict):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=30, max_length=30000)


class MaterialTool(Strict):
    operation: Literal['list', 'save_message', 'read']
    title: str = Field(default='My study notes', min_length=1, max_length=160)
    reference: str | None = Field(default=None, max_length=200)
    offset: int = Field(default=0, ge=0, le=100000, strict=True)


class Citation(Strict):
    reference: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=15, max_length=600)


class GeneratedQuestion(Strict):
    text: str = Field(min_length=10, max_length=700)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option: Literal['A', 'B', 'C', 'D']
    explanations: dict[Literal['A', 'B', 'C', 'D'], str]
    citations: list[Citation] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def valid_choices(self):
        if len({o.strip().casefold() for o in self.options}) != 4 or any(not 1 <= len(o.strip()) <= 300 for o in self.options):
            raise ValueError('Use four distinct nonempty options.')
        if set(self.explanations) != set('ABCD') or any(not 15 <= len(v.strip()) <= 700 for v in self.explanations.values()):
            raise ValueError('Explain every answer option.')
        return self


class GeneratedBatch(Strict):
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=5)


class SaveAttempt(Strict):
    revision: int = Field(ge=0, strict=True)
    answers: dict[str, Literal['A', 'B', 'C', 'D']]


class SubmitAttempt(SaveAttempt):
    submission_id: UUID


class StartAttempt(Strict):
    request_id: UUID


class CreatePractice(PracticeRequest):
    request_id: UUID
