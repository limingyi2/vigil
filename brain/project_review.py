# -*- coding: utf-8 -*-
"""Bounded, resumable project review for the Agent harness.

The evidence layer is deterministic: it discovers the repository, records a
coverage plan, reads source files in bounded batches, and stores findings with
stable IDs and line evidence. A model may summarize the report, but cannot turn
an unverified guess into a confirmed defect.
"""
import hashlib
import json
import os
import re
import tempfile
import time
import uuid

import paths

_EXCLUDE = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build',
            '.tox', '.mypy_cache', '.pytest_cache'}
_SOURCE_EXT = {'.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.css', '.html', '.vue',
               '.java', '.go', '.rs', '.cs', '.cpp', '.h', '.sql', '.sh', '.ps1'}
_CONFIG_NAMES = {'package.json', 'pyproject.toml', 'requirements.txt', 'cargo.toml',
                 'go.mod', 'pom.xml', 'build.gradle', 'dockerfile'}
_MAX_FILES = 20000
_MAX_BYTES = 2_000_000
_MAX_FINDINGS = 500
_TODO = re.compile(r'\b(TODO|FIXME|XXX|HACK)\b', re.I)
_BARE_EXCEPT = re.compile(r'^\s*except\s*(?:Exception|BaseException)?\s*:\s*(?:pass|\.\.\.)\s*$', re.I)
_DEF = re.compile(r'^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(|^\s*(?:export\s+)?function\s+([A-Za-z_]\w*)\s*\(', re.M)
_IDENT_CALL = re.compile(r'\b([A-Za-z_]\w*)\s*\(')


def _root():
    root = os.path.join(paths.DATA_DIR, 'project_reviews')
    os.makedirs(root, exist_ok=True)
    return root


def _path(review_id):
    return os.path.join(_root(), str(review_id) + '.json')


def _inside(root, path):
    target = os.path.realpath(os.path.join(root, path)) if not os.path.isabs(str(path or '')) else os.path.realpath(path)
    root = os.path.realpath(root)
    try:
        return target if os.path.commonpath([root, target]) == root else None
    except ValueError:
        return None


def _read(review_id, owner):
    try:
        with open(_path(review_id), encoding='utf-8') as fh:
            item = json.load(fh)
    except (OSError, ValueError):
        return None
    return item if item.get('owner') == owner else None


