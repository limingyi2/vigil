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
  // Tool categories are decided by the **real tool identity**, not by a loose
  // keyword match. The old regex (/find|grep|search|list/) could not tell the
  // web `search` tool from `local_file_grep`, so a live web search was
  // labelled "搜索文件" — the user was told the wrong thing happened.
  // Order matters: the first matching rule wins, so specific names come first.
  const TOOL_RULES=[
    // 联网检索
    {cat:'web',icon:'i-search',match:/^web_?search$|^search$|^websearch$|^browser_search$|^fetch_url$|^web_fetch$|^open_url$/},
    // 能力中心（查询 / 调用）
    {cat:'capability',icon:'i-plug',match:/^capability_(status|call|invoke)$/},
    // Codex / Agent 工作室
    {cat:'codex',icon:'i-puzzle',match:/^codex[_:]|^agent_?center|^dynamic_tool_call$/},
    // 视频工作台
    {cat:'video',icon:'i-video',match:/video_window|^video_|^window_(attach|detach|focus|show|hide)/},
    // 命令执行
    {cat:'exec',icon:'i-term',match:/^local_exec$|^exec$|^command$|^shell$/},
    // 文件读取
    {cat:'read',icon:'i-book',match:/^local_file_read$|^read_file$|^file_read$|^cat$/},
    // 文件编辑（写入 / 补丁 / 移动 / 删除）
    {cat:'edit',icon:'i-book',match:/^(local_)?(file_)?(write|append|edit|patch|delete|mkdir|move|copy)(_|$)|^fs_/},
    // 本地文件查询（在允许目录里按名字/内容找）
    {cat:'find',icon:'i-folder',match:/^local_file_(find|grep|list|recent)$|^(find|grep|list)_files?$|^file_(find|grep|list)$|^glob$/},
    // 本机状态 / 进程 / 剪贴板 / 截图
    {cat:'system',icon:'i-activity',match:/^local_(system_info|process_list|process_kill|active_window|clipboard_read|clipboard_write|screenshot)$/},
    // 动态技能（包名__工具名）
    {cat:'skill',icon:'i-puzzle',match:/__/},
  ];
  function classify(name){
    const n=String(name||'');
    for(const rule of TOOL_RULES) if(rule.match.test(n)) return rule;
    return {cat:'other',icon:'i-activity'};
  }
  // Human label per category, per state. The category decides the wording;
  // the state decides whether it is running, done, refused or unknown.
  const CAT_LABEL={
    web:{run:'正在联网搜索',done:'联网搜索了',fail:'联网搜索'+'失败',denied:'联网搜索被拒绝'},
    capability:{run:'正在调用能力',done:'调用了能力',fail:'能力调用失败',denied:'能力调用被拒绝'},
    codex:{run:'正在操作 Agent 工作室',done:'操作了 Agent 工作室',fail:'Agent 工作室操作失败',denied:'Agent 工作室操作被拒绝'},
    video:{run:'正在操作视频窗口',done:'操作了视频窗口',fail:'视频窗口操作失败',denied:'视频窗口操作被拒绝'},
    exec:{run:'正在运行命令',done:'运行了命令',fail:'命令执行失败',denied:'命令执行被拒绝'},
    read:{run:'正在读取文件',done:'读取了文件',fail:'文件读取失败',denied:'文件读取被拒绝'},
    edit:{run:'正在编辑文件',done:'编辑了文件',fail:'文件编辑失败',denied:'文件编辑被拒绝'},
    find:{run:'正在搜索文件',done:'搜索了文件',fail:'文件搜索失败',denied:'文件搜索被拒绝'},
    system:{run:'正在查询本机状态',done:'查询了本机状态',fail:'本机状态查询失败',denied:'本机状态查询被拒绝'},
    skill:{run:'正在执行技能',done:'执行了技能',fail:'技能执行失败',denied:'技能执行被拒绝'},
    other:{run:'正在执行 ',done:'已完成 ',fail:'执行失败',denied:'执行被拒绝'},
  };
  function toolProgress(event={}){
    const state=String(event.state||'');
    const summaries={
      started:'正在执行工具操作', running:'正在执行工具操作', in_flight:'正在执行工具操作',
      awaiting_authorization:'等待你批准本次操作', denied:'操作已拒绝，未执行',
      failed:'工具执行失败', not_dispatched:'工具未派发，操作未执行',
      unknown:'执行结果尚未确认，需要核对',
      not_returned:'尚未收到执行结果，需要核对',
      interrupted:'工具执行中断，实际结果需要核对',
    };
    let summary=summaries[state];
    if(state==='returned')summary=event.error?'工具返回了错误':'工具已返回结果，正在继续';
    return {summary:summary||'工具状态未确认',state:state||'unknown'};
  }
  function errorInfo(meta){
    const raw=meta.error;
    if(!raw)return null;
    if(typeof raw==='string')return {category:'',hint:'',detail:raw,receipts:[]};
    if(typeof raw!=='object')return null;
    return {category:String(raw.category||''),hint:String(raw.hint||''),
      detail:String(raw.detail||''),receipts:Array.isArray(raw.receipts)?raw.receipts:[],
      dispatched:raw.dispatched};
  }
  // 能力审批卡片：等待授权的操作不是失败也不是成功，它要用户拍板。
  // 展示操作目标/类型/实际参数 + 确认/拒绝；执行证据来自 Brain 的真实
  // 回执（approve 响应或 /api/capabilities/requests 的请求状态），不猜。
  function approvalParams(a){
    const params=a.args && typeof a.args==='object' ? a.args : null;
    if(!params || !Object.keys(params).length)return '';
    const rows=Object.entries(params).map(([k,v])=>{
      const text=String(redact(v));
      return '<dt>'+esc(k)+'</dt><dd>'+esc(text.length>160?text.slice(0,160)+'…[截断]':text)+'</dd>';
    }).join('');
    return '<dl class="cap-approval-facts cap-approval-params">'+rows+'</dl>';
  }
  function approvalCard(meta){
    const a=meta.approval;
    if(!a || !a.request_id)return '';
    const rid=String(a.request_id);
    const state=String(a.state || 'pending');
    const target=a.target || a.capability || '';
    const op=a.operation || '';
    const head='<div class="cap-approval" data-cap-request-id="'+esc(rid)+'">'
      +'<div class="cap-approval-title">需要你的授权</div>'
      +'<dl class="cap-approval-facts">'
      +'<dt>操作</dt><dd><code>'+esc(a.capability||'')+'.'+esc(op)+'</code></dd>'
      +(target?'<dt>目标</dt><dd><code>'+esc(target)+'</code></dd>':'')
      +'</dl>'
      +approvalParams(a);
    if(state==='denied' || a.denied){
      return head+'<div class="cap-approval-state cap-denied">已拒绝，未执行</div></div>';
    }
    if(state==='stale' || state==='missing'){
      // Brain 重启后内存中的审批请求已丢失：不能留下能点的确认按钮。
      return head+'<div class="cap-approval-state cap-stale">审批已失效，请重新发起</div></div>';
    }
    if(state==='completed'){
      const r=a.result || {};
      const st=String(r.status || '');
      if(st==='unknown'){
        return head+'<div class="cap-approval-state cap-unknown">已执行，但结果未知；'
          +'在确认实际生效情况前，不会重试这个操作</div></div>';
      }
      const ok=!!r.ok;
      const detail=JSON.stringify(redact(r)).slice(0,400);
      return head+'<div class="cap-approval-state '+(ok?'cap-ok':'cap-fail')+'">'
        +(ok?'已确认并执行成功':'已确认，但执行未成功')
        +(detail?'<pre>'+esc(detail)+'</pre>':'')+'</div></div>';
    }
    if(state==='executing' || state==='approved'){
      // approved 是历史残留值：审批已受理、正在执行 —— 不能再显示按钮。
      return head+'<div class="cap-approval-state cap-running">正在执行…</div></div>';
    }
    // pending：只有真正等待用户拍板时才出现按钮。
    return head+'<div class="cap-approval-actions">'
      +'<button type="button" class="cap-approve" data-cap-action="approve" data-cap-request="'+esc(rid)+'">确认执行</button>'
      +'<button type="button" class="cap-deny" data-cap-action="deny" data-cap-request="'+esc(rid)+'">拒绝</button>'
      +'<span class="cap-approval-note">执行前不会产生任何更改；拒绝后 Agent 会如实说明</span>'
      +'</div></div>';
  }
  function step(meta, open=false) {
    const name=meta.tool || '工具', input=redact(meta.input || {}), state=meta.state || 'started';
    const info=errorInfo(meta);
    const running=['started','running','in_flight'].includes(state);
    // 'denied' 是被明确拒绝（策略或他点了不允许），不是"执行失败"；
    // 'returned' 但带了 error（如执行完没有任何输出）同样不能显示成完成。
    const failed=['failed','unknown','not_dispatched','not_returned','interrupted','denied'].includes(state)
      || (state==='returned' && !!info);
    const rule=classify(name), cat=rule.cat;
    const purpose=meta.purpose || (cat==='exec' ? '执行本机命令（具体目的未记录）'
      : '执行工具 '+name+'（具体目的未记录）');
    const purposeText=String(redact(purpose));
    const purposeFull=purposeText.length>320 ? purposeText.slice(0,319)+'…' : purposeText;
    const purposeBrief=Array.from(purposeFull).length>76
      ? Array.from(purposeFull).slice(0,75).join('')+'…' : purposeFull;
    // The category decides the wording; the state decides which variant.
    // An unknown outcome reads as unknown — never as a plain success.
    const unknownOutcome=state==='unknown'||state==='not_returned'||state==='interrupted';
    const awaiting=state==='awaiting_authorization' && !!(meta.approval && meta.approval.request_id);
    const words=CAT_LABEL[cat]||CAT_LABEL.other;
    let label;
    if(awaiting)label='等待你的授权';
    else if(state==='denied')label=words.denied;
    else if(unknownOutcome)label=words.fail.replace(/失败$/,'结果未知');
    else if(running)label=words.run;
    else if(failed)label=words.fail;
    else label=words.done+(cat==='other'?' '+name:'');
    // The preview must show what was actually asked for. Web search shows the
    // query, file tools show the path/pattern, commands show the command.
    const preview=cat==='exec'?(input.cmd||input.command||'')
      :cat==='web'?(input.query||input.q||input.url||'')
      :cat==='capability'?(input.operation||input.capability||'')
      :cat==='video'?(input.action||input.operation||'')
      :(input.path||input.pattern||input.name||input.file||'');
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
    if(awaiting)parts.push(approvalCard(meta));
    if(diagnostics.length)parts.push('<div class="agent-detail-label">诊断</div><pre>'
      +esc(diagnostics.map(([k,v])=>k+'：'+redact(v)).join('\n'))+'</pre>');
    if(info && info.receipts.length)parts.push('<div class="agent-detail-label">执行凭据</div><pre>'
      +esc(info.receipts.map(r=>[r.state,r.reason,r.detail].filter(Boolean).join(' · ')).join('\n'))+'</pre>');
    parts.push(Object.entries({执行内容:input,执行结果:meta.output,
      错误:info ? undefined : meta.error}).filter(([,v])=>v!==undefined && v!=='')
      .map(([title,v])=>'<div class="agent-detail-label">'+title+'</div><pre>'+esc(typeof v==='object'?JSON.stringify(redact(v),null,2):redact(v))+'</pre>').join(''));
    const detail=parts.join('');
    // 等待授权的步骤必须一眼看见：默认展开，不藏在折叠区里。
    return '<details class="agent-step" data-step-key="'+esc(key)+'" data-state="'+esc(state)+'" data-error="'+esc(info?.category || '')+'" data-tool-cat="'+esc(cat)+'"'+(open || awaiting ?' open':'')+'>'+
      '<summary><svg class="i xs agent-step-icon" aria-hidden="true"><use href="#'+(rule.icon||'i-activity')+'"/></svg><span>'+esc(label)+'</span>'+badge+why+
      (preview?'<code class="agent-step-preview">'+esc(String(preview).slice(0,160))+'</code>':'')+
      '<span class="agent-step-summary-purpose" title="操作目的：'+esc(purposeFull)+'"><b>操作目的</b>：'+esc(purposeBrief)+'</span></summary>'+
      '<div class="agent-step-purpose"><b>操作目的</b>：'+esc(purposeFull)+'</div>'+detail+'</details>';
  }
  function elapsed(ms) {
    const seconds=Math.max(0,Math.floor(ms/1000));
    return '已处理 '+(seconds>=60?Math.floor(seconds/60)+' 分钟 ':'')+(seconds%60)+' 秒';
  }
  function codingSummary(coding) {
    if (!coding || typeof coding !== 'object') return '';
    const plan=coding.plan || {}, steps=Array.isArray(plan.steps)?plan.steps:[];
    const tests=Array.isArray(coding.tests)?coding.tests:[];
    const todoSteps=Array.isArray(coding.workflow_steps)?coding.workflow_steps:[];
    const todoProgress=coding.workflow_progress||{};
    const files=Array.isArray(coding.changed_files)?coding.changed_files:
      (Array.isArray(coding.files)?coding.files.filter(Boolean):[]);
    const phases=steps.map(s=>'<li><span class="ac-state ac-'+esc(s.status||'pending')+'">'+esc(s.status||'pending')+'</span> '+esc(s.title||s.id||'step')+'</li>').join('');
    const testRows=tests.map(t=>'<li><span class="ac-state ac-'+(t.passed?'succeeded':'failed')+'">'+(t.passed?'通过':'失败')+'</span> <code>'+esc(t.command||'')+'</code> · exit '+esc(t.exit_code==null?'—':t.exit_code)+'</li>').join('');
    const todoRows=todoSteps.map(s=>'<li><span class="ac-state ac-'+esc(s.status||'pending')+'">'+esc(s.status||'pending')+'</span> '+esc(s.title||s.step_id||'step')+'</li>').join('');
    const todoStatus=coding.workflow_sync_error?'sync_unavailable':
      (coding.workflow_requires_review?'needs_review':(coding.workflow_status||''));
    return '<section class="agent-coding-card"><header><strong>Coding Agent</strong><span class="ac-state ac-'+esc(coding.phase||coding.status||'')+'">'+esc(coding.phase||coding.status||'')+'</span></header>'+
      (todoSteps.length?'<div class="agent-coding-section"><b>任务进度 · '+esc(todoProgress.completed||0)+' / '+esc(todoProgress.total||todoSteps.length)+' · '+esc(todoStatus)+'</b><ol>'+todoRows+'</ol></div>':'')+
      (steps.length?'<div class="agent-coding-section"><b>计划</b><ol>'+phases+'</ol></div>':'')+
      (files.length?'<div class="agent-coding-section"><b>修改文件</b><div class="agent-coding-files">'+files.map(f=>'<code>'+esc(f)+'</code>').join('')+'</div></div>':'')+
      (testRows?'<div class="agent-coding-section"><b>验证</b><ul>'+testRows+'</ul></div>':'')+
      '<div class="agent-coding-meta">自动修复 '+esc(coding.repair_count||0)+' 次</div>'+
      (coding.diff?'<details><summary>查看 Diff</summary><pre>'+esc(coding.diff)+'</pre></details>':'')+'</section>';
  }
  // 历史回填的纯决策函数：给定卡片集合与服务端请求映射，决定每张卡片
  // 的处置。服务端查不到的 request_id（Brain 重启后内存请求丢失）按
  // stale 处置 —— 失效卡片不显示确认按钮；已处置过的跳过。
  function approvalResolutions(cards, byId){
    if (!byId) return [];   // 查询失败：不产生任何处置，调用方保持卡片原样
    return (cards||[]).map((card)=>{
      const rid=card.dataset && card.dataset.capRequestId;
      if(card.dataset && card.dataset.capDecorated)return {card,action:'skip'};
      const req=rid ? byId[rid] : undefined;
      if(!req)return {card,action:'stale'};
      return {card,action:req.state,request:req};
    });
  }
  // 历史审批查询的纯决策核心(可 Node 测试):
  // fetchMap(withSession) 由调用方提供,可能 reject(断网/超时)、返回
  // {ok:false}(HTTP 401/403/500)或格式异常——这些**一律是查询失败**,
  // 返回 null,调用方保持卡片原样;绝不能当成"空审批列表"把缺失的
  // request_id 误判成 stale。只有成功拿到有效列表(且必要的回退查询也
  // 成功)时,才返回映射交给 approvalResolutions 做缺失 ID 的 stale 判定。
  async function approvalHistory(cards, fetchMap) {
    const toMap = (r) => {
      if (!r || r.ok !== true || !Array.isArray(r.requests)) {
        throw new Error('approval_history_invalid: ' +
          String((r && (r._err || r.err)) || '格式异常').slice(0, 80));
      }
      const map = {};
      for (const item of r.requests) {
        if (item && item.request_id) map[String(item.request_id)] = item;
      }
      return map;
    };
    let sessionMap;
    try {
      sessionMap = toMap(await fetchMap(true));
    } catch (_) {
      return null;                       // 查询失败:不产生任何处置
    }
    const needsFallback = (cards || []).some((card) =>
      card.dataset && !card.dataset.capDecorated && card.dataset.capRequestId
      && !sessionMap[card.dataset.capRequestId]);
    if (!needsFallback) return sessionMap;
    try {
      const all = toMap(await fetchMap(false));
      return Object.assign({}, sessionMap, all);
    } catch (_) {
      return null;                       // 回退查询失败:同样不判定 stale
    }
  }
  const api={step,toolProgress,elapsed,redact,codingSummary,approvalCard,approvalResolutions,approvalHistory};root.VigilAgentView=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
