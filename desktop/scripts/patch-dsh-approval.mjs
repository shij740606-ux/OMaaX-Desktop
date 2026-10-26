// Keep approval details correlated with the exact call, including run_code sub-tools.
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const marker = 'OMAAX_APPROVAL_DETAILS_V1';
const start = '\t\tfunction ApprovalCommand(';
const end = '\t\t//#endregion';
const implementation = `\t\t/*OMAAX_APPROVAL_DETAILS_V1*/
\t\tfunction approvalCall(root, callId, seen = new Set()) {
\t\t\tif (!root || seen.has(root)) return;
\t\t\tseen.add(root);
\t\t\tif (root.callId === callId) return "kind" in root ? root.call ?? void 0 : root;
\t\t\tfor (const child of root.subCalls ?? []) {
\t\t\t\tconst found = approvalCall(child, callId, seen);
\t\t\t\tif (found) return found;
\t\t\t}
\t\t}
\t\tfunction ApprovalCommand({ callId, useChat, t }) {
\t\t\tconst detail = useChat((snapshot) => {
\t\t\t\tfor (const node of snapshot.nodes.values()) {
\t\t\t\t\tif (node.kind !== "tool-call") continue;
\t\t\t\t\tconst call = approvalCall(node.data.root, callId);
\t\t\t\t\tif (call) return commandOf(call) ?? null;
\t\t\t\t}
\t\t\t});
\t\t\treturn detail === void 0 ? t("approval.detailUnavailable") : detail;
\t\t}
`;

function once(source, before, after) {
  if (source.split(before).length !== 2) throw new Error(`DSH approval patch anchor changed: ${before}`);
  return source.replace(before, after);
}

export function patchApprovalSource(source) {
  if (source.includes(marker)) {
    const from = source.indexOf(`\t\t/*${marker}*/`), to = source.indexOf(end, from);
    if (from < 0 || to < 0) throw new Error('DSH patched approval detail region changed');
    return source.slice(0, from) + implementation + source.slice(to);
  }
  const from = source.indexOf(start), to = source.indexOf(end, from);
  if (from < 0 || to < 0 || !source.slice(from, to).includes('root.callId === callId')) {
    throw new Error('DSH approval detail implementation changed');
  }
  source = source.slice(0, from) + implementation + source.slice(to);
  source = once(source, '{ name: "conversation.approval.detail" }, ApprovalCommand', '{ name: "conversation.approval.detail", locale: NS }, ApprovalCommand');
  source = once(source, 'const zh = {', 'const zh = {\n\t\t\t"approval.detailUnavailable": "暂未读取到这次操作的详细参数，请先核对审批说明。",');
  return once(source, 'const en = {', 'const en = {\n\t\t\t"approval.detailUnavailable": "Details for this operation are not available yet. Review the approval description before deciding.",');
}

export function patchApproval(runtimeDir = path.join(root, 'dsh-runtime')) {
  const bases = ['node_modules/@deepseek-ai', 'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai']
    .map(p => path.join(runtimeDir, p)).filter(p => existsSync(path.join(p, 'dsh-client-ui-chat/lib/client.js')));
  if (!bases.length) throw new Error('DSH approval runtime missing');
  const files = [];
  for (const base of bases) {
    const chatFile = path.join(base, 'dsh-client-ui-chat/lib/client.js');
    const panelFile = path.join(base, 'dsh-client-ui-approval/lib/client.js');
    const chat = patchApprovalSource(readFileSync(chatFile, 'utf8'));
    let panel = readFileSync(panelFile, 'utf8');
    if (!panel.includes('OMAAX_APPROVAL_REASON_V1')) {
      panel = once(panel, 'pending.reason ?? t("escalation", { toolName: pending.toolName })',
        '/*OMAAX_APPROVAL_REASON_V1*/ pending.reason?.trim() || t("escalation", { toolName: pending.toolName })');
    }
    writeFileSync(chatFile, chat);
    writeFileSync(panelFile, panel);
    files.push(chatFile, panelFile);
  }
  return files;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(`DSH approval details ready (${patchApproval(process.argv[2]).length} files)`);
}
