# -*- coding: utf-8 -*-
"""Evidence gated repair records for project reviews.

Plans are viewable before a write. Applying a plan requires an explicit
``approved`` flag and an exact old-text match, so retries cannot repeat an
uncertain side effect or overwrite a concurrent edit.
"""
import hashlib
import json
import os
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
    target = os.path.abspath(os.path.join(root, path)) if not os.path.isabs(path) else os.path.abspath(path)
    root = os.path.abspath(root)
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
    item['status'] = 'applied'; item['applied_at'] = time.time(); _save(item)
    out = dict(item); out.pop('owner', None); out.pop('old', None); out.pop('new', None)
    out['ok'] = True
    return out


def verify(plan_id, owner, passed, evidence='', command=''):
    item = _load(plan_id, owner)
    if not item:
        return {'ok': False, 'err': '修复方案不存在'}
    item['verification'] = {'passed': bool(passed), 'evidence': str(evidence or '')[:2000],
                            'command': str(command or '')[:240], 'at': time.time()}
    item['status'] = 'verified' if passed else 'regression'
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
        return json.dumps(verify(args.get('plan_id'), owner, args.get('passed') is True,
                                 args.get('evidence'), args.get('command')), ensure_ascii=False)
    return None