def _write(item):
    target = _path(item['review_id'])
    fd, temp = tempfile.mkstemp(prefix='.review-', dir=_root(), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(item, fh, ensure_ascii=False, indent=2)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _discover(root):
    files, dirs = [], set()
    for current, names, leafs in os.walk(root):
        names[:] = sorted(n for n in names if n not in _EXCLUDE and not n.startswith('.'))
        rel_dir = os.path.relpath(current, root)
        if rel_dir != '.':
            dirs.add(rel_dir.replace('\\', '/').split('/')[0])
        for name in sorted(leafs):
            path = os.path.join(current, name)
            rel = os.path.relpath(path, root).replace('\\', '/')
            if os.path.splitext(name)[1].lower() in _SOURCE_EXT or name.lower() in _CONFIG_NAMES:
                files.append(rel)
            if len(files) >= _MAX_FILES:
                break
        if len(files) >= _MAX_FILES:
            break
    return files, sorted(dirs)


def _stack(files):
    names = set(os.path.basename(p).lower() for p in files)
    stack = []
    if 'package.json' in names or any(p.endswith(('.js', '.ts', '.tsx', '.jsx')) for p in files):
        stack.append('JavaScript/TypeScript')
    if 'pyproject.toml' in names or 'requirements.txt' in names or any(p.endswith('.py') for p in files):
        stack.append('Python')
    if 'cargo.toml' in names or any(p.endswith('.rs') for p in files):
        stack.append('Rust')
    if 'go.mod' in names or any(p.endswith('.go') for p in files):
        stack.append('Go')
    return stack or ['未识别（按源码文件审查）']


def _finding(item, kind, severity, rel, line, snippet, title):
    key = '%s|%s|%s|%s' % (kind, rel, line, snippet[:180])
    issue_id = 'REV-' + hashlib.sha1(key.encode('utf-8', 'replace')).hexdigest()[:10].upper()
    if any(x.get('issue_id') == issue_id for x in item['findings']):
        return
    item['findings'].append({'issue_id': issue_id, 'kind': kind, 'severity': severity,
        # Static patterns are leads for a semantic review, not proof of a bug.
        'status': 'candidate', 'title': title, 'evidence': {
            'path': rel, 'line': line, 'snippet': snippet[:400]},
        'acceptance': '复现或补充针对该证据的回归测试后再关闭'})


def start(path, owner):
    raw = str(path or '').strip()
    if not raw or not os.path.isabs(raw):
        return {'ok': False, 'err': '项目路径必须是绝对路径'}
    root = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(root) or os.path.islink(root):
        return {'ok': False, 'err': '项目目录不可访问，或不能使用符号链接'}
    files, dirs = _discover(root)
    review_id = 'review_' + uuid.uuid4().hex[:16]
    item = {'review_id': review_id, 'owner': owner, 'root': root,
        'created_at': time.time(), 'updated_at': time.time(), 'status': 'planned',
        'stack': _stack(files), 'files': files, 'modules': dirs,
        'entrypoints': [p for p in files if os.path.basename(p).lower() in
                        {'main.py', 'app.py', 'server.py', 'index.js', 'package.json', 'pyproject.toml'}][:40],
        'cursor': 0, 'findings': [], 'coverage': {'files_seen': 0, 'files_total': len(files)},
        'semantic_cursor': 0, 'semantic_status': 'pending', 'semantic_skipped': [],
        'limits': {'max_files': _MAX_FILES, 'max_bytes_per_file': _MAX_BYTES}}
    _write(item)
    return _public(item)


def step(review_id, owner, budget_files=25):
    item = _read(review_id, owner)
    if not item:
        return {'ok': False, 'err': '审查任务不存在'}
    try:
        budget = max(1, min(200, int(budget_files)))
    except (TypeError, ValueError):
        budget = 25
    item['status'] = 'running'
    for rel in item['files'][item['cursor']:item['cursor'] + budget]:
        item['cursor'] += 1
        item['coverage']['files_seen'] = item['cursor']
        path = _inside(item['root'], rel)
        if not path:
            _finding(item, 'read_error', 'medium', rel, 1, '路径解析后超出审查项目目录', '源码路径越界')
            continue
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                _finding(item, 'unreviewed_large_file', 'info', rel, 1, '文件超过有界读取上限', '文件未完整审查')
                continue
            with open(path, encoding='utf-8', errors='replace') as fh:
                lines = fh.read().splitlines()
        except OSError as exc:
            _finding(item, 'read_error', 'medium', rel, 1, str(exc), '源码读取失败')
            continue
        for number, line in enumerate(lines, 1):
            if len(item['findings']) >= _MAX_FINDINGS:
                break
            if _TODO.search(line):
                _finding(item, 'deferred_work', 'low', rel, number, line.strip(), '源码包含未完成标记')
            if rel.endswith(('.py', '.js', '.ts')) and _BARE_EXCEPT.match(line):
                _finding(item, 'silent_failure', 'high', rel, number, line.strip(), '异常被静默吞掉')
    if item['cursor'] >= len(item['files']):
        item['status'] = 'completed'
    item['updated_at'] = time.time()
    _write(item)
    return _public(item)


def report(review_id, owner):
    item = _read(review_id, owner)
    return _public(item) if item else {'ok': False, 'err': '审查任务不存在'}


def export_report(review_id, owner):
    item = _read(review_id, owner)
    if not item:
        return {'ok': False, 'err': '审查任务不存在'}
    directory = os.path.join(_root(), 'reports')
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, review_id + '.md')
    lines = ['# 项目语义审查报告', '', '- 项目：`%s`' % item['root'],
             '- 静态扫描：%s，覆盖 %d/%d 个文件' % (item['status'], item['coverage']['files_seen'], item['coverage']['files_total']),
             '- 语义审查：%s，游标 %d/%d，跳过 %d 个文件' % (item.get('semantic_status', 'unknown'),
                 item.get('semantic_cursor', 0), len(item['files']), len(item.get('semantic_skipped') or [])), '']
    if not item['findings']:
        lines.append('未记录发现。')
    for finding in item['findings']:
        evidence = finding.get('evidence') or {}
        lines += ['## %s · %s' % (finding.get('issue_id'), finding.get('status')),
                  '', '- 位置：`%s:%s`' % (evidence.get('path'), evidence.get('line')),
                  '- 严重度：%s' % finding.get('severity'), '- 问题：%s' % finding.get('title')]
        if finding.get('hypothesis'):
            lines.append('- 假设：%s' % finding['hypothesis'])
        if evidence.get('quote') or evidence.get('snippet'):
            lines += ['', '```text', str(evidence.get('quote') or evidence.get('snippet') or '')[:1000], '```']
        if finding.get('validation_plan'):
            lines.append('- 验证计划：%s' % finding['validation_plan'])
        if finding.get('verification'):
            lines.append('- 测试执行：run `%s`, step `%s`, exit code `%s`' % (
                finding['verification'].get('run_id'), finding['verification'].get('step_id'),
                finding['verification'].get('exit_code')))
        lines.append('')
    with open(target, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(lines).rstrip() + '\n')
    return {'ok': True, 'path': target, 'findings': len(item['findings'])}


