(function (global) {
  'use strict';

  async function renderVideoWorkbench(ctx) {
    ctx.topbar.innerHTML = '<div class="top-title">视频工作台</div><div class="top-subtitle">本地播放与视频问答</div>';
    ctx.view.innerHTML = `
      <section class="video-workbench">
        <div class="video-media-frame" id="videoMediaPane">
          <video id="videoPlayer" controls playsinline preload="metadata" aria-label="本地视频播放器"></video>
          <span class="video-media-empty" id="videoMediaEmpty">打开本地视频开始播放</span>
        </div>
        <div class="video-tools" id="videoPlayerTools">
          <input type="file" id="videoFileInput" accept="video/*,.mkv,.mov,.m4v,.avi" hidden>
          <button class="btn primary" id="videoOpenFile">打开视频</button>
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
            <button class="btn primary" id="videoQaSend" type="submit" disabled>提问</button>
            <button class="btn" id="videoQaCancel" type="button" hidden>停止</button>
          </form>
          <div class="video-qa-status" id="videoQaStatus" role="status">正在检查问答服务…</div>
        </section>
      </section>`;

    const $ = id => ctx.view.querySelector('#' + id);
    const player = $('videoPlayer');
    const playerStatus = $('videoPlayerStatus');
    const input = $('videoFileInput');
    const log = $('videoQaLog');
    const form = $('videoQaForm');
    const question = $('videoQaInput');
    const sendButton = $('videoQaSend');
    const cancelButton = $('videoQaCancel');
    const qaStatus = $('videoQaStatus');

    let mediaUrl = null;
    let playerActive = true;
    let service = null;         // { base, provider, isFake }
    let sessionReady = false;
    let asking = null;          // 当前提问的 AbortController
    let watchTimer = null;      // 提问过程的秒表，服务端不推心跳时也能看出还在跑

    const releaseMedia = () => {
      player.pause();
      player.removeAttribute('src');
      player.load();
      if (mediaUrl) global.URL.revokeObjectURL(mediaUrl);
      mediaUrl = null;
    };

    // ---- 播放器 ----
    $('videoOpenFile').onclick = () => input.click();
    input.addEventListener('change', () => {
      if (!playerActive) return;
      const file = input.files && input.files[0];
      input.value = ''; // Selecting the same video again must still fire change.
      if (!file) return;
      if (!file.size || (!String(file.type || '').startsWith('video/') &&
          !/\.(mp4|webm|mov|mkv|m4v|ogv|ogg|avi)$/i.test(file.name))) {
        playerStatus.textContent = '请选择非空的视频文件。'; return;
      }
      try {
        releaseMedia();
        mediaUrl = global.URL.createObjectURL(file);
        player.src = mediaUrl;
        player.playbackRate = Number($('videoPlaybackRate').value || 1);
        $('videoMediaEmpty').hidden = true;
        playerStatus.textContent = file.name + ' — 点击播放开始；若无法解码，请尝试 MP4（H.264）或 WebM。';
        player.load();
      } catch (error) { playerStatus.textContent = '打开视频失败：' + error.message; }
      // 分析服务读的是磁盘路径，不是 blob；Electron 28 的 File 还带 path。
      startSession(file.path, file.name);
    });
    $('videoPlaybackRate').addEventListener('change', () => {
      const rate = Number($('videoPlaybackRate').value);
      if (Number.isFinite(rate) && rate >= 0.5 && rate <= 2) player.playbackRate = rate;
    });
    player.addEventListener('error', () => {
      if (mediaUrl && playerActive) playerStatus.textContent = '此视频无法播放，可能是编码不受支持或文件已损坏。请尝试 MP4（H.264）或 WebM。';
    });

    // ---- 问答 ----
    const setStatus = (text) => { qaStatus.textContent = text; };
    const setComposability = () => {
      const usable = sessionReady && !asking;
      question.disabled = !sessionReady;
      sendButton.disabled = !usable || !question.value.trim();
      cancelButton.hidden = !asking;
    };

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

    // 打开视频 = 新会话；索引在服务端按视频内容哈希缓存，换回同一个视频不用重建。
    async function startSession(filePath, name) {
      sessionReady = false;
      setComposability();
      if (!filePath) {
        setStatus('拿不到这个文件的磁盘路径，分析服务需要路径才能读取视频。');
        return;
      }
      if (!service) {
        setStatus('问答服务还没连上，等它起来再打开视频。');
        return;
      }
      setStatus('正在为「' + name + '」建索引，长视频会慢一些…');
      try {
        const response = await fetch(service.base + '/session', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ video: filePath }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
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

    // 服务由主进程探测/拉起；拿不到就明说，不静默禁用输入框。
    (async () => {
      try {
        const info = await global.vigil.ego.ensure();
        if (!playerActive) return;
        if (!info || !info.ok) {
          setStatus('问答服务没起来：' + ((info && info.error) || '未知原因') +
            (info && info.log ? '（日志：' + info.log + '）' : ''));
          return;
        }
        service = { base: info.base, provider: info.provider, isFake: info.isFake };
        qaStatus.dataset.base = info.isFake ? '脚本 provider（不做视觉判断）' : '真实视觉模型 ' + info.provider;
        setStatus(qaStatus.dataset.base + ' · 打开视频后即可提问');
      } catch (error) {
        if (playerActive) setStatus('问答服务没起来：' + error.message);
      }
    })();

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
