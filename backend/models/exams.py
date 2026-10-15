from datetime import date
import math
from typing import Any, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

AwareDatetimeValidator = TypeAdapter(AwareDatetime)


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_reference: str = Field(min_length=1, max_length=1000)
    excerpt: str = Field(min_length=1, max_length=4000)
    published_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    course_id: str | None = None
    source_id: str | None = None
    resource_id: str | None = None
    revision_id: str | None = None
    locator: str | None = None
    content_hash: str | None = None
    extraction_warnings: list[str] = Field(default_factory=list)


class ExamFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: Any = None
    status: Literal['missing', 'approximate', 'confirmed', 'conflicting'] = 'missing'
    evidence: list[Evidence] = Field(default_factory=list)
    uncertainty: str | None = None

    @model_validator(mode='after')
    def require_sources(self):
        if self.status == 'missing' and self.value is not None:
            raise ValueError('Missing facts cannot contain invented values.')
        if self.status != 'missing' and not self.evidence:
            raise ValueError('Non-missing facts require source evidence.')
        if self.status == 'confirmed' and self.value is None:
            raise ValueError('Confirmed facts require a value.')
        if self.status in {'approximate', 'conflicting'} and not self.uncertainty:
            raise ValueError('Uncertain facts require an explanation.')
        if self.status == 'conflicting' and len(self.evidence) < 2:
            raise ValueError('Contradictions require at least two source references.')
        return self


class QuestionCategory(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_type: ExamFact = Field(default_factory=ExamFact)
    count: ExamFact = Field(default_factory=ExamFact)
    marks: ExamFact = Field(default_factory=ExamFact)
    exam_weight_percent: ExamFact = Field(default_factory=ExamFact)


class ExamInformation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    course_id: str
    course_name: str
    course_source_reference: str | None = None
    assessment_id: str | None = None
    assessment_source_reference: str | None = None
    assessment_title: ExamFact = Field(default_factory=ExamFact)
    final_exam_exists: ExamFact = Field(default_factory=ExamFact)
    approximate_exam_week: ExamFact = Field(default_factory=ExamFact)
    date_range: ExamFact = Field(default_factory=ExamFact)
    confirmed_start: ExamFact = Field(default_factory=ExamFact)
    duration_minutes: ExamFact = Field(default_factory=ExamFact)
    tested_topics: ExamFact = Field(default_factory=ExamFact)
    learning_outcomes: ExamFact = Field(default_factory=ExamFact)
    learning_materials: ExamFact = Field(default_factory=ExamFact)
    total_questions: ExamFact = Field(default_factory=ExamFact)
    total_marks: ExamFact = Field(default_factory=ExamFact)
    assessment_type: ExamFact = Field(default_factory=ExamFact)
    instructions: ExamFact = Field(default_factory=ExamFact)
    module_weight_percent: ExamFact = Field(default_factory=ExamFact)
    question_categories: list[QuestionCategory] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_facts(self):
        boolean = self.final_exam_exists
        if boolean.value is not None and type(boolean.value) is not bool:
            raise ValueError('Final-exam classification must be a boolean when known.')
        for field in [self.duration_minutes, self.total_questions, *[category.count for category in self.question_categories]]:
            if field.value is not None and (type(field.value) is not int or field.value < 0):
                raise ValueError('Known counts/duration must be non-negative integers.')
        for field in [self.module_weight_percent, *[category.exam_weight_percent for category in self.question_categories]]:
            if field.value is not None and (type(field.value) not in (int, float) or not 0 <= field.value <= 100):
                raise ValueError('Known percentage weights must be between 0 and 100.')
        for field in [self.total_marks,*[category.marks for category in self.question_categories]]:
            if field.value is not None and (type(field.value) not in (int, float) or not math.isfinite(field.value) or field.value < 0):
                raise ValueError('Known marks must be non-negative numbers.')
        confirmed_categories = self.question_categories and all(category.count.status == 'confirmed' for category in self.question_categories)
        if self.total_questions.status == 'confirmed' and confirmed_categories:
            if sum(category.count.value for category in self.question_categories) != self.total_questions.value:
                issue = 'Question-category counts do not match the stated total. Cross-check missing categories or contradictory sources.'
                if issue not in self.issues:
                    self.issues.append(issue)
        if self.confirmed_start.value is not None:
            AwareDatetimeValidator.validate_python(self.confirmed_start.value)
        if self.date_range.value is not None:
            interval = self.date_range.value
            if not isinstance(interval, dict) or set(interval) != {'start', 'end'}:
                raise ValueError('Date ranges require start/end dates.')
            if date.fromisoformat(interval['start']) > date.fromisoformat(interval['end']):
                raise ValueError('Exam date range is reversed.')
        return self
