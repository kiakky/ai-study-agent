"""Native function calls through IBM Orchestrate's documented model gateway.

No remote tool registration, public callback server or agent redeployment. Python
executes the supplied actor-bound tools and returns their results on the next turn.
"""
import json
import httpx
from services.orchestrate import OrchestrateProvider, ERROR_CODES
from services.orchestrate_client import POCError, check_status
from services.llm_contracts import ChatReply, ToolCall, LLMError, strict_json
from services.watsonx import validate_messages


MODEL = 'groq/openai/gpt-oss-120b'


def inline_parameters(schema):
    """Make nested function arguments explicit for the gateway's model template."""
    definitions = schema.get('$defs', {})
    def expand(node):
        if isinstance(node, list):
            return [expand(value) for value in node]
        if not isinstance(node, dict):
            return node
        if '$ref' in node:
            name = node['$ref'].removeprefix('#/$defs/')
            if name not in definitions:
                raise LLMError('unsupported_tools')
            node = {**definitions[name], **{k:v for k,v in node.items() if k != '$ref'}}
        return {key: expand(value) for key,value in node.items() if key != '$defs'}
    return expand(schema)


def outbound_messages(messages):
    """Keep private backend fields out of native tool-result continuations."""
    excluded = {'notes','reviewed_notes','telegram_user_id','user_id','account_id',
                'google_event_id','encrypted_credentials','authorization_start_url'}
    def minimize(value):
        if isinstance(value, dict):
            return {k:minimize(v) for k,v in value.items() if k not in excluded}
        if isinstance(value, list):
            return [minimize(v) for v in value]
        return value
    result = []
    for message in messages:
        copy = dict(message)
        if copy['role'] == 'tool':
            try:
                copy['content'] = json.dumps(minimize(strict_json(copy['content'])))
            except (ValueError, TypeError):
                raise LLMError('invalid_request') from None
        result.append(copy)
    return result


