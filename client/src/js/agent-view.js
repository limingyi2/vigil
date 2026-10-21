(function(root) {
  'use strict';
  const esc=s=>String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // Defence in depth: the Brain already redacts before it stores a diagnostic,
  // but a tool error can echo an argument back and the argument is sometimes a
  // bare key with no `key=` prefix to match on.
  const SECRET_SHAPES=[/\b(?:sk|rk|pk|gh[pousr]|xox[baprs]|AKIA|LTAI)[-_A-Za-z0-9]{8,}/g,
    /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi];
  const redact=value=>{
    if(Array.isArray(value))return value.slice(0,128).map(redact);
    if(value && typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,v])=>
      [key,/(api.?key|token|password|secret|credential)/i.test(key)?'[已隐藏]':redact(v)]));
    let text=String(value ?? '').replace(/((?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*["']?)(?:Bearer\s+)?[^\s,;"']+/gi,'$1[已隐藏]');
    for(const shape of SECRET_SHAPES)text=text.replace(shape,'[已隐藏]');
    return text.slice(0,12000);
  };
  // One badge per failure class. The class comes from the Brain receipt, not from
  // guessing at the message text: "never dispatched" and "ran but outcome
  // unknown" must not read like an ordinary error, and neither may look like success.
  const ERROR_LABEL={client_offline:'客户端离线',client_busy:'客户端繁忙',
    permission_denied:'权限拒绝',approval_denied:'审批拒绝',tool_failed:'执行失败',
    invalid_args:'参数不合法',unknown:'结果未知',empty_output:'没有输出',cancelled:'已取消'};
  function errorInfo(meta){
    const raw=meta.error;
    if(!raw)return null;
    if(typeof raw==='string')return {category:'',hint:'',detail:raw,receipts:[]};
    if(typeof raw!=='object')return null;
    return {category:String(raw.category||''),hint:String(raw.hint||''),
      detail:String(raw.detail||''),receipts:Array.isArray(raw.receipts)?raw.receipts:[],
      dispatched:raw.dispatched};
  }
  function step(meta, open=false) {
    const name=meta.tool || '工具', input=redact(meta.input || {}), state=meta.state || 'started';
    const info=errorInfo(meta);
    const running=['started','running','in_flight'].includes(state);
    // 'denied' 是被明确拒绝（策略或他点了不允许），不是"执行失败"；
    // 'returned' 但带了 error（如执行完没有任何输出）同样不能显示成完成。
    const failed=['failed','unknown','not_dispatched','not_returned','interrupted','denied'].includes(state)
      || (state==='returned' && !!info);
    const command=/exec|command/.test(name), read=/read/.test(name), edit=/write|edit|patch/.test(name), find=/find|grep|search|list/.test(name);
    const failWord=state==='denied' ? '被拒绝' : '失败';
    const label=command?(running?'正在运行命令':failed?'命令执行'+failWord:'运行了命令')
      :read?(running?'正在读取文件':failed?'文件读取'+failWord:'读取了文件')
      :edit?(running?'正在编辑文件':failed?'文件编辑'+failWord:'编辑了文件')
      :find?(running?'正在搜索文件':failed?'文件搜索'+failWord:'搜索了文件')
      :(running?'正在执行 '+name:failed?name+' 执行'+failWord:'已完成 '+name);
    const preview=command?(input.cmd || input.command || ''):(input.path || input.pattern || '');
    const key=encodeURIComponent(String(meta.run_id || '')+':'+String(meta.call_id || meta.step_id || name));
    // The short reason stays in the always-visible summary; the raw diagnostic
    // only expands on demand. A tool that never ran must not read as "done".
    const badge=info ? '<span class="agent-step-badge" data-error="'+esc(info.category||'unknown')+'">'
      + esc(ERROR_LABEL[info.category] || '执行失败')+'</span>' : '';
    const why=info && info.hint ? '<span class="agent-step-why">'+esc(info.hint)+'</span>' : '';
    const diagnostics=info ? [
      ['分类', ERROR_LABEL[info.category] || info.category || '未知'],
      ['是否已派发', info.dispatched===false ? '否，客户端从未收到这条调用'
        : info.dispatched===true ? '是' : '无法确认'],
      ['原因', info.detail],
    ].filter(([,v])=>v!==undefined && v!=='') : [];
    const parts=[];
    if(diagnostics.length)parts.push('<div class="agent-detail-label">诊断</div><pre>'
      +esc(diagnostics.map(([k,v])=>k+'：'+redact(v)).join('\n'))+'</pre>');
    if(info && info.receipts.length)parts.push('<div class="agent-detail-label">执行凭据</div><pre>'
      +esc(info.receipts.map(r=>[r.state,r.reason,r.detail].filter(Boolean).join(' · ')).join('\n'))+'</pre>');
    parts.push(Object.entries({参数:input,结果:meta.output,
      错误:info ? undefined : meta.error}).filter(([,v])=>v!==undefined && v!=='')
      .map(([title,v])=>'<div class="agent-detail-label">'+title+'</div><pre>'+esc(typeof v==='object'?JSON.stringify(redact(v),null,2):redact(v))+'</pre>').join(''));
    const detail=parts.join('');
    return '<details class="agent-step" data-step-key="'+esc(key)+'" data-state="'+esc(state)+'" data-error="'+esc(info?.category || '')+'"'+(open?' open':'')+'>'+
      '<summary><svg class="i xs agent-step-icon" aria-hidden="true"><use href="#i-'+(command?'term':edit?'book':'folder')+'"/></svg><span>'+esc(label)+'</span>'+badge+why+
      (preview?'<code class="agent-step-preview">'+esc(String(preview).slice(0,160))+'</code>':'')+'</summary>'+detail+'</details>';
  }
  function elapsed(ms) {
    const seconds=Math.max(0,Math.floor(ms/1000));
    return '已处理 '+(seconds>=60?Math.floor(seconds/60)+' 分钟 ':'')+(seconds%60)+' 秒';
  }
  const api={step,elapsed,redact};root.VigilAgentView=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
