// -*- coding: utf-8 -*-
/**
 * Marvis 文件分类方案页。
 *
 * 内容逆向自腾讯 Marvis 1.60.3000.446 的本地知识库引擎
 *（F:\Program Files\Tencent\Marvis\Knowledgebase\1.60.3000.446\http_server.pyd，
 * Cython 编译，命名空间 knowledgebase.*），三类证据：
 *   - 扩展名常量表（knowledgebase.index_build.file_scan.high_value.types）
 *   - magic bytes 嗅探（_IMAGE_MAGIC_BYTES / _header_matches_heic）
 *   - 端侧意图模型 docstring（TextIntentModel，OpenVINO/QAI，7 类）
 * 本页把扩展名表 + magic bytes 两层移植成纯前端参考实现，可直接试。
 */
(function (global) {
  'use strict';

  // ── 从 http_server.pyd 提取的常量（原样移植） ────────────────────────────
  const TABLES = {
    video: ['mp4', 'mov', '3gp', 'rmvb', 'avi', 'flv', 'ts', 'webm', 'm4v', 'wmv', 'rm', 'mkv', 'mpg', 'mpeg', 'vob'],
    audio: ['wav', 'wma', 'mp3', 'ape', 'aac', 'flac', 'm4a', 'ogg'],
    img: ['nrw', 'mrw', 'dcr', 'heic', 'jpg', 'x3f', 'orf', 'raw', 'rw2', 'erf', 'raf', '3fr', 'iiq', 'psd', 'avif',
      'bmp', 'kdc', 'heif', 'pef', 'png', 'cr3', 'dng', 'arw', 'webp', 'nef', 'tif', 'eps', 'svg', 'gif', 'jpeg',
      'srw', 'cr2', 'rwl', 'tiff', 'ai'],
    strongDoc: ['pptx', 'md', 'ppt', 'doc', 'pdf', 'docx', 'xlsx', 'csv', 'xls'],
    target: ['txt', 'pptx', 'md', 'ppt', 'doc', 'pdf', 'docx', 'html', 'xlsx', 'csv', 'xls'],
    code: ['css', 'less', 'jsx', 'sh', 'tsx', 'kt', 'scala', 'cfg', 'yaml', 'hpp', 'xml', 'cs', 'json', 'graphql',
      'toml', 'cpp', 'php', 'proto', 'rs', 'bat', 'conf', 'thrift', 'rb', 'pl', 'mjs', 'sql', 'lua', 'vue', 'ps1',
      'java', 'ts', 'hjs', 'go', 'yml', 'htm', 'py', 'swift'],
    program: ['dll', 'sys', 'dylib', 'war', 'pyo', 'pyc', 'msi', 'class', 'obj', 'so', 'drv', 'pdb', 'exe', 'ocx'],
  };
  // Marvis 聚合器的 6 路口径：target / video / audio / code / program / other（另含 img 计数）
  const CATEGORY = {
    video: { name: '视频', cls: 'mv-cat-video' },
    audio: { name: '音频', cls: 'mv-cat-audio' },
    img: { name: '图片 / RAW', cls: 'mv-cat-img' },
    strongDoc: { name: '文档', cls: 'mv-cat-doc' },
    code: { name: '代码', cls: 'mv-cat-code' },
    program: { name: '程序 / 二进制', cls: 'mv-cat-program' },
    other: { name: '其他', cls: 'mv-cat-other' },
    noise: { name: '噪音（不入索引）', cls: 'mv-cat-noise' },
  };
  // 文件名黑名单（_HV_NOISE / _NOISE_TXT_MD / _NOISE_HTML，节选高频项）
  const NOISE_NAMES = new Set(['readme', 'license', 'licence', 'changelog', 'changes', 'copying', 'notice', 'authors',
    'contributors', 'install', 'todo', 'news', 'version', 'history', 'manifest', 'requirements', 'makefile', 'cmakelists',
    'roadmap', 'upgrade', 'security', 'support', 'governance', 'maintainers', 'contributing', 'issue_template',
    'pull_request_template', 'bug_report', 'feature_request', 'installation', 'migration', 'upgrading', 'building']);
  const NOISE_HTML = new Set(['index.html', 'index.htm', '404.html', '500.html', '502.html', '503.html', '403.html',
    'error.html', 'error_page.html', 'offline.html', 'offline_page.html', 'manifest.html', 'favicon.html',
    'skeletonloading.html']);
  // magic bytes（_IMAGE_MAGIC_BYTES / ISOBMFF heic 判定）
  const MAGIC = [
    { name: 'PNG', exts: ['png'], bytes: [0x89, 0x50, 0x4e, 0x47] },
    { name: 'BMP', exts: ['bmp'], bytes: [0x42, 0x4d] },
    { name: 'GIF', exts: ['gif'], bytes: [0x47, 0x49, 0x46, 0x38] },
    { name: 'RIFF（WEBP）', exts: ['webp'], bytes: [0x52, 0x49, 0x46, 0x46] },
    { name: 'TIFF（II）', exts: ['tif', 'tiff'], bytes: [0x49, 0x49, 0x2a, 0x00] },
    { name: 'TIFF（MM）', exts: ['tif', 'tiff'], bytes: [0x4d, 0x4d, 0x00, 0x2a] },
  ];

  function extOf(name) {
    const base = String(name || '').trim().split(/[\\/]/).pop();
    const m = base.match(/\.([A-Za-z0-9]+)$/);
    return m ? m[1].toLowerCase() : '';
  }
  function stemOf(name) {
    return String(name || '').trim().split(/[\\/]/).pop().replace(/\.[^.]*$/, '').toLowerCase();
  }

  // 6 路分类（移植 aggregator._classify_ext 的口径，img 单列）
  function classifyName(name) {
    const base = String(name || '').trim().split(/[\\/]/).pop().toLowerCase();
    if (NOISE_HTML.has(base)) return 'noise';
    if (NOISE_NAMES.has(stemOf(base))) return 'noise';
    const ext = extOf(base);
    if (!ext) return 'other';
    if (TABLES.video.indexOf(ext) >= 0) return 'video';
    if (TABLES.audio.indexOf(ext) >= 0) return 'audio';
    if (TABLES.img.indexOf(ext) >= 0) return 'img';
    if (TABLES.strongDoc.indexOf(ext) >= 0) return 'strongDoc';
    if (TABLES.target.indexOf(ext) >= 0) return 'strongDoc';   // 弱文档并入文档展示
    if (TABLES.code.indexOf(ext) >= 0) return 'code';
    if (TABLES.program.indexOf(ext) >= 0) return 'program';
    return 'other';
  }

  function sniffMagic(u8) {
    for (const m of MAGIC) {
      if (m.bytes.every((b, i) => u8[i] === b)) {
        if (m.name === 'RIFF（WEBP）') {
          const tag = String.fromCharCode(u8[8], u8[9], u8[10], u8[11]);
          return tag === 'WEBP' ? m : null;
        }
        return m;
      }
    }
    // ISOBMFF：offset 4..11 为 'ftyp' + 品牌（heic / heif / mif1 / heix）
    const brand = String.fromCharCode(u8[4], u8[5], u8[6], u8[7], u8[8], u8[9], u8[10], u8[11]);
    if (brand.slice(0, 4) === 'ftyp' && /^(heic|heif|mif1|heix|hevc)/.test(brand.slice(4))) {
      return { name: 'HEIF/HEIC（ISOBMFF）', exts: ['heic', 'heif'] };
    }
    return null;
  }

  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const STYLE = `
  .marvis-page { max-width: 1080px; }
  .mv-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin:12px 0; }
  .mv-step { background:var(--card,#fff); border:1px solid var(--line,#e5e7eb); border-radius:10px; padding:12px 14px; }
  .mv-step b { display:block; margin-bottom:4px; }
  .mv-step small { color:var(--muted,#6b7280); line-height:1.5; display:block; }
  .mv-tag { display:inline-block; padding:1px 8px; border-radius:999px; font-size:11px; margin-right:6px; }
  .mv-cat-video { background:#e8f0fe; color:#1a56b8; } .mv-cat-audio { background:#fef3e2; color:#9a5b00; }
  .mv-cat-img { background:#e9f6ec; color:#1c7a35; } .mv-cat-doc { background:#fdeef3; color:#b0246e; }
  .mv-cat-code { background:#eef0fd; color:#4640c9; } .mv-cat-program { background:#f0f0f2; color:#4b5563; }
  .mv-cat-other { background:#f4f4f5; color:#6b7280; } .mv-cat-noise { background:#fdeaea; color:#b3261e; }
  .mv-row { display:flex; align-items:center; gap:8px; padding:5px 0; border-bottom:1px dashed var(--line,#eceef1); font-size:12.5px; }
  .mv-row:last-child { border-bottom:0; }
  .mv-demo { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:900px){ .mv-demo { grid-template-columns:1fr; } }
  .mv-drop { border:1.5px dashed var(--line,#c9cdd4); border-radius:10px; padding:18px; text-align:center;
    color:var(--muted,#6b7280); font-size:12.5px; margin-top:8px; }
  .mv-drop.over { border-color:#4a8fe7; background:rgba(74,143,231,.06); }
  .mv-tablewrap details { margin:6px 0; }
  .mv-tablewrap summary { cursor:pointer; font-size:13px; padding:6px 0; }
  .mv-chips { display:flex; flex-wrap:wrap; gap:4px; padding:4px 0 8px; }
  .mv-chip { font:11px/1 ui-monospace,Consolas,monospace; padding:3px 7px; border-radius:6px;
    background:var(--chip,#f3f4f6); color:var(--muted,#374151); }
  .mv-note { font-size:11.5px; color:var(--muted,#6b7280); line-height:1.6; margin-top:6px; }
  .mv-bad { color:#b3261e; font-weight:600; } .mv-ok { color:#1c7a35; font-weight:600; }`;

  async function renderMarvis(ctx) {
    ctx.topbar.className = 'topbar';
    ctx.topbar.innerHTML = '<h1>Marvis 分类方案</h1>'
      + '<div class="sp"></div><span class="muted mono" style="font-size:11.5px">逆向自 Tencent Marvis 1.60.3000.446 · knowledgebase.*</span>';
    ctx.view.className = 'scroll';
    ctx.view.innerHTML = '<style>' + STYLE + '</style><div class="wrap marvis-page" id="mvRoot"></div>';
    const root = ctx.view.querySelector('#mvRoot');

    root.innerHTML =
      '<div class="card hd"><div class="h"><h3>它是怎么把文档 / 图片 / 视频分得又准又全的</h3></div>'
      + '<div class="mv-grid">'
      + '<div class="mv-step"><b>① 扩展名静态表</b><small>六路映射 target / video / audio / code / program / other，图片表覆盖相机 RAW 长尾（cr2 cr3 nef arw dng…）；readme、license、index.html 等噪音文件直接不进索引。</small></div>'
      + '<div class="mv-step"><b>② magic bytes 嗅探</b><small>对"自称是图片"的文件校验真实头字节：PNG / BMP / GIF / RIFF(WEBP) / TIFF(II/MM) / HEIC(ISOBMFF ftyp 品牌)。扩展名可伪造，文件头不会。</small></div>'
      + '<div class="mv-step"><b>③ 端侧意图模型</b><small>TextIntentModel（OpenVINO/QAI 双后端，7 类：文档 / 应用 / 设置 / 图片 / 视频 / 音频 / 其他），对扩展名无信息或二义的文件按文件名文本兜底；搜索查询同样过意图路由。</small></div>'
      + '<div class="mv-step"><b>④ lite / full 两阶段 + 目录评分</b><small>lite 阶段先把视频 / 音频 / RAW 文件名收进倒排索引（秒可搜），full 阶段全类型解析；目录按文档占比 / 图片占比打分决定优先级。封面用确定性规则渲染（表格→淡绿表头卡、文档→白卡黑标题），多级降级不依赖模型。</small></div>'
      + '</div>'
      + '<div class="mv-note">知识库分类标签（前端点击筛选用的那层）是双形态：云端 LLM 可用时按固定 JSON 协议生成（3~10 个标签、每个 ≤8 字），不可用时回退本地规则聚合。模型 blob 在 data/ 下加密分片存储。</div>'
      + '</div>'

      + '<div class="card hd"><div class="h"><h3>试一试：移植的分类器</h3></div>'
      + '<div class="mv-demo">'
      + '<div><div class="muted" style="font-size:12px;margin-bottom:4px">每行一个文件名（或直接把文件拖到右侧）：</div>'
      + '<textarea class="input" id="mvNames" rows="9" style="width:100%;font-family:ui-monospace,Consolas,monospace;font-size:12px" '
      + 'placeholder="报告最终版.pdf&#10;IMG_2026.mp4&#10;readme.md&#10;重阳节合影.cr3&#10;模型权重.bin&#10;train.py&#10;截图.png"></textarea>'
      + '<div class="mv-note">回车按行分类。分类口径：扩展名表 → 噪音名单 → 其他。</div></div>'
      + '<div><div class="muted" style="font-size:12px;margin-bottom:4px">拖入真实文件 → 额外做 magic bytes 校验（第②层）：</div>'
      + '<div class="mv-drop" id="mvDrop">把文件拖到这里（只读文件头 16 字节，不上传）</div>'
      + '<div id="mvMagic" class="mv-note"></div></div>'
      + '</div>'
      + '<div id="mvOut" style="margin-top:12px"></div>'
      + '</div>'

      + '<div class="card hd"><div class="h"><h3>逆向提取的参考常量表</h3></div><div class="mv-tablewrap" id="mvTables"></div>'
      + '<div class="mv-note">来源：http_server.pyd 内 high_value/types 常量区与函数 docstring。原文为无点小写扩展名；noise 黑名单为节选（readme / license / changelog 等十余族 + 前端构建产物 index.html / 404.html 等）。</div></div>';

    // 常量表
    const meta = [
      ['视频 VIDEO_EXTENSIONS', 'video'], ['音频 AUDIO_EXTENSIONS', 'audio'],
      ['图片（含相机 RAW）IMG_EXTENSIONS', 'img'], ['强文档 STRONG_DOC_EXTENSIONS', 'strongDoc'],
      ['可检索文档 TARGET_EXTENSIONS', 'target'], ['代码 CODE_EXTENSIONS', 'code'],
      ['程序 / 二进制 PROGRAM_EXTENSIONS', 'program'],
    ];
    root.querySelector('#mvTables').innerHTML = meta.map(([label, key]) =>
      '<details><summary>' + esc(label) + ' <span class="muted mono" style="font-size:11px">' + TABLES[key].length + '</span></summary>'
      + '<div class="mv-chips">' + TABLES[key].map((e) => '<span class="mv-chip">.' + esc(e) + '</span>').join('') + '</div></details>'
    ).join('');

    // 文件名分类
    const out = root.querySelector('#mvOut');
    function runNames() {
      const lines = root.querySelector('#mvNames').value.split('\n')
        .map((s) => s.trim()).filter(Boolean).slice(0, 300);
      if (!lines.length) { out.innerHTML = ''; return; }
      const buckets = {};
      lines.forEach((n) => { (buckets[classifyName(n)] = buckets[classifyName(n)] || []).push(n); });
      out.innerHTML = Object.keys(CATEGORY).filter((k) => buckets[k])
        .map((k) => '<div class="mv-row"><span class="mv-tag ' + CATEGORY[k].cls + '">' + CATEGORY[k].name
          + ' × ' + buckets[k].length + '</span>'
          + '<span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
          + esc(buckets[k].join('  ')) + '</span></div>').join('');
    }
    root.querySelector('#mvNames').addEventListener('input', runNames);
    runNames();

    // magic bytes 拖放
    const drop = root.querySelector('#mvDrop');
    const magicOut = root.querySelector('#mvMagic');
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', async (e) => {
      e.preventDefault(); drop.classList.remove('over');
      const files = Array.from(e.dataTransfer.files || []).slice(0, 20);
      const rows = [];
      for (const f of files) {
        const head = new Uint8Array(await f.slice(0, 16).arrayBuffer());
        const m = sniffMagic(head);
        const ext = extOf(f.name);
        const claimed = classifyName(f.name);
        let verdict;
        if (!m) {
          verdict = f.type && /^(image|video|audio)\//.test(f.type)
            ? '<span class="mv-bad">嗅探未识别（声明 ' + esc(f.type) + '）</span>' : '<span class="muted">非图片类，跳过头校验</span>';
        } else if (m.exts.indexOf(ext) >= 0) {
          verdict = '<span class="mv-ok">' + esc(m.name) + ' · 与扩展名一致</span>';
        } else {
          verdict = '<span class="mv-bad">伪装！头是 ' + esc(m.name) + '，扩展名却是 .' + esc(ext || '无') + '</span>';
        }
        rows.push('<div class="mv-row"><span class="mv-tag ' + CATEGORY[claimed].cls + '">'
          + CATEGORY[claimed].name + '</span><b>' + esc(f.name) + '</b><span>' + verdict + '</span></div>');
      }
      magicOut.innerHTML = rows.join('') || '<span class="muted">没有拿到文件</span>';
    });
  }

  global.VigilRenderMarvis = renderMarvis;
})(window);
