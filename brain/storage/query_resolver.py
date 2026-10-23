"""Bounded, deterministic query grounding. No model calls or persistent anchors.

Context and confirmed entities are supplied by trusted application code. Corpus
text, assistant messages and previous rewrites are never resolution authorities.
"""
import re
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


_CURRENT = ContextVar('retrieval_context', default=None)
_POINTER = re.compile(r'刚才那个|这个|那个|这份|那份|上述|前者|后者|(?<!其)[他她它]')
_SWITCH = re.compile(r'换个话题|换一个话题|另一个话题|不谈[^，。！？]*了|先不说[^，。！？]*|不说[^，。！？]*了|说回|重新开始')
_FIELDS = re.compile(r'工资|薪资|薪水|金额|价格|生日|日期|版本|文件名')
_NAME = re.compile(r'(?:欧阳|司马|诸葛|[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭鲁马任袁柳方石白林罗梁宋唐徐高郭邓胡萧曾程董叶杜钟汪田丁姚卢陆夏贾江])'
                   r'[\u4e00-\u9fff]{1,2}?(?=的|工资|薪资|薪水|生日|是|在|来自|与|和|、|同时|一起|[，。！？：；（）\s]|$)')
_LITERAL = re.compile(r'(?<![A-Za-z0-9_.])(?:v?\d+\.\d+(?:\.\d+)*|\d{4}[-/]\d{1,2}[-/]\d{1,2}|'
                      r'\d+(?:\.\d+)?(?:元|万元|美元)|[A-Za-z0-9_./\\-]+\.(?:py|md|txt|pdf|docx|xlsx|json|csv|log|js|ts))(?![A-Za-z0-9_.])', re.I)
_QUALIFIER = re.compile(r'[A-Za-z0-9\u4e00-\u9fff]{1,12}(?:公司|部门)')


@dataclass(frozen=True)
class Entity:
    name: str
    entity_id: str = ''
    qualifiers: tuple = ()
    kind: str = 'person'


@dataclass(frozen=True)
class QueryContext:
    conversation_id: str
    scope: tuple
    history: tuple = ()  # Original user text only, in chronological order.
    confirmed_entities: tuple = ()
    user_query: str = ''  # Trusted current Turn text, never a model tool argument.


@dataclass(frozen=True)
class Resolution:
    original_query: str
    resolved_query: str
    anchor_entities: tuple
    resolution_status: str
    resolution_source: str
    literals: tuple = ()

    def to_dict(self):
        return dict(original_query=self.original_query, resolved_query=self.resolved_query,
                    anchor_entities=[dict(name=e.name, entity_id=e.entity_id,
                        qualifiers=list(e.qualifiers), kind=e.kind) for e in self.anchor_entities],
                    resolution_status=self.resolution_status, resolution_source=self.resolution_source)

    @property
    def guarded(self):
        return bool(self.anchor_entities or self.literals or self.resolution_status == 'ambiguous')


def scope_key(scope):
    return tuple((k, tuple(sorted(v)) if isinstance(v, (list, tuple)) else str(v))
                 for k, v in sorted(scope.items()) if v is not None)


def current_context():
    return _CURRENT.get()


@contextmanager
def bind(context):
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def brain_scope(brain):
    """Scope observation only; never grants access or changes a storage owner."""
    src = getattr(brain, '_cur_src', 'pc')
    persona = brain._persona_key_for(src) if hasattr(brain, '_persona_key_for') else ''
    cid = str(getattr(brain, '_cur_conversation', '') or '')
    scope = dict(src=src, chan=src, persona=persona, conversation_id=cid, workspace_ids=[])
    if cid:
        from . import db
        with db.read_once() as conn:
            rows = conn.execute('SELECT c.owner,c.workspace_id,w.key FROM conversations c '
                'LEFT JOIN workspaces w ON w.owner=c.owner AND w.src=c.src AND w.persona=c.persona '
                'AND w.id=c.workspace_id WHERE c.id=? AND c.src=? AND c.persona=?',
                (cid, src, persona)).fetchall()
        if len(rows) != 1:
            return None
        scope['workspace_ids'] = [rows[0][1]] if rows[0][1] else []
        scope['workspace_key'] = rows[0][2] or ''
        scope['conversation_owner'] = rows[0][0]
    return scope


