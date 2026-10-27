// 写作专家的字数账：中文按字、英文按词，另外给标点和段落数。
//
// 为什么值得做成工具而不是让模型自己数：模型数字数**非常不准**，尤其中文
// 混英文标点时。而"改稿前后各跑一次，看删了多少"是写作场景里最常问的一句，
// 让模型每次都估一个数，等于每次都可能在骗人。
//
// 宿主契约：模块导出 setup(api)（或 register(api)），用 api.registerTool 注册。
// 工具全名 = <技能 id>__<工具 id>，这里是 writing-tools__count。
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g;
const PUNCT = /[，。！？；：、""''（）《》〈〉…—·,.!?;:"'()<>\[\]{}]/g;

function measure(text) {
  const raw = String(text == null ? '' : text);
  const cjk = (raw.match(CJK) || []).length;
  // 去掉中文字符再按空白切词：中英混排时英文词数才不会把每个汉字算一个
  const rest = raw.replace(CJK, ' ');
  const words = rest.replace(/[^\p{L}\p{N}\s'’-]/gu, ' ')
    .split(/\s+/).filter(Boolean);
  const paragraphs = raw.split(/\n\s*\n/).filter((p) => p.trim()).length;
  return {
    cjk_chars: cjk,
    words: words.length,
    total: cjk + words.length,
    chars_no_space: raw.replace(/\s/g, '').length,
    punctuation: (raw.match(PUNCT) || []).length,
    paragraphs,
  };
}

function setup(api) {
  api.registerTool({
    id: 'count',
    name: '字数统计',
    description: '数一段文字的字数：中文按字、英文按词，另给标点数与段落数。'
      + '改稿前后各跑一次就知道删了多少、有没有超篇幅。',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '要统计的文字，原样传进来即可' },
      },
      required: ['text'],
    },
    async execute(_ctx, args) {
      const r = measure((args || {}).text);
      return {
        ok: true,
        ...r,
        summary: '中文 ' + r.cjk_chars + ' 字 + 英文 ' + r.words + ' 词 = '
          + r.total + '，标点 ' + r.punctuation + '，段落 ' + r.paragraphs,
      };
    },
  });
}

module.exports = { setup };
