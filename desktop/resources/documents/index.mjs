// Host-side fixed document operations. Model-written scripts still use the
// ordinary terminal tool and its existing workspace sandbox.
import { realpath, lstat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runProcess, parseArguments } from './cli.mjs';

export const name = 'omaax-documents';
export const inject = ['tools', 'sandboxPolicy', 'systemPrompt'];
const runner = fileURLToPath(new URL('./cli.mjs', import.meta.url));

function inside(root, candidate) {
  const rel = path.relative(root, candidate);
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
}

export async function projectPath(root, value, existing = false) {
  if (typeof value !== 'string' || !value.trim() || /[\x00-\x1f]/.test(value)) throw Error('A local project file path is required.');
  const target = path.resolve(root, value);
  if (!inside(root, target) || target === root) throw Error('Document files must be inside the current project.');
  let ancestor = target;
  while (true) {
    try {
      await lstat(ancestor);
      if (!inside(root, await realpath(ancestor))) throw Error('Document paths cannot follow a link outside the current project.');
      break;
    } catch (error) {
      if (error.code !== 'ENOENT' || existing) throw error;
      ancestor = path.dirname(ancestor);
    }
  }
  return target;
}

export function createDocumentTool(resolvePolicy, invoke = runProcess) {
  return {
    name: 'omaax_documents',
    description: 'Prepare the local document environment, convert project DOCX/PPTX to PDF, or render document pages to PNG previews. For any Word/PPT/PDF task first load skill omaax-documents (even if absent from a shortened catalog). prepare returns the Python executable; write generation/edit scripts in the project and execute that Python with the existing terminal tool. This tool does not run scripts, replace files, or call another model. Save new revisions in the current project.',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', enum: ['prepare', 'pdf', 'preview'] },
        input: { type: 'string', description: 'Existing project DOCX/PPTX, or PDF for preview.' },
        output: { type: 'string', description: 'New project PDF filename, or new preview directory.' },
        pages: { type: 'string', description: 'For preview: all or one-based comma-separated pages, e.g. 1,2.' },
        dpi: { type: 'integer', minimum: 24, maximum: 600 },
      },
      required: ['action'], additionalProperties: false,
    },
    output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) {
      const policy = await resolvePolicy({ session: exec.agent?.session });
      if (!policy || policy.mode === 'read-only') throw Error('Document preparation and export require a writable project.');
      const cwd = exec.agent?.session?.header?.cwd;
      if (!cwd || !path.isAbsolute(cwd)) throw Error('Select a local project before creating documents.');
      const root = await realpath(cwd);
      if (!args || Object.keys(args).some(key => !['action', 'input', 'output', 'pages', 'dpi'].includes(key))) throw Error('Invalid document options.');
      const command = [args.action];
      if (args.action === 'prepare') {
        if (Object.keys(args).length !== 1) throw Error('prepare does not accept document paths or rendering options.');
      } else if (args.action === 'pdf' || args.action === 'preview') {
        const input = await projectPath(root, args.input, true);
        const output = await projectPath(root, args.output);
        command.push('--input', input, args.action === 'pdf' ? '--output' : '--output-dir', output);
        if (args.pages !== undefined) command.push('--pages', String(args.pages));
        if (args.dpi !== undefined) command.push('--dpi', String(args.dpi));
      } else throw Error('Unknown document operation.');
      parseArguments(command);
      exec.signal?.throwIfAborted();
      const output = await invoke(process.execPath, [runner, ...command], {
        cwd: root, env: process.env, signal: exec.signal, capture: true,
      });
      return JSON.parse(output);
    },
  };
}

export function apply(ctx) {
  ctx.tools.register(createDocumentTool(request => ctx.sandboxPolicy.resolve(request)));
  ctx.systemPrompt.section({
    name: 'omaax:documents', order: 660,
    text: 'For Word, PPT/PowerPoint or PDF generation and revision, load the omaax-documents skill by name even if a shortened skill catalog omits it. Use omaax_documents for preparation, PDF conversion and page previews. Run project generation/edit scripts through the existing terminal tool using the returned Python executable. Reuse the current model, preserve source results and previous revisions, and save deliverables in the current project (Analysis results > Project documents).',
  });
}
