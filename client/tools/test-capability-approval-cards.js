// P20.2 收尾验收：审批卡片的历史状态回填与失效处置。
// 纯逻辑测试（不依赖 DOM/Electron）：卡片用最小 dataset 桩代替。
const test = require('node:test');
const assert = require('node:assert/strict');
const av = require('../src/js/agent-view');

function card(rid, decorated) {
  return { dataset: { capRequestId: rid, capDecorated: decorated ? '1' : undefined } };
}

function cardText(meta) {
  return av.approvalCard(meta);
}

test('pending card shows confirm and deny buttons', () => {
  const html = cardText({ approval: { request_id: 'r1', capability: 'plugins_skills', operation: 'disable', state: 'pending' } });
  assert.match(html, /data-cap-action="approve"/);
  assert.match(html, /data-cap-action="deny"/);
});

test('executing or approved card shows running without buttons', () => {
  for (const state of ['executing', 'approved']) {
    const html = cardText({ approval: { request_id: 'r1', state } });
    assert.match(html, /正在执行/);
    assert.doesNotMatch(html, /data-cap-action="approve"/);
  }
});

test('second identical request hitting executing is not a new pending card', () => {
  // 第二次相同 invoke 命中 executing：registry 返回同一 request_id 且
  // request_state=executing —— 卡片直接渲染为执行中。
  const html = cardText({ approval: { request_id: 'r1', state: 'executing' } });
  assert.match(html, /正在执行/);
  assert.doesNotMatch(html, /data-cap-action="approve"/);
});

test('completed card shows real result; unknown result never reads as success', () => {
  const ok = cardText({ approval: { request_id: 'r1', state: 'completed', result: { ok: true, status: 'succeeded' } } });
  assert.match(ok, /已确认并执行成功/);
  const fail = cardText({ approval: { request_id: 'r1', state: 'completed', result: { ok: false, status: 'failed', error: 'x' } } });
  assert.match(fail, /执行未成功/);
  const unknown = cardText({ approval: { request_id: 'r1', state: 'completed', result: { ok: false, status: 'unknown' } } });
  assert.match(unknown, /结果未知/);
  assert.doesNotMatch(unknown, /执行成功/);
});

test('denied card shows refused', () => {
  const html = cardText({ approval: { request_id: 'r1', state: 'denied' } });
  assert.match(html, /已拒绝/);
  assert.doesNotMatch(html, /data-cap-action="approve"/);
});

test('stale card after brain restart shows expired without buttons', () => {
  for (const state of ['stale', 'missing']) {
    const html = cardText({ approval: { request_id: 'r1', state } });
    assert.match(html, /审批已失效，请重新发起/);
    assert.doesNotMatch(html, /data-cap-action="approve"/);
    assert.doesNotMatch(html, /data-cap-action="deny"/);
  }
});

test('approvalResolutions: missing request id after restart resolves to stale', () => {
  // 模拟 Brain 重启：服务端内存请求全部丢失（byId 为空映射）。
  const cards = [card('r1'), card('r2')];
  const out = av.approvalResolutions(cards, {});
  assert.deepEqual(out.map(o => o.action), ['stale', 'stale']);
});

test('approvalResolutions: known states map through, pending untouched', () => {
  const byId = {
    r1: { request_id: 'r1', state: 'pending' },
    r2: { request_id: 'r2', state: 'executing' },
    r3: { request_id: 'r3', state: 'completed', result: { ok: true } },
    r4: { request_id: 'r4', state: 'denied' },
  };
  const cards = [card('r1'), card('r2'), card('r3'), card('r4'), card('r5')];
  const out = av.approvalResolutions(cards, byId);
  assert.deepEqual(out.map(o => o.action), ['pending', 'executing', 'completed', 'denied', 'stale']);
});

test('approvalResolutions: already decorated cards are skipped', () => {
  const cards = [card('r1', true), card('r2')];
  const out = av.approvalResolutions(cards, {});
  assert.equal(out[0].action, 'skip');
  assert.equal(out[1].action, 'stale');
});

test('approvalResolutions: transient query failure produces no decisions', () => {
  // 页面层在 fetch 异常时直接 return；helper 自身对 null 映射也不产生
  // 任何处置 —— 卡片保持原样，绝不把未知状态误判成已拒绝或已成功。
  const cards = [card('r1')];
  assert.deepEqual(av.approvalResolutions(cards, null), []);
  assert.deepEqual(av.approvalResolutions(cards, undefined), []);
});
