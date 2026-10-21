"""Incremental display boundary. Raw tool syntax never becomes confirmed text.

Markdown code fences/inline code are literal examples, not execution requests.
Only the executor may enable XML compatibility and dispatch validated calls.
"""
import json
import re
import uuid
from .base import ProviderResponseError


class ToolTextBoundary:
    OPENERS = ('<tool_call', '</tool_call', '<function=', '<function>', '</function',
               '<parameter=', '<parameter>', '</parameter')

    def __init__(self):
        self.pending = ''
        self.block = None
        self.blocks = []
        self.code_ticks = 0
        self.text = ''
        self.malformed = False

    def feed(self, fragment, final=False):
        self.pending += fragment
        output = []
        while self.pending:
            if self.block is not None:
                self.block += self.pending
                self.pending = ''
                end = re.search(r'</tool_call\s*>', self.block, re.I)
                if end:
                    self.blocks.append(self.block[:end.end()])
                    self.pending = self.block[end.end():]
                    self.block = None
                    continue
                if len(self.block) > 65536:
                    raise ProviderResponseError('工具协议超过长度限制')
                break
            if self.pending[0] == '`':
                ticks = len(self.pending) - len(self.pending.lstrip('`'))
                if ticks == len(self.pending) and not final:
                    break  # a delimiter itself can be split across chunks
                if not self.code_ticks:
                    self.code_ticks = ticks
                elif ticks == self.code_ticks:
                    self.code_ticks = 0
                output.append(self.pending[:ticks])
                self.pending = self.pending[ticks:]
                continue
            if not self.code_ticks and self.pending[0] == '<':
                low = self.pending.lower()
                if any(low.startswith(tag) for tag in self.OPENERS):
                    # Wait until a tag boundary so <tool_callback> stays prose.
                    match = re.match(r'<(?:/?tool_call|/?function|/?parameter)(?=[\s=>/])', low)
                    if match:
                        self.block = ''
                        continue
                if any(tag.startswith(low) for tag in self.OPENERS) and len(low) > 1:
                    if final:
                        self.malformed = True
                        self.pending = ''
                    break
                if low == '<' and not final:
                    break
            output.append(self.pending[0])
            self.pending = self.pending[1:]
        visible = ''.join(output)
        self.text += visible
        if final and self.block is not None:
            self.malformed = True
            self.block = None
        return visible

    def calls(self, allowed):
        if self.malformed:
            raise ValueError('工具协议未闭合')
        result = []
        for block in self.blocks:
            match = re.fullmatch(r'<tool_call>\s*<function=([\w.-]+)>\s*(.*?)\s*</function>\s*</tool_call\s*>', block, re.S | re.I)
            if not match or match[1] not in allowed:
                raise ValueError('工具协议结构或工具名称无效')
            params, rest = {}, match[2]
            while rest.strip():
                param = re.match(r'\s*<parameter=([\w.-]+)>(.*?)</parameter>\s*', rest, re.S | re.I)
                if not param or param[1] in params:
                    raise ValueError('工具参数结构无效或重复')
                params[param[1]] = param[2].strip()
                rest = rest[param.end():]
            result.append({'id': 'xml_' + uuid.uuid4().hex, 'type': 'function',
                           'function': {'name': match[1], 'arguments': json.dumps(params, ensure_ascii=False)}})
        return result
