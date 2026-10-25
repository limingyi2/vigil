const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../src/js/agent-view');
test('tool evidence updates a stable identity with command and collapsed real details',()=>{
  const event={tool:'local_exec',run_id:'run1',call_id:'call1',state:'started',input:{cmd:'python -m pytest -q'}};
  const start=view.step({...event,purpose:'为检查项目测试而运行 pytest'}),end=view.step({...event,purpose:'为检查项目测试而运行 pytest',state:'returned',output:'3 passed'});
  assert.match(start,/正在运行命令/);assert.match(start,/python -m pytest -q/);
  assert.match(start,/操作目的/);assert.match(start,/为检查项目测试而运行 pytest/);
  const summary=start.match(/<summary>([\s\S]*?)<\/summary>/)?.[1] || '';
  assert.match(summary,/agent-step-summary-purpose/,'purpose summary is inside the always-visible summary');
  assert.match(summary,/为检查项目测试而运行 pytest/);
  assert.match(start,/执行内容/);assert.doesNotMatch(start,/执行结果/);
  assert.match(end,/运行了命令/);assert.match(end,/3 passed/);
  assert.match(end,/执行结果/);
  assert.equal(start.match(/data-step-key="([^"]+)"/)[1],end.match(/data-step-key="([^"]+)"/)[1]);
  assert.doesNotMatch(start,/<details[^>]+ open/);
});
test('historical tool steps without a purpose use an honest fallback',()=>{
  const html=view.step({tool:'local_exec',state:'started',input:{cmd:'dir'}});
  assert.match(html,/具体目的未记录/);
  assert.match(html.match(/<summary>([\s\S]*?)<\/summary>/)?.[1] || '',/具体目的未记录/);
});
test('purpose preview is escaped, redacted, clipped and distinct from command content',()=>{
  const purpose='检查项目状态 <script>'+('很长的目的'.repeat(40))+' token=private_secret_123456';
  const html=view.step({tool:'local_exec',state:'started',purpose,input:{cmd:'echo command'}});
  const summary=html.match(/<summary>([\s\S]*?)<\/summary>/)?.[1] || '';
  assert.ok(summary.length<700,'summary preview has a bounded layout');
  assert.match(summary,/目的：检查项目状态 &lt;script&gt;/);
  assert.doesNotMatch(html,/private_secret_123456|<script>/);
  assert.match(html,/echo command/);
});
test('tool progress status mapping stays conservative for every known and unknown state',()=>{
  const cases=[
    ['started',false,'正在执行工具操作'],['running',false,'正在执行工具操作'],
    ['in_flight',false,'正在执行工具操作'],['awaiting_authorization',false,'等待你批准本次操作'],
    ['denied',false,'操作已拒绝，未执行'],['returned',false,'工具已返回结果，正在继续'],
    ['returned',true,'工具返回了错误'],['failed',false,'工具执行失败'],
    ['not_dispatched',false,'工具未派发，操作未执行'],['unknown',false,'执行结果尚未确认，需要核对'],
    ['not_returned',false,'尚未收到执行结果，需要核对'],
    ['interrupted',false,'工具执行中断，实际结果需要核对'],
    ['new_future_state',false,'工具状态未确认'],['',false,'工具状态未确认'],
  ];
  for(const [state,error,summary] of cases){
    assert.equal(view.toolProgress({state,error}).summary,summary,`state=${state||'<empty>'}`);
  }
});
test('command errors stay errors and parameters/results are escaped and redacted',()=>{
  const html=view.step({tool:'local_exec',state:'failed',input:{cmd:'API_KEY=secret1 command',token:'secret2'},output:'Authorization: Bearer secret3',error:'<script>failure</script>'});
  assert.match(html,/命令执行失败/);assert.doesNotMatch(html,/secret[123]|<script>/);
  assert.match(html,/&lt;script&gt;/);
});
test('duration is derived from actual elapsed time',()=>assert.equal(view.elapsed(483000),'已处理 8 分钟 3 秒'));
