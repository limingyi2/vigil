(function(root) {
  'use strict';
  const esc=s=>String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const redact=value=>{
    if(Array.isArray(value))return value.slice(0,128).map(redact);
    if(value && typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,v])=>
      [key,/(api.?key|token|password|secret|credential)/i.test(key)?'[已隐藏]':redact(v)]));
    return String(value ?? '').replace(/((?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*["']?)(?:Bearer\s+)?[^\s,;"']+/gi,'$1[已隐藏]')
      .replace(/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi,'Bearer [已隐藏]').slice(0,12000);
  };
  function step(meta, open=false) {
    const name=meta.tool || '工具', input=redact(meta.input || {}), state=meta.state || 'started';
    const running=['started','running','in_flight'].includes(state);
    const failed=['failed','unknown','not_dispatched','interrupted'].includes(state);
    const command=/exec|command/.test(name), read=/read/.test(name), edit=/write|edit|patch/.test(name), find=/find|grep|search|list/.test(name);
    const label=command?(running?'正在运行命令':failed?'命令执行失败':'运行了命令')
      :read?(running?'正在读取文件':failed?'文件读取失败':'读取了文件')
      :edit?(running?'正在编辑文件':failed?'文件编辑失败':'编辑了文件')
      :find?(running?'正在搜索文件':failed?'文件搜索失败':'搜索了文件')
      :(running?'正在执行 '+name:failed?name+' 执行失败':'已完成 '+name);
    const preview=command?(input.cmd || input.command || ''):(input.path || input.pattern || '');
    const key=encodeURIComponent(String(meta.run_id || '')+':'+String(meta.call_id || meta.step_id || name));
    const detail=Object.entries({参数:input,结果:meta.output,错误:meta.error}).filter(([,v])=>v!==undefined && v!=='')
      .map(([title,v])=>'<div class="agent-detail-label">'+title+'</div><pre>'+esc(typeof v==='object'?JSON.stringify(redact(v),null,2):redact(v))+'</pre>').join('');
    return '<details class="agent-step" data-step-key="'+esc(key)+'" data-state="'+esc(state)+'"'+(open?' open':'')+'>'+
      '<summary><svg class="i xs agent-step-icon" aria-hidden="true"><use href="#i-'+(command?'term':edit?'book':'folder')+'"/></svg><span>'+esc(label)+'</span>'+
      (preview?'<code class="agent-step-preview">'+esc(String(preview).slice(0,160))+'</code>':'')+'</summary>'+detail+'</details>';
  }
  function elapsed(ms) {
    const seconds=Math.max(0,Math.floor(ms/1000));
    return '已处理 '+(seconds>=60?Math.floor(seconds/60)+' 分钟 ':'')+(seconds%60)+' 秒';
  }
  const api={step,elapsed,redact};root.VigilAgentView=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
