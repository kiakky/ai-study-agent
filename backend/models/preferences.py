from datetime import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Day = Literal['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
PracticeFormat = Literal['mcq', 'short_answer', 'explanations', 'mixed']


class StudyWindow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: time
    end: time

    @model_validator(mode='after')
    def validate_window(self):
        if self.start.tzinfo or self.end.tzinfo or self.start == self.end:
            raise ValueError('Use different local start/end times without timezone offsets.')
        if self.start.second or self.end.second or self.start.microsecond or self.end.microsecond:
            raise ValueError('Study times must use whole minutes.')
        return self


class StudyPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    study_days: list[Day] = Field(default_factory=list, max_length=7)
    study_windows: list[StudyWindow] = Field(default_factory=list, max_length=7)
    session_minutes: int = Field(default=30, ge=5, le=180, strict=True)
    practice_format: PracticeFormat = 'mcq'
    question_count: int = Field(default=10, ge=1, le=100, strict=True)
    notes: str = Field(default='', max_length=2000)

    @field_validator('study_days')
    @classmethod
    def unique_days(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Choose each study day only once.')
        return value


class SchedulingOverrides(BaseModel):
    """Proposal-local defaults, never a saved-profile mutation."""
    model_config = ConfigDict(extra='forbid')
    study_days: list[Day] | None = Field(default=None, max_length=7)
    study_windows: list[StudyWindow] | None = Field(default=None, max_length=7)
    session_minutes: int | None = Field(default=None, ge=5, le=180, strict=True)

    @field_validator('study_days')
    @classmethod
    def unique_days(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError('Choose each study day only once.')
        return value


def effective_preferences(saved, overrides=None):
    patch = SchedulingOverrides.model_validate(overrides or {}).model_dump(mode='json', exclude_none=True)
    return StudyPreferences.model_validate({**saved, **patch}).model_dump(mode='json')