def for_brain(brain, conversation_id=None):
    if brain is None:
        return None
    try:
        scope = brain_scope(brain)
        if scope is None or (conversation_id is not None and
                            str(conversation_id) != scope['conversation_id']):
            return None
        key = scope_key(scope)
        # Old history lacks workspace provenance: do not inherit its anchors.
        history = tuple(m['content'] for m in list(getattr(brain, 'hist', []))[-24:]
                        if m.get('role') == 'user' and m.get('_retrieval_scope') == key
                        and isinstance(m.get('content'), str))
        return QueryContext(scope['conversation_id'], key, history)
    except Exception:
        return None


def for_memory(path):
    context = current_context()
    if not isinstance(context, QueryContext):
        return context
    try:
        import memory_store_v2
        scope = dict(context.scope)
        expected = memory_store_v2.default_store_path(scope['persona'], scope['src'])
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(expected)):
            return context
    except Exception:
        pass
    return QueryContext('', (), user_query=context.user_query)  # No other partition's anchors.


def for_turn(turn):
    context = for_brain(getattr(turn, 'brain', None), getattr(turn, 'conversation_id', None))
    original = str(getattr(turn, 'text', '') or '')
    if context is None:
        return QueryContext(str(getattr(turn, 'conversation_id', '') or ''), (), user_query=original)
    return QueryContext(context.conversation_id, context.scope, context.history,
                        context.confirmed_entities, original)


def entities(text, confirmed=()):
    names = list(dict.fromkeys(_NAME.findall(text)))
    names += [e.name for e in confirmed if e.name in text and e.name not in names]
    out = []
    for name in names:
        known = [e for e in confirmed if e.name == name]
        selected = [e for e in known if e.qualifiers and all(q in text for q in e.qualifiers)]
        if selected:
            out.extend(selected)
        elif known:
            out.extend(known)
        else:
            qualifiers = tuple(_QUALIFIER.findall(text[:text.find(name)]))[-1:]
            out.append(Entity(name, qualifiers=qualifiers))
    out.extend(Entity(value, kind='literal') for value in _LITERAL.findall(text)
               if '.' in value and value not in names)
    return tuple(dict.fromkeys(out))


def resolve(query, context=None, scope=None):
    original = str(query or '')
    text = original.strip()
    context = context if context is not None else current_context()
    pointer = _POINTER.search(text)
    # A complete descriptive search ("他养了只猫叫年糕") is not a follow-up.
    dependent = bool(pointer) and bool(
        len(text) <= 4 or pointer.group() not in {'他', '她', '它'} or
        re.search(r'的|工资|薪资|薪水|生日|[?？]|多少|什么|哪里|怎么|是否|吗|呢', text))
    literals = tuple(dict.fromkeys(_LITERAL.findall(text)))
    valid = isinstance(context, QueryContext) and bool(context.conversation_id)
    if valid and scope is not None:
        supplied = dict(context.scope)
        supplied.setdefault('conversation_id', context.conversation_id)
        valid = all(supplied.get(k) == v for k, v in scope_key(scope)
                    if k in {'owner', 'src', 'persona', 'chan', 'conversation_id', 'workspace_ids'})
    confirmed = context.confirmed_entities if valid else ()
    switches = list(_SWITCH.finditer(text))
    anchors = entities(text[switches[-1].end():] if switches else text, confirmed)
    source = 'explicit_query'
    # A name and a dependent pronoun in the same question are not necessarily
    # coreferential (e.g. "李四和他的工资" may refer to someone else).
    # Do not bind the pronoun to the only explicit name by position.
    if dependent and anchors:
        return Resolution(original, original, anchors, 'ambiguous',
                          'mixed_explicit_entity_and_reference', literals)
    if not anchors and dependent and valid and not _SWITCH.search(text):
        anchors = tuple(confirmed)
        source = 'confirmed_entities'
        for raw in reversed(context.history[-12:]):
            if not isinstance(raw, str):
                continue
            raw = raw[:4000]
            switches = list(_SWITCH.finditer(raw))
            found = entities(raw[switches[-1].end():] if switches else raw, confirmed)
            if found:
                if _POINTER.search(raw):
                    return Resolution(original, original, found, 'ambiguous',
                                      'mixed_reference_in_user_history', literals)
                anchors, source = found, 'original_user_history'
                break
            if switches:
                anchors = ()
                break
    if dependent:
        if len(anchors) != 1:
            return Resolution(original, original, anchors, 'ambiguous',
                              source if valid else 'missing_trusted_context', literals)
        # No top-1, assistant text, generated query or recursive resolver call.
        entity = anchors[0]
        replacement = ''.join(entity.qualifiers) + entity.name
        resolved = _POINTER.sub(replacement, text)
        return Resolution(original, resolved, anchors, 'resolved', source, literals)
    if not anchors and re.search(r'\S{1,20}的(?:工资|薪资|薪水|生日)', text):
        return Resolution(original, original, (), 'ambiguous', 'unrecognized_entity', literals)
    if len({e.name for e in anchors}) != len(anchors):
        return Resolution(original, original, anchors, 'ambiguous', 'same_name_identity', literals)
    return Resolution(original, original, anchors, 'standalone', source, literals)


