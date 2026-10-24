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
            if self.pending[0] == '~' and self.code_ticks <= 0:
                ticks = len(self.pending) - len(self.pending.lstrip('~'))
                if ticks == len(self.pending) and not final:
                    break
                if ticks >= 3 and not self.code_ticks:
                    self.code_ticks = -ticks
                elif self.code_ticks == -ticks:
                    self.code_ticks = 0
                output.append(self.pending[:ticks])
                self.pending = self.pending[ticks:]
                continue
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

    def calls(self, allowed=None):
        if self.malformed:
            raise ValueError('工具协议未闭合')
        result = []
        for block in self.blocks:
            match = re.fullmatch(r'<tool_call>\s*<function=([\w.-]+)>\s*(.*?)\s*</function>\s*</tool_call\s*>', block, re.S | re.I)
            if not match or (allowed is not None and match[1] not in allowed):
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


class ThinkTextBoundary:
    """把 content 里内联的 <think>…</think> 推理块从正式回答中分出去。

    MiMo 等 OpenAI 兼容端点不一定把推理放在 reasoning_content 字段：
    推理会以内联 <think> 标签混进 content 流。这些块是思考内容，
    不能作为正式回答展示或写进对话历史 —— 但也不能粗暴全局删除：
    代码围栏（反引号）里的字面文本、以及用户正文里合法出现的
    "<think>" 字样必须原样保留。

    只在出现**成对的** <think>…</think> 时才把区间转入 think 通道；
    标签跨 chunk 分割时先缓冲（等标签闭合再判定），流结束时仍未闭合
    的 <think> 块整体按思考内容处理，绝不当作正文。
    """

    OPEN = '<think>'
    CLOSE = '</think>'
    LEAD = ('think:', 'think：')

    def __init__(self):
        self.pending = ''
        self.mode = 'text'      # 'text' | 'think' | 'think_line'
        self.code_ticks = 0
        self.lead = 'pending'   # 'pending' | 'yes' | 'no'：正文开头是否 think: 前缀
        self.complete = True    # 全部内容都得到了明确的通道归属

    @staticmethod
    def _partial_tag_suffix(low, tag):
        """pending 尾部可能是 tag 前缀的长度（0..len(tag)-1）。"""
        for size in range(min(len(tag) - 1, len(low)), 0, -1):
            if low.endswith(tag[:size]):
                return size
        return 0

    def _check_lead(self, final):
        """正文开头的普通文本 think:/think： 前缀（截图泄漏场景）。

        只在**任何输出发出之前**判定一次：开头是 think:+冒号（半角/全角）
        时，前缀本身和该行剩余部分按思考内容处理，换行后回到正文通道。
        判定依据不足（只收到 "thi" 这类前缀）且流未结束时先缓冲；
        一旦确认不是这个形态，立刻放行为正文 —— 正文中间出现的
        "think:" 字样、代码块里的示例一律不受影响。
        """
        if self.lead != 'pending' or self.mode != 'text' or self.code_ticks:
            return
        stripped = self.pending.lstrip()
        if not stripped:
            if final:
                self.lead = 'no'
            return
        low = stripped.lower()
        if low.startswith('think:') or low.startswith('think：'):
            match = re.match(r'^\s*think[:：][ \t]?', self.pending, re.I)
            self.pending = self.pending[match.end():]
            self.lead = 'yes'
            self.mode = 'think_line'
            return
        partial = any(s.startswith(low) or low.startswith(s[:len(low)])
                      for s in self.LEAD if len(low) <= len(s))
        # "think" 的任意前缀（thi/think）都可能长成 think: —— 缓冲等待；
        # 其余任何形态都放行。
        if 'think'.startswith(low[:5]) and len(low) <= 5 and not final:
            return
        if partial and not final:
            return
        self.lead = 'no'

    def feed(self, fragment, final=False):
        """吃进一段 content 增量，返回 [(kind, text)]，kind 为 'text'|'think'。"""
        self.pending += fragment or ''
        output = []
        self._check_lead(final)
        if self.lead == 'pending' and self.mode == 'text' and not self.code_ticks:
            # 开头形态还没判定（可能长成 think:），在判定前一个字都不能发。
            return output
        while self.pending:
            if self.mode == 'think_line':
                # think: 前缀的那一行：整行归思考，换行后回正文。
                idx = self.pending.find('\n')
                if idx < 0:
                    if final:
                        output.append(('think', self.pending))
                        self.pending = ''
                        self.complete = False
                    break
                if self.pending[:idx]:
                    output.append(('think', self.pending[:idx]))
                self.pending = self.pending[idx + 1:]
                self.mode = 'text'
                continue
            if self.mode == 'think':
                low = self.pending.lower()
                idx = low.find(self.CLOSE)
                if idx < 0:
                    keep = self._partial_tag_suffix(low, self.CLOSE)
                    emit = self.pending[:len(self.pending) - keep]
                    if emit:
                        output.append(('think', emit))
                    self.pending = self.pending[len(self.pending) - keep:]
                    break
                if self.pending[:idx]:
                    output.append(('think', self.pending[:idx]))
                self.pending = self.pending[idx + len(self.CLOSE):]
                self.mode = 'text'
                continue
            # ---- text 通道 ----
            # 批量发出到下一个可能改变状态的字符；围栏内只有反引号有意义。
            scan = '`' if self.code_ticks else '<`'
            nxt = len(self.pending)
            for i, ch in enumerate(self.pending):
                if ch in scan:
                    nxt = i
                    break
            if nxt:
                output.append(('text', self.pending[:nxt]))
                self.pending = self.pending[nxt:]
                continue
            head = self.pending[0]
            if head == '`':
                ticks = len(self.pending) - len(self.pending.lstrip('`'))
                if ticks == len(self.pending) and not final:
                    break          # 定界符本身可能被拆成多个 chunk
                if not self.code_ticks:
                    self.code_ticks = ticks
                elif ticks == self.code_ticks:
                    self.code_ticks = 0
                # 数量对不上的反引号串是围栏里的字面内容
                output.append(('text', self.pending[:ticks]))
                self.pending = self.pending[ticks:]
                continue
            if not self.code_ticks:
                low = self.pending.lower()
                if low.startswith(self.OPEN):
                    self.pending = self.pending[len(self.OPEN):]
                    self.mode = 'think'
                    continue
                if len(low) < len(self.OPEN) and self.OPEN.startswith(low) and not final:
                    break          # 可能是 <think> 的前缀，等下一个 chunk
            output.append(('text', head))
            self.pending = self.pending[1:]
        if final and self.pending:
            # 流结束仍有滞留：think 模式的尾巴是思考内容；text 模式的
            # 滞留只可能是半个标签前缀，按字面正文处理。
            output.append(('think' if self.mode == 'think' else 'text', self.pending))
            self.pending = ''
            self.complete = self.mode == 'text'
        return output
