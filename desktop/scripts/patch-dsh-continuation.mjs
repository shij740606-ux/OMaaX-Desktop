// Continue a truncated model response inside the same DSH turn. Completed text,
// usage and raw stream remain durable; DSH already removes truncated tool calls.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const marker = 'OMAAX_AUTO_CONTINUE_V1';
const patches = [
  ['import { randomUUID } from "node:crypto";', 'import { randomUUID, createHash } from "node:crypto";'],
  ['\t\tlet turnEnds = null;', '\t\tlet turnEnds = null;\n\t\tthis.omaaxContinuation = { signature: null, stalled: 0 }; /*OMAAX_AUTO_CONTINUE_V1*/'],
  ['\t\t\tlet started = false;', '\t\t\tlet started = false;\n\t\t\tconst omaaxProgress = createHash("sha256");\n\t\t\tlet omaaxHasProgress = false;'],
  ['\t\t\t\t\tlive.push(chunk);', `\t\t\t\t\tlive.push(chunk);
\t\t\t\t\t// Hash deltas without provider IDs/timestamps so repeated empty responses terminate.
\t\t\t\t\tconst fragment = chunk.type === "text-delta" || chunk.type === "reasoning-delta" ? (chunk.text ?? "") : chunk.type === "tool-call-delta" ? (chunk.name ?? "") + (chunk.argumentsDelta ?? "") : "";
\t\t\t\t\tif (fragment.trim()) { omaaxHasProgress = true; omaaxProgress.update(fragment); }`],
  ['\t\t\t\tif (finish.kind === "max-tokens") return { kind: "max-tokens" };', `\t\t\t\tif (finish.kind === "max-tokens") {
\t\t\t\t\tsignal.throwIfAborted();
\t\t\t\t\tconst signature = omaaxProgress.digest("hex");
\t\t\t\t\tconst previous = this.omaaxContinuation;
\t\t\t\t\tconst stalled = !omaaxHasProgress || signature === previous.signature ? previous.stalled + 1 : 0;
\t\t\t\t\tthis.omaaxContinuation = { signature, stalled };
\t\t\t\t\tif (stalled >= 2) throw new LlmError("模型连续返回空内容或重复的截断内容，自动续接未能推进。已有内容已保存，请检查模型服务后重试。", "AUTO_CONTINUE_STALLED");
\t\t\t\t\tthis.inbox.append("next-step", createUserMessage({
\t\t\t\t\t\tcontent: [{ type: "text", text: "[OMaaX 自动续接] 上一次响应触及模型单次输出上限。请从已保存内容的断点继续当前任务，不要重复已完成的内容或操作，也不要要求用户发送继续。若上一次工具调用的参数被截断，该调用并未执行；请重新发出完整调用，必要时分成较小步骤。遵循当前用户指令，任务完成后正常结束。" }],
\t\t\t\t\t\tsource: { kind: "plugin", plugin: "omaax-output-continuation", form: "notice", summary: "正在接续未完成的回答" }
\t\t\t\t\t}));
\t\t\t\t\treturn null;
\t\t\t\t}
\t\t\t\tthis.omaaxContinuation = { signature: null, stalled: 0 };`],
];

export function patchContinuation(runtimeDir = path.join(root, 'dsh-runtime')) {
  const candidates = [
    'node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js',
    'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js',
  ].map(p => path.join(runtimeDir, p)).filter(existsSync);
  if (!candidates.length) throw new Error('DSH agent-loop runtime missing; cannot stage automatic continuation');
  for (const file of candidates) {
    let source = readFileSync(file, 'utf8');
    if (source.includes(marker)) continue;
    // Check every anchor before writing: a dependency update must not silently
    // ship a partial patch or restore the old manual-continue behavior.
    for (const [before] of patches) {
      if (source.split(before).length !== 2) throw new Error(`DSH continuation patch anchor changed: ${before}`);
    }
    for (const [before, after] of patches) source = source.replace(before, after);
    // Stable 47 keeps the original plugin message source schema.
    writeFileSync(file, source);
  }
  return candidates;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(`DSH automatic continuation ready (${patchContinuation(process.argv[2]).length} runtime)`);
}
