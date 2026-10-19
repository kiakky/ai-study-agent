"""Readable whitespace view of source segments; originals/revisions stay intact.

Native read offsets and practice selections both use this same view. Only page
padding, common margins and trailing whitespace are removed. Relative indentation,
internal spacing, word order and paragraph boundaries are retained.
"""
from textwrap import dedent


def source_text(segments):
    parts=[]
    for segment in segments:
        lines=dedent(segment['cleaned_text']).splitlines()
        selected=[]
        for line in lines:
            if line.strip():selected.append(line.rstrip())
            elif selected and selected[-1]!='':selected.append('')
        while selected and selected[-1]=='':selected.pop()
        if selected:parts.append('\n'.join(selected))
    return '\n\n'.join(parts)
