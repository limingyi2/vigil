(function (global) {
  'use strict';

  async function renderVideoWorkbench(ctx) {
    ctx.topbar.innerHTML = '<div class="top-title">视频工作台</div><div class="top-subtitle">本地播放与视频问答</div>';
    ctx.view.innerHTML = `
      <section class="video-workbench">
        <div class="video-media-frame" id="videoMediaPane">
          <video id="videoPlayer" controls playsinline preload="metadata" aria-label="本地视频播放器"></video>
          <span class="video-media-empty" id="videoMediaEmpty">点「选择视频」打开本地文件，或直接把视频拖进来</span>
        </div>
        <div class="video-tools" id="videoPlayerTools">
          <input type="file" id="videoFileInput" accept="video/*,.mkv,.mov,.m4v,.avi" hidden>
          <button class="btn fill" id="videoOpenFile">选择视频</button>
          <label class="video-speed-label">倍速<select id="videoPlaybackRate" aria-label="播放速度">
            <option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1" selected>1×</option>
            <option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option>
          </select></label>
          <span class="video-status" id="videoPlayerStatus" role="status">视频只在本机播放，不会自动发送给模型。</span>
        </div>
        <section class="video-qa" aria-label="视频问答">
          <div class="video-qa-log" id="videoQaLog" role="log" aria-live="polite">
            <p class="video-qa-hint">打开视频后，在下面用一句话提问。回答会带上它实际看过的画面时间点，点一下就能跳过去核对。</p>
          </div>
          <form class="video-qa-form" id="videoQaForm">
            <input id="videoQaInput" type="text" autocomplete="off" placeholder="先打开一个视频" aria-label="问题" disabled>
            <button class="btn fill" id="videoQaSend" type="submit" disabled>提问</button>
            <button class="btn oline" id="videoQaCancel" type="button" hidden>停止</button>
          </form>
          <div class="video-qa-status" id="videoQaStatus" role="status">正在检查问答服务…</div>
        </section>
      </section>`;

    const $ = id => ctx.view.querySelector('#' + id);
    const player = $('videoPlayer');
    const playerStatus = $('videoPlayerStatus');
    const fileInput = $('videoFileInput');
    const pane = $('videoMediaPane');
    const log = $('videoQaLog');
    const form = $('videoQaForm');
    const question = $('videoQaInput');
    const sendButton = $('videoQaSend');
    const cancelButton = $('videoQaCancel');
    const qaStatus = $('videoQaStatus');

    let current = null;         // { kind:'ticket'|'blob', url, path, name }
    let playerActive = true;
    let service = null;         // { base, provider, isFake }
    let sessionReady = false;
    let asking = null;          // 当前提问的 AbortController
    let watchTimer = null;      // 提问过程的秒表，服务端不推心跳时也能看出还在跑

    const bridge = () => (global.vigil && global.vigil.video) || null;

    const releaseMedia = () => {
      player.pause();
      player.removeAttribute('src');
      player.load();
      // 只有自己造出来的 blob 地址需要回收；票据地址由主进程管生命周期。
      if (current && current.kind === 'blob') global.URL.revokeObjectURL(current.url);
      current = null;
    };

    // ---- 播放器 ----
    const showMedia = (media) => {
      releaseMedia();
      current = media;
      player.src = media.url;
      player.playbackRate = Number($('videoPlaybackRate').value || 1);
      $('videoMediaEmpty').hidden = true;
      player.load();
      playerStatus.textContent = media.name +
        (media.note ? '（' + media.note + '）' : '') + ' — 点播放开始。';
      resetLog();
      // 分析服务读的是磁盘路径，不是地址；换视频等于换会话。
      startSession(media.path, media.name);
    };

    // 主进程弹系统对话框，拿回真实路径 + 只放行该文件的播放地址。
    const pickVideo = async () => {
      const api = bridge();
      if (!api) { fileInput.click(); return; }   // 预览/旧版环境退回原生 input
      let info;
      try { info = await api.pick(); } catch (error) {
        playerStatus.textContent = '打开选择框失败：' + error.message;
        return;
      }
      if (!playerActive || !info || info.canceled) return;
      if (!info.ok) { playerStatus.textContent = '打不开这个视频：' + (info.err || '未知原因'); return; }
      showMedia({ kind: 'ticket', url: info.url, path: info.path, name: info.name, note: info.note });
    };

    // input 兜底与拖拽都走这里：能拿到路径就让主进程发播放地址，否则退到本机临时地址。
    const adoptFile = async (file) => {
      if (!playerActive || !file) return;
      if (!file.size) { playerStatus.textContent = '这个文件是空的。'; return; }
      const api = bridge();
      const filePath = file.path || '';
      if (api && filePath) {
        let info;
        try { info = await api.share(filePath); } catch (error) { info = { ok: false, err: error.message }; }
        if (!playerActive) return;
        if (info && info.ok) {
          showMedia({ kind: 'ticket', url: info.url, path: info.path, name: info.name, note: info.note });
          return;
        }
        playerStatus.textContent = ((info && info.err) || '没能登记这个文件') + '，改用临时地址播放（问答需要磁盘路径）。';
      }
      showMedia({ kind: 'blob', url: global.URL.createObjectURL(file), path: filePath, name: file.name });
    };

    $('videoOpenFile').onclick = pickVideo;
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = ''; // 再选同一个文件也要重新触发
      adoptFile(file);
    });
    ['dragenter', 'dragover'].forEach(type => pane.addEventListener(type, (event) => {
      event.preventDefault();
      pane.classList.add('video-drop-ready');
    }));
    ['dragleave', 'dragend'].forEach(type => pane.addEventListener(type, () => {
      pane.classList.remove('video-drop-ready');
    }));
    pane.addEventListener('drop', (event) => {
      event.preventDefault();
      pane.classList.remove('video-drop-ready');
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) adoptFile(file);
    });
    $('videoPlaybackRate').addEventListener('change', () => {
      const rate = Number($('videoPlaybackRate').value);
      if (Number.isFinite(rate) && rate >= 0.5 && rate <= 2) player.playbackRate = rate;
    });
    // 打不开的文件会先换封装或转码，这一步可能几十秒 —— 必须让状态行动起来。
    (() => {
      const api = bridge();
      if (!api || typeof api.onPrepare !== 'function') return;
      api.onPrepare((state) => {
        if (!playerActive || !state || !state.phase) return;
        const say = (text) => { playerStatus.textContent = text; };
        if (state.phase === 'probing') say('正在看这个视频的编码…');
        else if (state.phase === 'remuxing') say(state.message || '正在换封装（不重编码）…');
        else if (state.phase === 'transcoding') say('播放器解不了这个编码，正在转成 H.264：' + (state.percent || 0) + '%（大文件会慢一些）');
        else if (state.phase === 'ready') say('转换完成，马上播放…');
        else if (state.phase === 'error') say('转换失败：' + (state.message || '未知原因'));
      });
    })();

    player.addEventListener('error', () => {
      if (current && playerActive) {
        playerStatus.textContent = '这个视频放不出来：编码或封装不被支持，也可能文件本身损坏了。'
          + '本机找到 ffmpeg 时会自动转码，没转成功的话先转成 MP4（H.264）再打开。';
      }
    });

    // ---- 问答 ----
    const setStatus = (text) => { qaStatus.textContent = text; };
    const setComposability = () => {
      const usable = sessionReady && !asking;
      question.disabled = !sessionReady;
      sendButton.disabled = !usable || !question.value.trim();
      cancelButton.hidden = !asking;
    };

    // 换视频就把上一段的问答清掉：旧答案对应的是另一个文件，留着只会误导。
    function resetLog() {
      sessionReady = false;
      question.value = '';
      question.placeholder = '先打开一个视频';
      setComposability();
      log.textContent = '';
      const hint = document.createElement('p');
      hint.className = 'video-qa-hint';
      hint.textContent = '打开视频后，在下面用一句话提问。回答会带上它实际看过的画面时间点，点一下就能跳过去核对。';
      log.appendChild(hint);
    }

    const appendTurn = (who, text) => {
      const hint = log.querySelector('.video-qa-hint');
      if (hint) hint.remove();
      const block = document.createElement('div');
      block.className = 'video-qa-turn video-qa-' + who;
      const p = document.createElement('p');
      p.textContent = text;
      block.appendChild(p);
      log.appendChild(block);
      log.scrollTop = log.scrollHeight;
      return block;
    };

    // 答案本体只写文本节点：模型输出不可信，不拼 HTML。
    const renderAnswer = (final) => {
      const block = appendTurn('answer', '');
      const text = document.createElement('p');
      text.className = 'video-qa-text';
      text.textContent = final.status === 'answered' ? final.answer : '没有足够的画面证据回答这个问题。';
      block.appendChild(text);
      if (final.status !== 'answered' && final.reasoning_summary) {
        const why = document.createElement('p');
        why.className = 'video-qa-why';
        why.textContent = final.reasoning_summary;
        block.appendChild(why);
      }
      const stamps = Array.isArray(final.evidence_timestamps) ? final.evidence_timestamps : [];
      if (stamps.length) {
        const row = document.createElement('div');
        row.className = 'video-qa-evidence';
        const label = document.createElement('span');
        label.textContent = '看过的画面';
        row.appendChild(label);
        for (const stamp of stamps) {
          const jump = document.createElement('button');
          jump.type = 'button';
          jump.className = 'video-qa-stamp';
          jump.textContent = Number(stamp).toFixed(1) + 's';
          jump.title = '跳到 ' + Number(stamp).toFixed(1) + ' 秒核对';
          jump.addEventListener('click', () => {
            player.currentTime = Number(stamp);
            player.pause();
            player.focus();
          });
          row.appendChild(jump);
        }
        block.appendChild(row);
      }
      const doubts = Array.isArray(final.uncertainties) ? final.uncertainties.filter(Boolean) : [];
      if (doubts.length) {
        const list = document.createElement('ul');
        list.className = 'video-qa-doubts';
        for (const item of doubts) {
          const li = document.createElement('li');
          li.textContent = item;
          list.appendChild(li);
        }
        block.appendChild(list);
      }
      const usage = final.usage || {};
      const bits = [];
      if (Number.isFinite(final.rounds)) bits.push(final.rounds + ' 轮');
      if (Number.isFinite(final.observed_frames)) bits.push(final.observed_frames + ' 帧');
      if (Number.isFinite(final.latency_seconds)) bits.push(Number(final.latency_seconds).toFixed(1) + 's');
      if (Number.isFinite(usage.input_tokens)) bits.push(usage.input_tokens + ' / ' + (usage.output_tokens || 0) + ' tokens');
      if (final.is_fake) bits.push('假 provider：不含真实视觉判断');
      const meta = document.createElement('div');
      meta.className = 'video-qa-meta';
      meta.textContent = bits.join(' · ');
      block.appendChild(meta);
      log.scrollTop = log.scrollHeight;
    };

    const handleEvent = (event, turn) => {
      if (!event || typeof event.type !== 'string') return;
      if (event.type === 'round') {
        setStatus('第 ' + event.payload.round + ' 轮推理…');
      } else if (event.type === 'tool') {
        const tool = event.payload.tool || 'observe';
        setStatus('正在看画面：' + tool);
      } else if (event.type === 'final') {
        renderAnswer(event.payload);
        turn.final = event.payload;
      } else if (event.type === 'error') {
        const message = (event.payload && event.payload.message) || '未知错误';
        appendTurn('error', message);
      }
    };

    // SSE 手动解析：服务端就是 `data: {json}\n\n`，不需要额外依赖。
    const askQuestion = async (text) => {
      const controller = new AbortController();
      asking = controller;
      setComposability();
      const started = Date.now();
      watchTimer = global.setInterval(() => {
        setStatus(qaStatus.dataset.base + ' · ' + Math.round((Date.now() - started) / 1000) + ' 秒');
      }, 1000);
      try {
        const response = await fetch(service.base + '/ask', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ question: text }), signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error('服务返回 HTTP ' + response.status);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let turn = {};
        for (;;) {
          if (!playerActive) { try { reader.cancel(); } catch (e) { /* 页面已卸载 */ } break; }
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          let cut;
          while ((cut = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            const line = frame.split('\n').find(item => item.startsWith('data: '));
            if (!line) continue;
            let parsed = null;
            try { parsed = JSON.parse(line.slice(6)); } catch (e) { continue; }
            handleEvent(parsed, turn);
          }
        }
        if (!turn.final && playerActive) appendTurn('error', '没有收到最终回答，流被中断了。');
      } catch (error) {
        if (error.name === 'AbortError') appendTurn('error', '已取消这次提问。');
        else if (playerActive) appendTurn('error', '提问失败：' + error.message);
      } finally {
        if (watchTimer) { global.clearInterval(watchTimer); watchTimer = null; }
        asking = null;
        if (playerActive) { setStatus(qaStatus.dataset.base || ''); setComposability(); question.focus(); }
      }
    };

    question.addEventListener('input', setComposability);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = question.value.trim();
      if (!text || !sessionReady || asking) return;
      question.value = '';
      setComposability();
      appendTurn('question', text);
      askQuestion(text);
    });
    cancelButton.addEventListener('click', async () => {
      try { await fetch(service.base + '/cancel', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }); } catch (e) { /* 服务可能已退出 */ }
    });

    // 服务由主进程探测/拉起。这里先把它存成 promise：用户可能比它先点「选择视频」，
    // 建会话时等一下即可，不必让按钮先灰着等人。
    const serviceReady = (async () => {
      try {
        const info = await global.vigil.ego.ensure();
        if (!playerActive) return null;
        if (!info || !info.ok) {
          setStatus('问答服务没起来：' + ((info && info.error) || '未知原因') +
            (info && info.log ? '（日志：' + info.log + '）' : ''));
          return null;
        }
        service = { base: info.base, provider: info.provider, isFake: info.isFake };
        qaStatus.dataset.base = info.isFake ? '脚本 provider（不做视觉判断）' : '真实视觉模型 ' + info.provider;
        setStatus(qaStatus.dataset.base + ' · 打开视频后即可提问');
        return service;
      } catch (error) {
        if (playerActive) setStatus('问答服务没起来：' + error.message);
        return null;
      }
    })();

    // 打开视频 = 新会话；索引在服务端按视频内容哈希缓存，换回同一个视频不用重建。
    async function startSession(filePath, name) {
      sessionReady = false;
      setComposability();
      if (!filePath) {
        setStatus('拿不到这个文件的磁盘路径，分析服务需要路径才能读取视频。');
        return;
      }
      setStatus('正在为「' + name + '」建索引，长视频会慢一些…');
      const active = service || await serviceReady;
      if (!playerActive) return;
      if (!active) { setStatus('问答服务没起来，暂时只能播放。'); return; }
      try {
        const response = await fetch(active.base + '/session', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ video: filePath }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        if (!playerActive) return;
        sessionReady = true;
        const label = data.is_fake ? '脚本 provider（不做视觉判断）' : '真实视觉模型';
        qaStatus.dataset.base = label + ' · 已索引 ' + Number(data.frames || 0) + ' 帧 · 视频 ' + Number(data.duration || 0).toFixed(1) + ' 秒';
        setStatus(qaStatus.dataset.base);
        question.placeholder = '问点什么，比如：画面里发生了什么？';
        question.focus();
      } catch (error) {
        if (!playerActive) return;
        setStatus('建会话失败：' + error.message);
      } finally {
        if (playerActive) setComposability();
      }
    }

    ctx.onDispose(() => {
      playerActive = false;
      releaseMedia();
      if (watchTimer) { global.clearInterval(watchTimer); watchTimer = null; }
      if (asking) { try { asking.abort(); } catch (e) { /* already done */ } }
      if (service && sessionReady) {
        // 离开页面就放掉会话，别让服务端占着视频句柄。
        fetch(service.base + '/close', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
          .catch(() => { /* 服务可能已退出 */ });
      }
    });
  }

  global.VigilRenderVideoWorkbench = renderVideoWorkbench;
})(window);
