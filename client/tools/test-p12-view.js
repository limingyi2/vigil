const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../src/js/agent-view');
test('tool evidence updates a stable identity with command and collapsed real details',()=>{
  const event={tool:'local_exec',run_id:'run1',call_id:'call1',state:'started',input:{cmd:'python -m pytest -q'}};
  const start=view.step({...event,purpose:'为检查项目测试而运行 pytest'}),end=view.step({...event,purpose:'为检查项目测试而运行 pytest',state:'returned',output:'3 passed'});
  assert.match(start,/正在运行命令/);assert.match(start,/python -m pytest -q/);
  assert.match(start,/操作目的/);assert.match(start,/为检查项目测试而运行 pytest/);
  assert.match(start,/执行内容/);assert.doesNotMatch(start,/执行结果/);
  assert.match(end,/运行了命令/);assert.match(end,/3 passed/);
  assert.match(end,/执行结果/);
  assert.equal(start.match(/data-step-key="([^"]+)"/)[1],end.match(/data-step-key="([^"]+)"/)[1]);
  assert.doesNotMatch(start,/<details[^>]+ open/);
});
test('historical tool steps without a purpose use an honest fallback',()=>{
  assert.match(view.step({tool:'local_exec',state:'started',input:{cmd:'dir'}}),/具体目的未记录/);
});
test('command errors stay errors and parameters/results are escaped and redacted',()=>{
  const html=view.step({tool:'local_exec',state:'failed',input:{cmd:'API_KEY=secret1 command',token:'secret2'},output:'Authorization: Bearer secret3',error:'<script>failure</script>'});
  assert.match(html,/命令执行失败/);assert.doesNotMatch(html,/secret[123]|<script>/);
  assert.match(html,/&lt;script&gt;/);
});
test('duration is derived from actual elapsed time',()=>assert.equal(view.elapsed(483000),'已处理 8 分钟 3 秒'));
