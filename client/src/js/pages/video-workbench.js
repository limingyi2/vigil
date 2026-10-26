(function (global) {
  'use strict';
  const { get, post, pick } = API;

  async function renderVideoWorkbench(ctx) {
    ctx.topbar.innerHTML = '<div class="top-title">视频工作台</div><div class="top-subtitle">本地视频播放</div>';
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
        <section class="video-analysis" aria-label="视频分析模型">
          <div class="video-analysis-text">
            <span class="video-analysis-eyebrow">后续独立接入</span>
            <b>独立视频分析模型</b>
            <p>用于片段描述与回看取证，不使用向量模型，失败直接报错、不切换模型。保存设置不会调用模型，也不会自动发送画面。</p>
          </div>
          <div class="video-analysis-form">
            <select id="videoAnalysisModel" aria-label="视频分析模型"><option value="">正在读取…</option></select>
            <button class="btn" id="videoAnalysisSave" disabled>保存分析模型</button>
            <span class="video-status" id="videoAnalysisStatus" role="status"></span>
          </div>
        </section>
      </section>`;

    const $ = id => ctx.view.querySelector('#' + id);
    const player = $('videoPlayer');
    const playerStatus = $('videoPlayerStatus');
    const input = $('videoFileInput');
    let mediaUrl = null;
    let playerActive = true;
    const releaseMedia = () => {
      player.pause();
      player.removeAttribute('src');
      player.load();
      if (mediaUrl) global.URL.revokeObjectURL(mediaUrl);
      mediaUrl = null;
    };
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
    });
    $('videoPlaybackRate').addEventListener('change', () => {
      const rate = Number($('videoPlaybackRate').value);
      if (Number.isFinite(rate) && rate >= 0.5 && rate <= 2) player.playbackRate = rate;
    });
    player.addEventListener('error', () => {
      if (mediaUrl && playerActive) playerStatus.textContent = '此视频无法播放，可能是编码不受支持或文件已损坏。请尝试 MP4（H.264）或 WebM。';
    });
    ctx.onDispose(() => { playerActive = false; releaseMedia(); });

    // 分析模型设置：只读写这一份配置，不触发任何模型调用。
    const modelSelect = $('videoAnalysisModel');
    const modelSave = $('videoAnalysisSave');
    const modelStatus = $('videoAnalysisStatus');
    let modelsAvailable = false;
    try {
      const settings = pick(await get('/api/video-analysis/settings'));
      if (!ctx.view.isConnected) return;
      if (!settings.ok) {
        modelSelect.replaceChildren(new Option('分析模型设置不可用', ''));
        modelStatus.textContent = '读取失败：' + (settings._err || settings.err || '请检查本地内核版本');
      } else {
        const models = Array.isArray(settings.models) ? settings.models : [];
        modelSelect.replaceChildren(...(models.length
          ? models.map(item => new Option(String(item.name || item.id) + ' · ' + String(item.provider || ''), String(item.id)))
          : [new Option('没有可用的视觉模型', '')]));
        if (settings.model_id) modelSelect.value = String(settings.model_id);
        modelsAvailable = models.length > 0;
        modelSelect.disabled = !modelsAvailable;
        modelSave.disabled = !modelsAvailable;
        if (!modelsAvailable) modelStatus.textContent = '可在“模型管理”里添加支持图片输入的模型。';
      }
    } catch (error) {
      if (ctx.view.isConnected) modelStatus.textContent = '读取失败：' + error.message;
    }
    modelSave.addEventListener('click', async () => {
      if (!modelsAvailable) return;
      modelSave.disabled = true;
      modelStatus.textContent = '正在保存…';
      try {
        const result = pick(await post('/api/video-analysis/settings', { model_id: modelSelect.value }));
        if (!ctx.view.isConnected) return;
        if (!result.ok) throw new Error(result._err || result.err || '保存失败');
        modelStatus.textContent = '已保存；未发起模型请求。新模型将使用独立的片段描述索引。';
      } catch (error) {
        if (ctx.view.isConnected) modelStatus.textContent = '保存失败：' + error.message;
      } finally {
        if (ctx.view.isConnected && modelsAvailable) modelSave.disabled = false;
      }
    });
  }

  global.VigilRenderVideoWorkbench = renderVideoWorkbench;
})(window);