def semantic_batch(review_id, owner, budget_files=6, budget_chars=30000):
    """Return bounded line-numbered files for a resumable model-led pass."""
    item = _read(review_id, owner)
    if not item:
        return {'ok': False, 'err': '审查任务不存在'}
    try:
        count = max(1, min(12, int(budget_files)))
        char_budget = max(1000, min(60000, int(budget_chars)))
    except (TypeError, ValueError):
        count, char_budget = 6, 30000
    cursor = int(item.get('semantic_cursor') or 0)
    batch, used, functions = [], 0, []
    while cursor < len(item['files']) and len(batch) < count and used < char_budget:
        rel = item['files'][cursor]
        cursor += 1
        target = _inside(item['root'], rel)
        try:
            if not target or os.path.getsize(target) > min(_MAX_BYTES, 100000):
                raise ValueError('超过语义审查单文件上限')
            with open(target, encoding='utf-8', errors='replace') as fh:
                source = fh.read()
        except (OSError, ValueError) as exc:
            item.setdefault('semantic_skipped', []).append({'path': rel, 'reason': str(exc)[:120]})
            continue
        remaining = char_budget - used
        if len(source) > remaining and batch:
            cursor -= 1
            break
        if len(source) > remaining:
            item.setdefault('semantic_skipped', []).append({'path': rel, 'reason': '超过本批读取预算'})
            continue
        lines = source.splitlines()
        defs = []
        calls = []
        for no, line in enumerate(lines, 1):
            match = _DEF.match(line)
            if match:
                defs.append({'name': match.group(1) or match.group(2), 'line': no})
            calls.extend({'name': m.group(1), 'line': no} for m in _IDENT_CALL.finditer(line))
        numbered = '\n'.join('%5d | %s' % (i, line) for i, line in enumerate(lines, 1))
        batch.append({'path': rel, 'sha256': hashlib.sha256(source.encode()).hexdigest(),
                      'line_count': len(lines), 'definitions': defs,
                      'call_references': calls[:400], 'source': numbered})
        functions.extend({'path': rel, **d} for d in defs)
        used += len(source)
    item['semantic_cursor'] = cursor
    item['semantic_status'] = 'completed' if cursor >= len(item['files']) else 'running'
    item['updated_at'] = time.time()
    _write(item)
    return {'ok': True, 'review_id': review_id, 'files': batch,
            'definitions': functions, 'chars': used, 'cursor': cursor,
            'files_total': len(item['files']), 'status': item['semantic_status'],
            'skipped': list(item.get('semantic_skipped') or [])[-100:]}


def submit_finding(review_id, owner, path, line, title, hypothesis, evidence, validation_plan):
    """Persist a model hypothesis only when its quoted source evidence matches."""
    item = _read(review_id, owner)
    if not item:
        return {'ok': False, 'err': '审查任务不存在'}
    target = _inside(item['root'], str(path or ''))
    try:
        line = int(line)
        rel = os.path.relpath(target, item['root']).replace('\\', '/')
        if not target or rel not in item['files'] or line < 1:
            raise ValueError()
        with open(target, encoding='utf-8', errors='replace') as fh:
            lines = fh.read().splitlines()
        snippet = lines[line - 1]
    except (OSError, ValueError, IndexError, TypeError):
        return {'ok': False, 'err': '证据位置不在审查项目内或代码行不存在'}
    evidence = str(evidence or '').strip()
    nearby = '\n'.join(lines[max(0, line - 3):min(len(lines), line + 2)])
    if not evidence or evidence not in nearby:
        return {'ok': False, 'err': '提交的证据必须与该位置附近的源码逐字匹配'}
    title, hypothesis, validation_plan = (str(x or '').strip()[:500]
        for x in (title, hypothesis, validation_plan))
    if not title or not hypothesis or not validation_plan:
        return {'ok': False, 'err': '标题、缺陷假设和验证计划都必须提供'}
    issue_id = 'REV-' + hashlib.sha1(('%s|%s|%s|%s' %
        (rel, line, evidence, hypothesis)).encode('utf-8', 'replace')).hexdigest()[:10].upper()
    for existing in item['findings']:
        if existing.get('issue_id') == issue_id:
            return {'ok': True, 'finding': existing, 'duplicate': True}
    if len(item['findings']) >= _MAX_FINDINGS:
        return {'ok': False, 'err': '审查发现已达到上限'}
    finding = {'issue_id': issue_id, 'kind': 'semantic_hypothesis', 'severity': 'unconfirmed',
        'status': 'candidate', 'title': title, 'hypothesis': hypothesis,
        'validation_plan': validation_plan, 'evidence': {'path': rel, 'line': line,
            'snippet': snippet[:400], 'quote': evidence[:1000],
            'source_sha256': hashlib.sha256('\n'.join(lines).encode()).hexdigest()},
        'acceptance': '运行验证计划并记录可信工具执行证据后再确认缺陷'}
    item['findings'].append(finding)
    item['updated_at'] = time.time()
    _write(item)
    return {'ok': True, 'finding': finding}


