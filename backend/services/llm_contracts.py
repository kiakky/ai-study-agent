"""Small provider-neutral reply and tool-validation contracts. No tool execution."""
from dataclasses import dataclass
import json
from typing import Any, Protocol
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LLMError(Exception):
    def __init__(self, code='unavailable'):
        self.code=code
        super().__init__('Conversation service: '+code)


class ToolCall(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str = Field(min_length=1,max_length=200)
    name: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
    arguments: dict[str,Any]


def strict_json(value):
    def unique(pairs):
        result={}
        for key,item in pairs:
            if key in result:raise ValueError('Duplicate key')
            result[key]=item
        return result
    return json.loads(value,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Invalid constant')))


@dataclass(frozen=True)
class ChatReply:
    content: str
    tool_calls: tuple[ToolCall,...] = ()


class ChatProvider(Protocol):
    def complete(self,messages:list[dict],tools:list[dict]|None=None,tool_choice=None,max_tokens:int|None=None)->ChatReply: ...


class ToolRegistry:
    """Allowlisted schemas only. No callback/execute facility in Stage 1."""
    def __init__(self):self._tools={}

    def register(self,name,description,schema):
        if name in self._tools or schema.model_config.get('extra')!='forbid':raise ValueError('Use unique names and strict schemas.')
        ToolCall(id='schema',name=name,arguments={})
        self._tools[name]=(description,schema)

    def definitions(self):
        return [{'type':'function','function':{'name':name,'description':description,'parameters':schema.model_json_schema()}}
                for name,(description,schema) in self._tools.items()]

    def validate(self,call):
        try:
            call=ToolCall.model_validate(call)
            if call.name not in self._tools:raise ValueError('Unavailable tool')
            schema=self._tools[call.name][1]
            parsed=schema.model_validate(call.arguments)
            return ToolCall(id=call.id,name=call.name,arguments=parsed.model_dump(mode='json'))
        except (ValueError,ValidationError):raise LLMError('invalid_tool_call') from None

    def parse_action(self,text):
        """Strict structured-action fallback for providers without native calls."""
        try:return self.validate(ToolCall.model_validate(strict_json(text)))
        except (ValueError,ValidationError):raise LLMError('invalid_tool_call') from None