class OrchestrateNativeProvider(OrchestrateProvider):
    native_calendar_tools = True
    structured_actions = False

    def complete(self, messages, tools=None, tool_choice=None, max_tokens=None):
        try:
            return self._complete_once(messages,tools,tool_choice,max_tokens)
        except LLMError as error:
            if (error.code!='invalid_response' or not self.last_error_metadata.get('repairable_format')
                    or self.last_attempt_count!=1):raise
            previous=self.last_attempt_count
            corrected=[dict(m) for m in messages]
            corrected[0]['content']+='\nFORMAT CORRECTION: Return one valid assistant response. Use only advertised function names and valid JSON arguments. Keep conversational prose below 3000 characters; use the appropriate tool for requested saved content instead of writing that content in chat.'
            try:return self._complete_once(corrected,tools,tool_choice,max_tokens)
            finally:self.last_attempt_count+=previous

    def _complete_once(self, messages, tools=None, tool_choice=None, max_tokens=None):
        self.last_attempt_count=0
        self.last_error_metadata={}
        validate_messages(messages, tools)
        if (messages[0]['role'] != 'system'
                or any(m['role'] == 'system' for m in messages[1:])
                or len(json.dumps(messages)) > 70000):
            raise LLMError('invalid_request')
        names = []
        if tools is not None:
            if not isinstance(tools, list) or len(tools) > 16:  # App-owned schema budget; eleven-tool support verified through IBM.
                raise LLMError('unsupported_tools')
            try:
                names = [t['function']['name'] for t in tools]
            except (KeyError, TypeError):
                raise LLMError('unsupported_tools') from None
            if len(set(names)) != len(names):
                raise LLMError('unsupported_tools')
        elif tool_choice is not None:
            raise LLMError('unsupported_tools')
        limit = 2048 if max_tokens is None else max_tokens
        if type(limit) is not int or not 1 <= limit <= 4096:
            raise LLMError('invalid_request')
        body = {'model': MODEL, 'messages': outbound_messages(messages), 'stream': False,
                'max_tokens': limit, 'temperature': 0.2, 'reasoning_effort': 'medium'}
        if tools:
            # This gateway accepts string choices, not OpenAI's named-choice object.
            # A singleton allowlist + required preserves the caller's exact restriction.
            if isinstance(tool_choice, dict):
                if (set(tool_choice) != {'type','function'} or tool_choice.get('type') != 'function'
                        or not isinstance(tool_choice.get('function'),dict)
                        or set(tool_choice['function']) != {'name'}
                        or tool_choice['function']['name'] not in names):
                    raise LLMError('unsupported_tools')
                selected=tool_choice['function']['name']
                tools=[t for t in tools if t['function']['name']==selected]
                names=[selected]
                tool_choice='required'
            native_tools = [{'type': 'function', 'function': {**t['function'],
                'parameters': inline_parameters(t['function']['parameters'])}} for t in tools]
            body.update(tools=native_tools, tool_choice=tool_choice or 'auto')
        try:
            token = self.transport.authenticate()
        except POCError as error:
            raise LLMError(ERROR_CODES.get(error.code, 'unavailable')) from None
        try:
            self.last_attempt_count+=1
            response = self.client.post(
                self.transport.config.url + '/v1/orchestrate/gateway/model/chat/completions',
                headers={'Authorization': 'Bearer ' + token}, json=body,
                timeout=httpx.Timeout(45, connect=5))
            # A returned tool-format rejection has no executable tool result.
            # Correct it once; never replay timeouts, auth or unknown failures.
            rejected_format=False
            if response.status_code==400 and tools:
                try:rejected_format=response.json().get('error',{}).get('code')=='tool_use_failed'
                except (ValueError,TypeError,AttributeError):pass
            if rejected_format:
                corrected=[dict(m) for m in body['messages']]
                corrected[0]['content']+='\nFORMAT CORRECTION: The gateway rejected the preceding tool output. Use an advertised function with valid JSON arguments matching its schema exactly. Escape quotes in JSON strings, use correct types, and omit unsupported fields. Do not invent missing facts.'
                self.last_attempt_count+=1
                response=self.client.post(
                    self.transport.config.url+'/v1/orchestrate/gateway/model/chat/completions',
                    headers={'Authorization':'Bearer '+token},json={**body,'messages':corrected},
                    timeout=httpx.Timeout(45,connect=5))
            if response.status_code == 401:
                self.transport.token = None
                self.transport._expiry = 0
            if response.status_code != 200:
                self.last_error_metadata = {'http_status': response.status_code}
                try:
                    error = response.json().get('error', {})
                    known = {'tool_use_failed', 'invalid_request_error', 'invalid_request', 'rate_limit_exceeded'}
                    self.last_error_metadata['provider_code'] = error.get('code') if error.get('code') in known else 'other'
                    self.last_error_metadata['failed_generation_present'] = bool(error.get('failed_generation'))
                    text = error.get('message', '')
                    if isinstance(text, str):
                        keywords = ('parse', 'json', 'schema', 'required', 'additional', 'format', 'arguments', 'validation', 'max_tokens')
                        self.last_error_metadata['message_categories'] = [word for word in keywords if word in text.lower()]
                except (ValueError, TypeError, AttributeError):
                    pass
            check_status(response, 'inference')
        except POCError as error:
            raise LLMError(ERROR_CODES.get(error.code, 'unavailable')) from None
        except (httpx.TransportError, TimeoutError):
            # The model may already have processed this request. Never replay it.
            raise LLMError('inference_uncertain') from None
        try:
            if len(response.content) > 200000:
                raise ValueError()
            value = strict_json(response.text)
            if value.get('model', MODEL) not in (MODEL, 'openai/gpt-oss-120b'):
                raise ValueError()
            choices = value['choices']
            if len(choices) != 1 or choices[0].get('finish_reason') == 'length':
                raise ValueError()
            message = choices[0]['message']
            if message.get('role') != 'assistant':
                raise ValueError()
            content = message.get('content') or ''
            raw = message.get('tool_calls') or []
            if not isinstance(content, str) or len(content) > 3800 or not isinstance(raw, list) or len(raw) > 4:
                raise ValueError()
            calls = []
            for call in raw:
                if call['type'] != 'function' or call['function']['name'] not in names:
                    raise ValueError()
                arguments = call['function']['arguments']
                if not isinstance(arguments, str) or len(arguments) > 16000:
                    raise ValueError()
                calls.append(ToolCall(id=call['id'], name=call['function']['name'],
                                      arguments=strict_json(arguments)))
            if len({c.id for c in calls}) != len(calls) or (not calls and not content.strip()):
                raise ValueError()
            # Credentials never belong in the model context; redact any echo anyway.
            for secret in (self.transport.config.api_key, token,
                           self.transport.config.agent_id, self.transport.config.url):
                content = content.replace(secret, '[redacted]')
            return ChatReply(content.strip(), tuple(calls))
        except (KeyError, IndexError, TypeError, ValueError):
            parsed=locals().get('value',{})
            parsed_message=locals().get('message',{})
            parsed_choices=locals().get('choices',[])
            parsed_content=locals().get('content')
            parsed_calls=locals().get('raw')
            repairable=(bool(tools) and isinstance(parsed,dict) and parsed.get('model',MODEL) in (MODEL,'openai/gpt-oss-120b')
                and isinstance(parsed_message,dict) and parsed_message.get('role')=='assistant'
                and isinstance(parsed_choices,list) and len(parsed_choices)==1
                and isinstance(parsed_choices[0],dict)
                and parsed_choices[0].get('finish_reason')!='length'
                and isinstance(parsed_content,str) and isinstance(parsed_calls,list)
                and all(isinstance(call,dict) and isinstance(call.get('function'),dict)
                    and call['function'].get('name') in names for call in parsed_calls))
            self.last_error_metadata={'http_status':200,'repairable_format':repairable,
                'content_characters':len(parsed_content) if isinstance(parsed_content,str) else None,
                'tool_count':len(parsed_calls) if isinstance(parsed_calls,list) else None}
            raise LLMError('invalid_response') from None
