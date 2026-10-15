from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from models.exams import ExamInformation
from models.preferences import Day, PracticeFormat, StudyWindow, SchedulingOverrides


class ResourceReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=200)
    source: Literal['user', 'brightspace'] = 'user'


class StudyGoal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(default_factory=list, max_length=30)
    target_date: date | None = None
    practice_format: PracticeFormat | None = None
    difficulty: Literal['beginner', 'intermediate', 'advanced'] = 'beginner'
    session_minutes: int | None = Field(default=None, ge=5, le=180, strict=True)
    question_count: int | None = Field(default=None, ge=1, le=100, strict=True)
    resources: list[ResourceReference] = Field(default_factory=list, max_length=30)
    exam: ExamInformation | None = None

    @field_validator('subject')
    @classmethod
    def subject_not_blank(cls, value):
        if not value.strip(): raise ValueError('Enter a subject.')
        return value.strip()

    @field_validator('topics')
    @classmethod
    def valid_topics(cls, values):
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError('Topics must be nonempty and at most 200 characters.')
        return [value.strip() for value in values]


class GoalInput(StudyGoal):
    request_id: UUID

    @model_validator(mode='after')
    def user_sources_only(self):
        if self.exam is not None or any(r.source != 'user' for r in self.resources):
            raise ValueError('Exam information must come from an authorized server-side connector.')
        return self


class BusyPeriod(BaseModel):
    model_config = ConfigDict(extra='ignore')
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start: raise ValueError('Busy period must end after starting.')
        return self


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    start: AwareDatetime
    days: int = Field(default=7, ge=1, le=90, strict=True)
    sessions: int = Field(default=3, ge=1, le=100, strict=True)
    timezone: str | None = None
    excluded_days: list[Day] = Field(default_factory=list, max_length=7)
    excluded_periods: list[BusyPeriod] = Field(default_factory=list, max_length=50)
    allow_unchecked_calendar: bool = False
    reviewed_notes: str | None = Field(default=None, max_length=2000)
    restrictions_confirmed: bool = False
    adjustment: str = Field(default='', max_length=1000)
    temporary_preferences: SchedulingOverrides = Field(default_factory=SchedulingOverrides)
    session_overrides: dict[int, SchedulingOverrides] = Field(default_factory=dict)

    @field_validator('session_overrides')
    @classmethod
    def valid_override_indices(cls, value):
        if len(value)>100 or any(not 0<=index<100 for index in value):
            raise ValueError('Invalid selected session constraints.')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try: ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError): raise ValueError('Use an IANA timezone such as Asia/Singapore.') from None
        return value


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['approve', 'reject']


class ProposedSession(BusyPeriod):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(default='', max_length=200)

    @field_validator('title')
    @classmethod
    def valid_title(cls,value):
        if not value.strip(): raise ValueError('An exact event title is required.')
        return value.strip()

    @model_validator(mode='after')
    def whole_minutes(self):
        if any(value.second or value.microsecond for value in (self.start,self.end)):
            raise ValueError('Proposed sessions must use whole-minute times.')
        return self


class ExactProposalInput(ProposalInput):
    proposed_sessions: list[ProposedSession] = Field(min_length=1, max_length=100)


class CalendarActionApproval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    proposal_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class StudyRange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start: AwareDatetime
    end: AwareDatetime
    timezone: str = 'Asia/Singapore'
    sessions: int = Field(default=3,ge=1,le=100,strict=True)
    excluded_days: list[Day] = Field(default_factory=list,max_length=7)
    excluded_periods: list[BusyPeriod] = Field(default_factory=list,max_length=50)
    allow_unchecked_calendar: bool = False
    reviewed_notes: str | None = Field(default=None,max_length=2000)
    restrictions_confirmed: bool = False
    temporary_preferences: SchedulingOverrides = Field(default_factory=SchedulingOverrides)

    @model_validator(mode='after')
    def valid_range(self):
        from datetime import timedelta
        if self.end<=self.start or self.end-self.start>timedelta(days=90):
            raise ValueError('Use an aware range of up to ninety days.')
        try: ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError,ValueError): raise ValueError('Use a valid IANA timezone.') from None
        return self


class SessionValidation(StudyRange):
    proposed_sessions: list[ProposedSession] = Field(min_length=1,max_length=100)
