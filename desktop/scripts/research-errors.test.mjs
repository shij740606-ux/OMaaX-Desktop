import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

function load(relative) {
  const code = ts.transpileModule(fs.readFileSync(new URL(relative, import.meta.url), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  const exports = {};
  new Function('exports', code)(exports);
  return exports;
}
const {researchFailure} = load('../src/renderer/src/views/research-data.ts');
const {RESEARCH_EN} = load('../src/renderer/src/i18n.research.en.ts');
const cases = [
  ['RESEARCH_CONNECTION', '模型连接未建立，请检查网络和登录状态后重试。'],
  ['RESEARCH_IDLE_TIMEOUT', '模型服务长时间未返回新内容，原有内容已保留，请重试。'],
  ['RESEARCH_INTERRUPTED', '研究内容接收中断，原有内容已保留，请重试。'],
  ['RESEARCH_MODEL_INVALID', '模型回复格式异常，原有内容已保留，请重试。'],
  ['RESEARCH_TRUNCATED', '模型回复未完整生成，原有内容已保留，请重试。'],
  ['RESEARCH_LOGIN', '登录已失效，请重新登录后更新研究内容。'],
  ['RESEARCH_PERMISSION', '当前账号暂不能使用模型服务，请联系管理员。'],
  ['RESEARCH_CONTEXT', '研究资料超出模型可接收范围，请精简资料后重试。'],
];
for (const [code, message] of cases) {
  test(`${code} has an actionable translated message without raw provider details`, () => {
    assert.equal(researchFailure(new Error(`${code}: private provider response`)), message);
    assert.ok(RESEARCH_EN[message]);
    assert.doesNotMatch(RESEARCH_EN[message], /\p{Script=Han}|private provider response/u);
  });
}
test('legacy model errors retain the model-service fallback', () => {
  assert.equal(researchFailure('RESEARCH_MODEL: failed'), '模型服务暂时不可用，请检查登录状态和模型权限后重试。');
});