def confirm_finding(review_id, issue_id, owner, run_id, step_id, expected_failure_text):
    """Confirm a semantic hypothesis from a real, failing local test receipt."""
    item = _read(review_id, owner)
    if not item:
        return {'ok': False, 'err': '审查任务不存在'}
    finding = next((x for x in item['findings'] if x.get('issue_id') == issue_id), None)
    if not finding or finding.get('status') != 'candidate':
        return {'ok': False, 'err': '缺陷假设不存在或已处理'}
    try:
        import agent_runs
        run = agent_runs.snapshot(str(run_id or ''), owner)
        sid = int(step_id)
        step = next((s for s in (run or {}).get('steps', []) if s.get('step_id') == sid), None)
    except (TypeError, ValueError):
        run, step = None, None
    if not step or step.get('kind') != 'tool' or step.get('tool') != 'local_exec' \
            or step.get('status') != 'completed' or step.get('result_state') != 'returned':
        return {'ok': False, 'err': '找不到可信的本机测试执行记录'}
    output = str(step.get('result') or '')
    match = re.search(r'\n\[VIGIL_EXEC_EXIT_CODE=(-?\d+)\]\s*$', output)
    args = step.get('input') if isinstance(step.get('input'), dict) else {}
    command = str(args.get('cmd') or '')
    expected = str(expected_failure_text or '').strip()[:160]
    root = os.path.normcase(item['root']).lower()
    if (not match or int(match.group(1)) == 0 or root not in os.path.normcase(command).lower()
            or not expected or expected not in output
            or not re.search(r'failed|failure|assert|error', output, re.I)):
        return {'ok': False, 'err': '测试执行没有提供与预期缺陷匹配的失败证据'}
    evidence = finding.get('evidence') or {}
    target = _inside(item['root'], evidence.get('path', ''))
    try:
        with open(target, encoding='utf-8', errors='replace') as fh:
            digest = hashlib.sha256('\n'.join(fh.read().splitlines()).encode()).hexdigest()
    except OSError:
        digest = ''
    if not digest or digest != evidence.get('source_sha256'):
        return {'ok': False, 'err': '证据源码已变化，请重新审查后再确认'}
    finding['status'] = 'confirmed'
    finding['severity'] = 'confirmed_by_test'
    finding['verification'] = {'run_id': str(run_id), 'step_id': sid,
        'command': command[:240], 'exit_code': int(match.group(1)),
        'expected_failure_text': expected, 'output': output[:4000],
        'finished_at': step.get('finished_at'), 'confirmed_at': time.time()}
    item['updated_at'] = time.time()
    _write(item)
    return {'ok': True, 'finding': finding}


def _public(item):
    out = dict(item)
    out.pop('owner', None)
    out['ok'] = True
    out['progress'] = {'status': item['status'], 'cursor': item['cursor'],
                       'files_seen': item['coverage']['files_seen'],
                       'files_total': item['coverage']['files_total'],
                       'remaining': max(0, item['coverage']['files_total'] - item['cursor']),
                       'semantic_status': item.get('semantic_status', 'unknown'),
                       'semantic_cursor': item.get('semantic_cursor', 0),
                       'semantic_remaining': max(0, item['coverage']['files_total'] - item.get('semantic_cursor', 0)),
                       'semantic_skipped': len(item.get('semantic_skipped') or [])}
    return out


def invoke(name, args, owner=''):
    args = args if isinstance(args, dict) else {}
    if name == 'project_review_start':
        return json.dumps(start(args.get('path'), owner), ensure_ascii=False)
    if name == 'project_review_step':
        return json.dumps(step(args.get('review_id'), owner, args.get('budget_files', 25)), ensure_ascii=False)
    if name == 'project_review_report':
        return json.dumps(report(args.get('review_id'), owner), ensure_ascii=False)
    if name == 'project_review_export':
        return json.dumps(export_report(args.get('review_id'), owner), ensure_ascii=False)
    if name == 'project_review_semantic_batch':
        return json.dumps(semantic_batch(args.get('review_id'), owner,
            args.get('budget_files', 6), args.get('budget_chars', 30000)), ensure_ascii=False)
    if name == 'project_review_submit_finding':
        return json.dumps(submit_finding(args.get('review_id'), owner, args.get('path'),
            args.get('line'), args.get('title'), args.get('hypothesis'), args.get('evidence'),
            args.get('validation_plan')), ensure_ascii=False)
    if name == 'project_review_confirm_finding':
        return json.dumps(confirm_finding(args.get('review_id'), args.get('issue_id'), owner,
            args.get('run_id'), args.get('step_id'), args.get('expected_failure_text')), ensure_ascii=False)
    return None
