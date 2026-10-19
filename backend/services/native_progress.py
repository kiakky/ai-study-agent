"""Time and factual-progress checks for the native loop; no intent routing."""
import hashlib
import json
import time
from services.llm_contracts import LLMError


class TurnDeadline:
    def __init__(self, provider, fence, seconds=90):
        self.provider, self.fence = provider, fence
        self.until = time.monotonic()+seconds

    def check(self):
        if not self.fence():raise LLMError('cleared')
        if time.monotonic() >= self.until:raise LLMError('timeout')

    def complete(self, *args, **kwargs):
        self.check()
        reply = self.provider.complete(*args, **kwargs)
        self.check()
        return reply


class ToolProgress:
    def __init__(self):
        self.version = 0
        self.facts = {}
        self.ranges = {}
        self.stalled = set()

    def record(self, call, result):
        """A new page item or unread text range is progress; changed limits are not."""
        name, args = call.name, call.arguments
        target = (name, args.get('course_id'), args.get('source_id', args.get('reference')))
        changed = False
        if not result.get('error_code') and not result.get('reused_read'):
            if 'text' in result and (result.get('citation') or result.get('reference')):
                reference = result.get('citation', result.get('reference'))
                start = result.get('offset', args.get('offset', 0))
                end = start+len(result['text'])
                ranges = self.ranges.setdefault(reference, [])
                covered = start
                for left, right in sorted(ranges):
                    if left > covered:break
                    covered = max(covered, right)
                changed = end > covered
                ranges.append((start, end))
            elif 'materials' in result or 'courses' in result:
                rows = result.get('materials', result.get('courses', []))
                if not rows:
                    key = target+('empty',)
                    changed = key not in self.facts
                    self.facts[key] = True
                for row in rows:
                    key = target+(row.get('source_id', row.get('course_id', row.get('reference'))),)
                    value = json.dumps(row, sort_keys=True)
                    changed = changed or self.facts.get(key) != value
                    self.facts[key] = value
            else:
                key = (name, json.dumps(args, sort_keys=True))
                value = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
                changed = self.facts.get(key) != value
                self.facts[key] = value
        if changed:
            self.version += 1
            self.stalled.clear()
        elif target in self.stalled:
            raise LLMError('tool_loop')
        else:
            self.stalled.add(target)
        return changed
