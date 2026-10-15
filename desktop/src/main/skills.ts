import { readFileSync, writeFileSync, existsSync, cpSync, rmSync, readdirSync, mkdirSync, renameSync } from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import type { AppPaths, CatalogSkill, SkillStatus } from '../shared/types';
import { resourcePath, nodeBinary } from './paths';

// 工具中心的“安装”非常朴素：把 resources/skills/<id> 复制到 $DSH_HOME/skills/<id>。
// dsh 的 skill-filesystem 会监听这个目录并热加载，模型下一轮就能看到新技能。
export class SkillStore {
  constructor(private paths: AppPaths) {}

  catalog(): CatalogSkill[] {
    const f = resourcePath('skills', 'catalog.json');
    if (!existsSync(f)) return [];
    return (JSON.parse(readFileSync(f, 'utf8')) as { skills: CatalogSkill[] }).skills;
  }

  statuses(): SkillStatus[] {
    return this.catalog().map((s) => {
      const dir = path.join(this.paths.skills, s.id);
      if (!existsSync(path.join(dir, 'SKILL.md'))) return { id: s.id, state: 'not_installed' as const };
      const vf = path.join(dir, '.biodsh-version');
      const installedVersion = existsSync(vf) ? readFileSync(vf, 'utf8').trim() : undefined;
      return {
        id: s.id,
        state: installedVersion && installedVersion !== s.version ? 'update_available' as const : 'installed' as const,
        installedVersion,
      };
    });
  }

  installedIds(): string[] {
    if (!existsSync(this.paths.skills)) return [];
    return readdirSync(this.paths.skills).filter((n) => existsSync(path.join(this.paths.skills, n, 'SKILL.md')));
  }

  async install(id: string): Promise<SkillStatus> {
    const skill = this.catalog().find((s) => s.id === id);
    if (!skill) return { id, state: 'error', error: '目录中没有这个技能' };
    if (!/^[a-zA-Z0-9_-]{1,160}$/.test(id)) return { id, state: 'error', error: '工具名称无效' };
    const src = resourcePath(skill.bundle ?? 'skills', id);
    const finalDst = path.join(this.paths.skills, id);
    if (existsSync(finalDst)) return { id, state: 'error', error: '工具目录已存在，不会覆盖原有文件' };
    const dst = path.join(this.paths.root, 'tool-downloads', randomUUID());
    try {
      mkdirSync(dst, { recursive: true });
      if (skill.tier === 'community') {
        const node = nodeBinary();
        const env: NodeJS.ProcessEnv = { ...process.env, NODE_USE_ENV_PROXY: '0' }; delete env.NODE_OPTIONS;
        await promisify(execFile)(node, [resourcePath('scripts', 'download-skill.mjs'), JSON.stringify(skill), dst], { env, windowsHide: true, timeout: 300000 });
      } else cpSync(src, dst, { recursive: true, filter: p => !/__pycache__|\.pyc$/.test(p) });
      if (!existsSync(path.join(dst, 'SKILL.md'))) throw new Error('下载内容缺少工具说明，未添加工具');
    // 给 SKILL.md 追加“桌面版怎么跑”的说明：PATH 里的 python 就是 OMaaX 生信环境。
    const md = path.join(dst, 'SKILL.md');
    const runNote = `

## Running in OMaaX Desktop

The OMaaX Python environment (scanpy, anndata, …) is already first on PATH; \`python\` resolves to it. Run this skill's script directly:

\`\`\`bash
python "${finalDst.replace(/\\/g, '/')}/${(skill as { entry?: { script?: string } }).entry?.script ?? 'run.py'}" --input <input file> --outdir <output directory> --seed 0
\`\`\`

Write outputs into a fresh directory under the current workspace, then summarize the generated files (${skill.outputs.join(', ')}) for the user in plain language.
`;
    const communityNote = skill.sourceGuide ? `

## Running in OMaaX Desktop

This is a user-added source guide, stored at \`${finalDst.replace(/\\/g, '/')}\`. Read its local references and configure only this tool's required software on the user's chosen computer or SSH server. Adding the guide does not install the analysis software. Use the tool's actual language and runtime, not necessarily Python. Keep original inputs and store new outputs in the user's project/task output directory. Explain results in plain language.
` : `

## Running in OMaaX Desktop

This skill was collected from ${skill.origin?.repo ?? 'a community repository'} (${skill.origin?.license ?? 'see repo license'}). The OMaaX Python environment (scanpy, anndata, pandas, …) is first on PATH, so \`python\` resolves to it; install any extra package the skill needs with \`uv pip install <pkg>\` (uv is on PATH) or \`python -m pip install <pkg>\`. Scripts referenced by this skill live in \`${finalDst.replace(/\\/g, '/')}\`. Explain results to the user in plain language.
`;
    writeFileSync(md, readFileSync(md, 'utf8').trimEnd() + (skill.tier === 'community' ? communityNote : runNote));
    writeFileSync(path.join(dst, '.biodsh-version'), skill.version + '\n');
    mkdirSync(this.paths.skills, { recursive: true });
    renameSync(dst, finalDst);
    return { id, state: 'installed', installedVersion: skill.version };
    } catch (e) { rmSync(dst, { recursive: true, force: true }); return { id, state: 'error', error: String(e) }; }
  }

  uninstall(id: string): SkillStatus {
    if (!/^[a-zA-Z0-9_-]{1,160}$/.test(id)) return { id, state: 'error', error: '工具名称无效' };
    rmSync(path.join(this.paths.skills, id), { recursive: true, force: true });
    return { id, state: 'not_installed' };
  }

  readme(id: string): string {
    const skill = this.catalog().find((s) => s.id === id);
    const local = path.join(this.paths.skills, id, 'SKILL.md');
    const f = existsSync(local) ? local : resourcePath(skill?.bundle ?? 'skills', id, 'SKILL.md');
    return existsSync(f) ? readFileSync(f, 'utf8').replace(/^---[\s\S]*?---\s*/, '') : '';
  }
}
