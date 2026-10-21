const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../src/js/agent-view');
test('tool evidence updates a stable identity with command and collapsed real details',()=>{
  const event={tool:'local_exec',run_id:'run1',call_id:'call1',state:'started',input:{cmd:'python -m pytest -q'}};
  const start=view.step(event),end=view.step({...event,state:'returned',output:'3 passed'});
  assert.match(start,/正在运行命令/);assert.match(start,/python -m pytest -q/);
  assert.match(end,/运行了命令/);assert.match(end,/3 passed/);
  assert.equal(start.match(/data-step-key="([^"]+)"/)[1],end.match(/data-step-key="([^"]+)"/)[1]);
  assert.doesNotMatch(start,/<details[^>]+ open/);
});
test('command errors stay errors and parameters/results are escaped and redacted',()=>{
  const html=view.step({tool:'local_exec',state:'failed',input:{cmd:'API_KEY=secret1 command',token:'secret2'},output:'Authorization: Bearer secret3',error:'<script>failure</script>'});
  assert.match(html,/命令执行失败/);assert.doesNotMatch(html,/secret[123]|<script>/);
  assert.match(html,/&lt;script&gt;/);
});
test('duration is derived from actual elapsed time',()=>assert.equal(view.elapsed(483000),'已处理 8 分钟 3 秒'));