def prepare(query, context=None, scope=None):
    """Resolver faults fail closed, never fall back to an ungrounded pronoun."""
    try:
        context = context if context is not None else current_context()
        if isinstance(context, QueryContext) and context.user_query:
            query = context.user_query
        return resolve(query, context, scope)
    except Exception:
        return Resolution(str(query or ''), str(query or ''), (), 'ambiguous', 'resolver_failed')


def guarded_text(resolution, text, meta=None, locator=''):
    """Return supported original clauses only; corpus content cannot change anchors."""
    if resolution.resolution_status == 'ambiguous':
        return ''
    if not resolution.guarded:
        return text
    locator = locator or ' '.join(str((meta or {}).get(k) or '') for k in ('source', 'locator', 'title'))
    def is_file(value):
        return bool(re.search(r'\.(?:py|md|txt|pdf|docx|xlsx|json|csv|log|js|ts)$', value, re.I))

    def exact(value, haystack):
        return re.search(r'(?<![A-Za-z0-9_.])' + re.escape(value) + r'(?![A-Za-z0-9_.])', haystack, re.I)

    for literal in resolution.literals:
        if not exact(literal, text + ('\n' + locator if is_file(literal) else '')):
            return ''
    fields = set(_FIELDS.findall(resolution.resolved_query.replace('薪资', '工资').replace('薪水', '工资')))
    if not resolution.anchor_entities:
        if fields and not fields.intersection(_FIELDS.findall(text.replace('薪资', '工资').replace('薪水', '工资'))):
            return ''
        return text
    clauses = re.split(r'(?<=[。！？；\n])', text)
    supported = []
    for clause in clauses:
        names = {e.name for e in entities(clause)}
        for anchor in resolution.anchor_entities:
            file_match = anchor.kind == 'literal' and is_file(anchor.name) and exact(anchor.name, locator)
            if (anchor.name not in names and not file_match) or not all(q in clause for q in anchor.qualifiers):
                continue
            identities = (meta or {}).get('entities', [])
            if anchor.entity_id and not anchor.qualifiers and not any(
                    isinstance(e, dict) and e.get('id') == anchor.entity_id and
                    e.get('name') == anchor.name for e in identities):
                continue
            if anchor.kind == 'person' and len(resolution.anchor_entities) == 1 and names - {anchor.name}:
                continue
            if fields and not fields.intersection(_FIELDS.findall(clause.replace('薪资', '工资').replace('薪水', '工资'))):
                continue
            supported.append(clause)
            break
    return ''.join(supported).strip()


def conflicting_names(resolution, candidates):
    """Different corpus IDs never become one identity merely by sharing a name."""
    conflicts = set()
    for anchor in resolution.anchor_entities:
        if anchor.kind != 'person' or anchor.entity_id or anchor.qualifiers:
            continue
        identities = set()
        unverified = set()
        for text, meta in candidates:
            supported = guarded_text(resolution, text, meta)
            if not supported:
                continue
            found = set()
            for e in (meta or {}).get('entities', []):
                if isinstance(e, dict) and e.get('name') == anchor.name and e.get('id'):
                    found.add('id:' + e['id'])
            if not found:
                found.update('qualifier:' + q for q in _QUALIFIER.findall(supported))
            if found:
                identities.update(found)
            else:
                unverified.add(supported)
        if len(identities) > 1 or (identities and unverified) or len(unverified) > 1:
            conflicts.add(anchor.name)
    return conflicts


def guard_hits(resolution, hits):
    return [] if conflicting_names(resolution, [(text, {}) for _, text in hits]) else hits


def metadata(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}
