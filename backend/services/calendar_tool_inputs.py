"""Lossless native-argument decoding and deterministic local calendar ranges."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from uuid import UUID
from services.llm_contracts import strict_json
from services.llm_contracts import LLMError


class ToolArgumentError(LLMError):
    def __init__(self,fields):
        super().__init__('invalid_tool_call')
        self.fields=fields


class ConversationIntent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    intent: Literal['chat','calendar','planning','preferences','amendment']
    calendar_task: Literal['session_change','sessions','availability','connection','proposals','unspecified'] = 'unspecified'
    reply: str = Field(default='',max_length=3800)
    relative_range: Literal['unspecified','next_week','this_week','today','tomorrow','next_two_weeks'] = 'unspecified'
    clarification: str = Field(default='',max_length=500)
    session_count: int | None = Field(default=None,ge=1,le=100,strict=True)
    sessions_per_week: int | None = Field(default=None,ge=1,le=7,strict=True)


class DraftFromSlots(BaseModel):
    """Small provider-facing projection of the existing exact proposal input."""
    model_config=ConfigDict(extra='forbid')
    subject: str = Field(min_length=1,max_length=200)
    sessions: int = Field(ge=1,le=100,strict=True)
    selections: list[str] = Field(min_length=1,max_length=100)
    title: str = Field(default='',max_length=200)
    topics: list[str] = Field(default_factory=list,max_length=100)
    weekly_counts: list[int] | None = Field(default=None,max_length=14)
    expected_weekdays: list[Literal['mon','tue','wed','thu','fri','sat','sun']] | None = Field(default=None,max_length=100)
    replaces_id: UUID | None = None


def relative_range(label, now, zone):
    """Weeks mean Monday 00:00 through the following Monday, in user timezone."""
    local=now.astimezone(ZoneInfo(zone));day=local.date()
    if label=='unspecified':return None
    if label in {'next_week','this_week','next_two_weeks'}:
        day-=timedelta(days=day.weekday())
        if label in {'next_week','next_two_weeks'}:day+=timedelta(days=7)
        days=14 if label=='next_two_weeks' else 7
    elif label in {'today','tomorrow'}:
        if label=='tomorrow':day+=timedelta(days=1)
        days=1
    else:raise ValueError('Unknown relative range.')
    return (datetime.combine(day,time(),ZoneInfo(zone)),datetime.combine(day+timedelta(days=days),time(),ZoneInfo(zone)))


def normalize_arguments(arguments, schema):
    """IBM sometimes stringifies JSON scalars/containers. Never guess values.

    Decode only a JSON representation matching the advertised type. Required nulls,
    extra fields, arbitrary numeric prose and lossy numbers still fail validation.
    """
    definitions=schema.get('$defs',{})
    def decode(value,node):
        if '$ref' in node:node=definitions[node['$ref'].split('/')[-1]]
        options=node.get('anyOf',[node])
        types={option.get('type') for option in options}
        if isinstance(value,str) and 'string' not in types:
            try:parsed=strict_json(value)
            except (ValueError,TypeError):parsed=value
            if parsed is None and 'null' not in types and node.get('default')==[]:
                parsed=[]
            expected=('array' if isinstance(parsed,list) else 'object' if isinstance(parsed,dict) else
                      'boolean' if type(parsed) is bool else 'integer' if type(parsed) is int else
                      'null' if parsed is None else None)
            if expected in types:value=parsed
        elif value=='null' and 'null' in types:value=None
        concrete=next((option for option in options if option.get('type')=='object' and isinstance(value,dict) or
                      option.get('type')=='array' and isinstance(value,list)),node)
        if isinstance(value,dict):
            properties=concrete.get('properties',{})
            result={}
            for key,item in value.items():
                child=properties.get(key)
                if child is None:result[key]=item;continue
                # Pydantic default_factory arrays omit 'default' from JSON Schema.
                # Null optional non-nullable arrays mean omitted, not a new constraint.
                if (item is None or isinstance(item,str) and item.strip() in {'','null'}) and key not in concrete.get('required',[]) and child.get('type')=='array':
                    item=[]
                result[key]=decode(item,child)
            return result
        if isinstance(value,list):return [decode(item,concrete.get('items',{})) for item in value]
        return value
    return decode(arguments,schema)
