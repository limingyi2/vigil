# -*- coding: utf-8 -*-
"""Evidence gated repair records for project reviews.

Plans are viewable before a write. Applying a plan requires an explicit
``approved`` flag and an exact old-text match, so retries cannot repeat an
uncertain side effect or overwrite a concurrent edit.
"""
import hashlib
import json
import os
import re
import tempfile
import time
import uuid

import project_review


def _root():
    root = os.path.join(project_review.paths.DATA_DIR, 'project_reviews', 'repairs')
    os.makedirs(root, exist_ok=True)
    return root


def _file(plan_id):
    return os.path.join(_root(), str(plan_id) + '.json')


def _load(plan_id, owner):
    try:
        with open(_file(plan_id), encoding='utf-8') as fh:
            item = json.load(fh)
    except (OSError, ValueError):
        return None
    return item if item.get('owner') == owner else None


def _save(item):
    fd, temp = tempfile.mkstemp(prefix='.repair-', dir=_root(), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(item, fh, ensure_ascii=False, indent=2)
        os.replace(temp, _file(item['plan_id']))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _inside(root, path):
    target = os.path.realpath(os.path.join(root, path)) if not os.path.isabs(path) else os.path.realpath(path)
    root = os.path.realpath(root)
    try:
        return target if os.path.commonpath([root, target]) == root else None
    except ValueError:
        return None


def plan(review_id, issue_id, path, old, new, owner):
    review = project_review.report(review_id, owner)
    if not review.get('ok'):
        return review
    finding = next((x for x in review.get('findings', []) if x.get('issue_id') == issue_id), None)
    if not finding:
        return {'ok': False, 'err': '问题 ID 不存在，不能制定修复方案'}
    if finding.get('status') != 'confirmed':
        return {'ok': False, 'err': '静态扫描提示尚未由实际证据确认，不能制定自动修复方案',
                'status': finding.get('status', 'unknown')}
    target = _inside(review['root'], str(path or finding['evidence']['path']))
    if not target or not os.path.isfile(target):
        return {'ok': False, 'err': '修复路径不在审查项目内，或文件不存在'}
    old = str(old if old is not None else '')
    new = str(new if new is not None else '')
    try:
        with open(target, encoding='utf-8') as fh:
            base = fh.read()
    except OSError as exc:
        return {'ok': False, 'err': '读取待修复文件失败：%s' % str(exc)[:120]}
    item = {'plan_id': 'fix_' + uuid.uuid4().hex[:16], 'owner': owner,
        'review_id': review_id, 'issue_id': issue_id, 'path': target,
        'base_sha256': hashlib.sha256(base.encode()).hexdigest(),
        'old_sha256': hashlib.sha256(old.encode()).hexdigest(),
        'new_sha256': hashlib.sha256(new.encode()).hexdigest(),
        'old': old, 'new': new, 'status': 'proposed', 'created_at': time.time(),
        'verification': None}
    _save(item)
    out = dict(item); out.pop('owner', None)
    out['old'] = old[:12000]; out['new'] = new[:12000]
    out['ok'] = True
    return out


def apply(plan_id, owner, approved=False):
    item = _load(plan_id, owner)
    if not item:
        return {'ok': False, 'err': '修复方案不存在'}
    if item['status'] != 'proposed':
        return {'ok': False, 'err': '修复方案已处理，不能重复执行', 'status': item['status']}
    if approved is not True:
        return {'ok': False, 'err': '需要明确授权后才能写入文件', 'status': 'awaiting_authorization'}
    review = project_review.report(item.get('review_id'), owner)
    scoped_path = _inside(review.get('root'), item.get('path')) if review.get('ok') else None
    if not scoped_path or scoped_path != os.path.realpath(item.get('path') or ''):
        item['status'] = 'blocked'; item['failure'] = '修复路径解析后超出审查项目目录'; _save(item)
        return {'ok': False, 'err': item['failure'], 'status': 'blocked'}
    try:
        with open(item['path'], encoding='utf-8') as fh:
            current = fh.read()
    except OSError as exc:
        item['status'] = 'failed'; item['failure'] = str(exc)[:240]; _save(item)
        return {'ok': False, 'err': '读取待修复文件失败', 'status': 'failed'}
    if hashlib.sha256(current.encode()).hexdigest() != item['base_sha256']:
        item['status'] = 'blocked'; item['failure'] = '文件已变化，拒绝覆盖'; _save(item)
        return {'ok': False, 'err': '文件在方案制定后已变化，拒绝覆盖', 'status': 'blocked'}
    updated = current.replace(item['old'], item['new'], 1)
    if updated == current:
        item['status'] = 'blocked'; item['failure'] = '原文片段未找到'; _save(item)
        return {'ok': False, 'err': '原文片段未找到，未写入', 'status': 'blocked'}
    fd, temp = tempfile.mkstemp(prefix='.vigil-fix-', dir=os.path.dirname(item['path']), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(updated)
        os.replace(temp, item['path'])
    finally:
        if os.path.exists(temp): os.unlink(temp)
    item['status'] = 'applied'; item['applied_at'] = time.time()
    item['applied_sha256'] = hashlib.sha256(updated.encode()).hexdigest()
    review = project_review.report(item.get('review_id'), owner)
    relpath = os.path.relpath(item['path'], review['root']).replace('\\', '/') if review.get('ok') else os.path.basename(item['path'])
    item['diff'] = {'path': relpath,
                    'before_sha256': item['base_sha256'],
                    'after_sha256': item['applied_sha256'],
                    'replaced_chars': len(item['old']), 'inserted_chars': len(item['new'])}
    _save(item)
    out = dict(item); out.pop('owner', None); out.pop('old', None); out.pop('new', None)
    out['ok'] = True
    return out


def verify(plan_id, owner, run_id, step_id):
    item = _load(plan_id, owner)
    if not item:
        return {'ok': False, 'err': '修复方案不存在'}
    if item.get('status') not in ('applied', 'regression'):
        return {'ok': False, 'err': '只有已应用且尚未验证通过的方案可以验证', 'status': item.get('status')}
    try:
        with open(item['path'], encoding='utf-8') as fh:
            current_hash = hashlib.sha256(fh.read().encode()).hexdigest()
    except OSError:
        return {'ok': False, 'err': '修复文件当前不可读取，不能验证'}
    if current_hash != item.get('applied_sha256'):
        item['status'] = 'blocked'
        item['failure'] = '修复应用后文件再次变化，验证证据无法绑定当前内容'
        _save(item)
        return {'ok': False, 'err': item['failure'], 'status': item['status']}
    # Verification is derived from a durable Agent tool step. A model-supplied
    # boolean or pasted test log is never sufficient evidence.
    try:
        import agent_runs
        run = agent_runs.snapshot(str(run_id or ''), owner)
        sid = int(step_id)
        step = next((s for s in (run or {}).get('steps', []) if s.get('step_id') == sid), None)
    except (TypeError, ValueError):
        run, step = None, None
    if not run or not step or step.get('kind') != 'tool' or step.get('tool') != 'local_exec' \
            or step.get('status') != 'completed':
        return {'ok': False, 'err': '找不到可信的本机命令执行记录'}
    result = str(step.get('result') or '')
    match = re.search(r'\n\[VIGIL_EXEC_EXIT_CODE=(-?\d+)\]\s*$', result)
    args = step.get('input') if isinstance(step.get('input'), dict) else {}
    command = str(args.get('cmd') or '')
    review = project_review.report(item.get('review_id'), owner)
    root = os.path.normcase(os.path.abspath(review.get('root') or '')) if review.get('ok') else ''
    if not match or not root or root.lower() not in os.path.normcase(command).lower():
        return {'ok': False, 'err': '执行记录缺少可信退出码，或命令未明确指向审查项目'}
    exit_code = int(match.group(1))
    status = 'passed' if exit_code == 0 and step.get('result_state') == 'returned' else 'failed'
    record = {'run_id': str(run_id), 'step_id': sid, 'command': command[:240],
              'exit_code': exit_code, 'status': status,
              'output': result[:4000], 'started_at': step.get('started_at'),
              'finished_at': step.get('finished_at'), 'at': time.time()}
    item.setdefault('verification_attempts', []).append(record)
    item['verification_attempts'] = item['verification_attempts'][-20:]
    item['verification'] = record
    item['status'] = 'verified' if status == 'passed' else 'regression'
    _save(item)
    out = dict(item); out.pop('owner', None); out.pop('old', None); out.pop('new', None); out['ok'] = True
    return out


def invoke(name, args, owner=''):
    args = args if isinstance(args, dict) else {}
    if name == 'project_repair_plan':
        return json.dumps(plan(args.get('review_id'), args.get('issue_id'), args.get('path'),
                               args.get('old'), args.get('new'), owner), ensure_ascii=False)
    if name == 'project_repair_apply':
        return json.dumps(apply(args.get('plan_id'), owner, args.get('approved') is True), ensure_ascii=False)
    if name == 'project_repair_verify':
        return json.dumps(verify(args.get('plan_id'), owner, args.get('run_id'),
                                 args.get('step_id')), ensure_ascii=False)
    return None
