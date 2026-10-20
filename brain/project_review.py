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


def _root():
    root = os.path.join(paths.DATA_DIR, 'project_reviews')
    os.makedirs(root, exist_ok=True)
    return root


def _path(review_id):
    return os.path.join(_root(), str(review_id) + '.json')


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
        'status': 'confirmed', 'title': title, 'evidence': {
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
        path = os.path.join(item['root'], rel)
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


def _public(item):
    out = dict(item)
    out.pop('owner', None)
    out['ok'] = True
    out['progress'] = {'status': item['status'], 'cursor': item['cursor'],
                       'files_seen': item['coverage']['files_seen'],
                       'files_total': item['coverage']['files_total'],
                       'remaining': max(0, item['coverage']['files_total'] - item['cursor'])}
    return out


def invoke(name, args, owner=''):
    args = args if isinstance(args, dict) else {}
    if name == 'project_review_start':
        return json.dumps(start(args.get('path'), owner), ensure_ascii=False)
    if name == 'project_review_step':
        return json.dumps(step(args.get('review_id'), owner, args.get('budget_files', 25)), ensure_ascii=False)
    if name == 'project_review_report':
        return json.dumps(report(args.get('review_id'), owner), ensure_ascii=False)
    return None
