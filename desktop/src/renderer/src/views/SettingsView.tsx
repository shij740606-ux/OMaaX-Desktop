import { useEffect, useState } from 'react';
import { RefreshCw, Plug, Plus, Trash2, SlidersHorizontal, Palette, Info, ChevronRight } from 'lucide-react';
import InfoDot, { useHelp } from '../components/HelpTip';
export { default as InfoDot } from '../components/HelpTip';
import { ExternalLink, FolderOpen, KeyRound } from 'lucide-react';
import { useApp } from '../store';
import type { McpServer } from '@shared/types';
import { useT } from '../i18n';

// OpenAI 兼容的模型提供商预设：选一个自动填「接口地址 + 模型名」，也可手填。
// dsh 引擎走 chat/completions 协议，这些都是兼容端点；Anthropic 原生协议暂不支持。
const PROVIDERS: { id: string; label: string; base: string; model: string }[] = [
  { id: 'deepseek', label: 'DeepSeek 官方', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { id: 'openai', label: 'OpenAI (Codex / GPT)', base: 'https://api.openai.com/v1', model: 'gpt-4o' },
  { id: 'moonshot', label: 'Moonshot Kimi', base: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-32k' },
  { id: 'qwen', label: '通义千问 DashScope', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { id: 'zhipu', label: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-plus' },
  { id: 'siliconflow', label: '硅基流动 SiliconFlow', base: 'https://api.siliconflow.cn/v1', model: 'deepseek-ai/DeepSeek-V3' },
];

const SETTINGS_PANES = [
  { id: 'model', label: '对话模型', detail: '连接方式与访问密钥', icon: KeyRound },
  { id: 'tools', label: '工具与服务', detail: '图像生成与外部工具', icon: Plug },
  { id: 'workspace', label: '文件与软件', detail: '保存位置与软件下载', icon: FolderOpen },
  { id: 'appearance', label: '界面偏好', detail: '主题与显示语言', icon: Palette },
  { id: 'about', label: '关于 OMaaX', detail: '版本、引导与许可', icon: Info },
] as const;
type SettingsPane = typeof SETTINGS_PANES[number]['id'];

export default function SettingsView() {
  const { settings, updateSettings, credential, saveKey, info, restartDsh } = useApp();
  const { t } = useT();
  const [pane, setPane] = useState<SettingsPane>('model');
  const [key, setKey] = useState('');
  const offline = settings?.mode === 'offline';
  const modelHelp = useHelp('model');
  const [draft, setDraft] = useState<{ offlineBaseUrl?: string; offlineModel?: string; offlineApiKey?: string; remoteDshUrl?: string }>({});
  const saveOffline = async () => { await updateSettings(draft); setDraft({}); void restartDsh(); };
  const [saved, setSaved] = useState(false);
  const dshVersion = (info as { dshVersion?: string } | null)?.dshVersion ?? '?';
  useEffect(() => { setSaved(false); }, [key]);
  if (!settings) return null;
  const submit = async () => { await saveKey(key); setKey(''); setSaved(true); };
  return (
    <div className="h-full flex flex-col omaax-page omaax-settings">
      <header className="omaax-settings-topbar drag"><div><h1>{t('设置')}</h1><p>{t('让 OMaaX 适合你的工作方式。')}</p></div><span className="omaax-settings-version">OMaaX {info?.version}</span></header>
      <div className="omaax-settings-layout">
        <nav className="omaax-settings-nav" role="tablist" aria-label={t('设置分类')} aria-orientation="vertical">
          {SETTINGS_PANES.map(({id,label,detail,icon:Icon},index)=><button type="button" role="tab" id={'settings-tab-'+id} aria-selected={pane===id} aria-controls={'settings-pane-'+id} tabIndex={pane===id?0:-1} className={pane===id?'is-active':''} key={id} onClick={()=>setPane(id)} onKeyDown={e=>{let next=index;if(e.key==='ArrowDown')next=(index+1)%SETTINGS_PANES.length;else if(e.key==='ArrowUp')next=(index-1+SETTINGS_PANES.length)%SETTINGS_PANES.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=SETTINGS_PANES.length-1;else return;e.preventDefault();setPane(SETTINGS_PANES[next].id);document.getElementById('settings-tab-'+SETTINGS_PANES[next].id)?.focus();}}><span className="omaax-settings-nav-icon"><Icon size={18}/></span><span><strong>{t(label)}</strong><small>{t(detail)}</small></span><ChevronRight size={14}/></button>)}
          <div className="omaax-settings-nav-note"><SlidersHorizontal size={15}/><span>{t('只配置需要的功能，其余保持默认即可。')}</span></div>
        </nav>
        <div className="omaax-settings-panel">
          <div className="omaax-settings-content" data-pane="model" role="tabpanel" id="settings-pane-model" aria-labelledby="settings-tab-model" hidden={pane!=='model'} tabIndex={0}>          <Section title={modelHelp.title} desc={modelHelp.summary} tip={modelHelp.detail} wide>
            <div className="seg">
              {(['online', 'offline'] as const).map((k) => <button key={k} className={(settings.mode ?? 'online') === k ? 'active' : ''} onClick={async () => { await updateSettings({ mode: k }); void restartDsh(); }}>{t({ online: '云端模型', offline: '本地／内网模型' }[k])}</button>)}
            </div>
            {offline && (
              <div className="flex flex-col gap-3 mt-3">
                <div className="flex flex-col gap-1.5">
                  <FieldLabel label={t('接口示例（可选）')} tip={t('示例只帮助填写地址和模型名称。本地／内网部署请改为部署方提供的地址；使用云端示例仍会联网。')} />
                  <select className="field" value="" onChange={(e) => { const p = PROVIDERS.find((x) => x.id === e.target.value); if (p) setDraft((d) => ({ ...d, offlineBaseUrl: p.base, offlineModel: p.model })); }}>
                    <option value="">{t('选择提供商…')}</option>
                    {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                </div>
                <div className="flex flex-col gap-1.5">
                  <FieldLabel label={t('模型接口地址')} tip={t('OpenAI/DeepSeek 兼容端点，例如 http://192.168.1.10:8000/v1')} />
                  <input className="field t-mono" placeholder="http://192.168.1.10:8000/v1" value={draft.offlineBaseUrl ?? settings.offlineBaseUrl ?? ''} onChange={(e) => setDraft((d) => ({ ...d, offlineBaseUrl: e.target.value }))} />
                </div>
                <div className="flex gap-3">
                  <div className="flex-1 flex flex-col gap-1.5"><FieldLabel label={t('模型名称')} /><input className="field t-mono" placeholder="qwen3-32b / deepseek-r1" value={draft.offlineModel ?? settings.offlineModel ?? ''} onChange={(e) => setDraft((d) => ({ ...d, offlineModel: e.target.value }))} /></div>
                  <div className="flex-1 flex flex-col gap-1.5"><FieldLabel label={t('接口密钥')} tip={t('内网自建服务通常不需要密钥，留空即可。')} /><input className="field t-mono" type="password" placeholder={t('可留空')} value={draft.offlineApiKey ?? settings.offlineApiKey ?? ''} onChange={(e) => setDraft((d) => ({ ...d, offlineApiKey: e.target.value }))} /></div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <FieldLabel label={t('已有助手服务地址（可选）')} tip={t('仅当课题组已部署兼容的助手服务时填写，连接后使用服务器上的助手。它不是 SSH 地址，也不是模型接口；不确定时留空。')} />
                  <input className="field t-mono" placeholder="http://192.168.1.10:3080" value={draft.remoteDshUrl ?? settings.remoteDshUrl ?? ''} onChange={(e) => setDraft((d) => ({ ...d, remoteDshUrl: e.target.value }))} />
                </div>
                <div className="flex items-center gap-2 pt-0.5"><button className="btn btn-primary" disabled={Object.keys(draft).length === 0} onClick={saveOffline}>{t('保存并重新连接')}</button><InfoDot text={t('模型服务需提供 OpenAI 兼容的对话接口。不同模型的工具调用能力不同；地址和模型名请使用部署方提供的信息。')} /></div>
              </div>
            )}
          {!offline && <div className="omaax-settings-subsection"><div className="omaax-section-heading"><div><h3>{t('云端访问密钥')}</h3><p>{t('填写 DeepSeek 提供的 API Key，用于云端对话。')}</p></div><InfoDot title={t('云端访问密钥')} text={t('密钥用于连接你的 DeepSeek 账户，调用费用由服务商计费。密钥保存在本机配置中；使用本地／内网模型时，在上方填写对应服务的密钥。')} align="right"/></div>
            <div className="flex items-center gap-3">
              <div className="relative flex-1">
                <KeyRound size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-3)' }} />
                <input className="field !pl-9 t-mono" type="password" placeholder={credential.hasKey ? t('已设置 {masked}，输入新值可替换', { masked: credential.masked ?? '' }) : 'sk-…'} value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && key && submit()} />
              </div>
              <button className="btn btn-primary" disabled={!key.trim()} onClick={submit}>{saved ? t('已保存') : t('保存')}</button>
            </div>
            <div className="flex items-center gap-3 mt-3">
              <button className="btn btn-ghost" onClick={() => window.biodsh.openExternal('https://platform.deepseek.com/api_keys')}><ExternalLink size={13} /> {t('去 DeepSeek 开放平台申请')}</button>
              {credential.hasKey && <span className="t-caption">{t('已保存，智能体已自动重启使其生效')}</span>}
            </div>
          </div>}</Section></div>
          <div className="omaax-settings-content" data-pane="tools" role="tabpanel" id="settings-pane-tools" aria-labelledby="settings-tab-tools" hidden={pane!=='tools'} tabIndex={0}><ImageSection /><McpSection /></div>
          <div className="omaax-settings-content" data-pane="workspace" role="tabpanel" id="settings-pane-workspace" aria-labelledby="settings-tab-workspace" hidden={pane!=='workspace'} tabIndex={0}>          <Section title={t('本机工作目录')} desc={t('本机项目和分析文件的默认保存位置。')} tip={t('更改的是本机工作位置，不会迁移旧文件，也不会改变服务器上的目录。SSH 分析默认保存到服务器家目录的 omaax 文件夹，按项目、对话和任务分开管理。')}>
            <div className="flex items-center gap-3">
              <div className="omaax-workspace-label"><FolderOpen size={20}/><div><strong>{t('本机项目文件夹')}</strong><small>{t('默认保存本机项目和分析文件，可直接打开查看。')}</small></div></div>
              <button className="btn btn-fill" onClick={async () => { const p = await window.biodsh.pickFolder(); if (p) { await updateSettings({ workspace: p }); void restartDsh(); } }}>{t('更改…')}</button>
              <button className="btn btn-ghost" aria-label={t('打开工作目录')} onClick={() => window.biodsh.openPath(settings.workspace)}><FolderOpen size={13} />{t('打开')}</button>
            </div>
          </Section>

          <Section title={t('软件下载')} desc={t('按网络情况选择 Python 和软件包的下载来源。')} tip={t('国内镜像可能提高下载成功率；实际速度取决于网络和镜像服务。此设置不会自动下载工具，也不会把本机环境同步到服务器。')}>
            <Toggle checked={settings.useChinaMirror} onChange={(v) => updateSettings({ useChinaMirror: v })} label={t('使用国内镜像')} />
          </Section>

<MigrateSection /></div>
          <div className="omaax-settings-content" data-pane="appearance" role="tabpanel" id="settings-pane-appearance" aria-labelledby="settings-tab-appearance" hidden={pane!=='appearance'} tabIndex={0}>          <Section title={t('外观')}>
            <div className="seg">
              {(['system', 'light', 'dark'] as const).map((k) => <button key={k} className={settings.theme === k ? 'active' : ''} onClick={() => updateSettings({ theme: k })}>{t({ system: '跟随系统', light: '浅色', dark: '深色' }[k])}</button>)}
            </div>
          </Section>

          <Section title={t('语言 / Language')} desc={t('界面与智能体界面一起切换；智能体回答会跟随你提问的语言。')}>
            <div className="seg">
              {(['system', 'zh', 'en'] as const).map((k) => <button key={k} className={(settings.language ?? 'system') === k ? 'active' : ''} onClick={() => updateSettings({ language: k })}>{{ system: t('跟随系统'), zh: '中文', en: 'English' }[k]}</button>)}
            </div>
          </Section>

</div>
          <div className="omaax-settings-content" data-pane="about" role="tabpanel" id="settings-pane-about" aria-labelledby="settings-tab-about" hidden={pane!=='about'} tabIndex={0}>          <Section title="关于 OMaaX" wide><div className="omaax-wordmark">OMaa<span>X</span></div><p className="t-body mt-2">One Model, Beyond the Max.</p><p className="t-caption mt-2">连接数据、工具与计算资源的智能分析工作台。</p><div className="flex gap-3 mt-4"><button className="btn btn-tint" onClick={()=>info&&window.biodsh.openPath(info.paths.root)}><FolderOpen size={14}/>打开数据目录</button><button className="btn btn-ghost" onClick={()=>updateSettings({onboarded:false})}>使用引导</button></div><details className="mt-4 t-caption"><summary>版本与许可说明</summary><p className="mt-2">OMaaX {info?.version} · 助手组件 {dshVersion}</p><p>基于开源组件构建。原始版权和 MIT 许可证随软件保留。</p><p>独立版本，自动更新尚未启用。</p><p>应用文件由 OMaaX 管理，可通过“打开数据目录”查看。</p></details></Section></div>

        </div>
      </div>
    </div>
  );
}

// 表单项标签 + 紧跟其后的圈-i（每个小项自己的说明，而不是堆在最底下）。
function FieldLabel({ label, tip }: { label: string; tip?: string }) {
  return <div className="flex items-center gap-1.5"><span className="t-caption">{label}</span>{tip && <InfoDot text={tip} />}</div>;
}

function Section({ title, desc, tip, wide, children }: { title: string; desc?: string; tip?: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <section className={`omaax-settings-section${wide ? ' is-wide' : ''}`}>
      <div className="omaax-section-heading">
        <div><h2>{title}</h2>{desc && <p>{desc}</p>}</div>
        {(tip || desc) && <InfoDot title={title} text={tip || desc} align="right" />}
      </div>
      {children}
    </section>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex items-center justify-between t-body">
      <span>{label}</span>
      <button role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className="relative w-[38px] h-[22px] rounded-full transition-colors" style={{ background: checked ? 'var(--green)' : 'var(--fill-2)' }}>
        <span className="absolute top-[2px] w-[18px] h-[18px] rounded-full bg-white shadow transition-all" style={{ left: checked ? 18 : 2 }} />
      </button>
    </label>
  );
}

interface MigrateSource { id: string; name: string; path: string; skills: string[]; instructions?: string | null; mcpServers: string[] }
function MigrateSection() {
  const { t } = useT();
  const { refresh } = useApp();
  const [sources, setSources] = useState<MigrateSource[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const scan = async () => { try { setSources(await window.biodsh.migrateScan() as MigrateSource[]); } catch { setSources([]); } };
  useEffect(() => { void scan(); }, []);
  const doImport = async (src: MigrateSource) => {
    setBusy(src.id);
    try {
      const r = await window.biodsh.migrateImport(src.id) as { skills: number; instructions: number; skipped: string[] };
      setMsg(t('已从 {name} 导入 {n} 个技能{extra}', { name: src.name, n: r.skills, extra: r.instructions ? t('，并合并了全局说明到工作区 AGENTS.md') : '' }));
      await refresh();
    } catch (e) { setMsg(`${t('导入失败')}: ${String(e).slice(0, 100)}`); }
    finally { setBusy(null); }
  };
  return (
    <Section title={t('导入已有工具')} desc={t('自动查找你在 Claude Code、Codex、OpenCode、Cursor 里已有的技能（SKILL.md）和全局说明（AGENTS.md / CLAUDE.md），复制进 OMaaX，原文件不动。')}>
      {sources === null && <div className="t-caption">{t('正在扫描…')}</div>}
      {sources !== null && sources.length === 0 && <div className="t-body" style={{ color: 'var(--text-2)' }}>{t('这台电脑上没有找到其他工具的技能或说明文件。')}</div>}
      <div className="flex flex-col gap-2">
        {(sources ?? []).map((s) => (
          <div key={s.id} className="flex items-center gap-3 rounded-xl px-3 py-2.5" style={{ background: 'var(--surface-2)' }}>
            <div className="flex-1 min-w-0">
              <div className="t-headline">{s.name}</div>
              <div className="t-caption truncate">{t('{n} 个技能', { n: s.skills.length })}{s.instructions ? ` · ${t('全局说明')}` : ''}{s.mcpServers.length ? ` · ${t('{n} 个 MCP 服务（暂不导入）', { n: s.mcpServers.length })}` : ''}{s.path ? ` · ${s.path}` : ''}</div>
            </div>
            <button className="btn btn-primary" disabled={busy === s.id || (s.skills.length === 0 && !s.instructions)} onClick={() => doImport(s)}>{busy === s.id ? t('导入中…') : t('导入')}</button>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 mt-3"><button className="btn btn-ghost" onClick={scan}><RefreshCw size={13} /> {t('重新扫描')}</button>{msg && <span className="t-caption">{msg}</span>}</div>
    </Section>
  );
}

const IMAGE_PRESETS: { name: string; base: string; model: string; hint: string }[] = [
  { name: '智谱 CogView', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'cogview-4-250304', hint: 'bigmodel.cn 申请 key，国内直连' },
  { name: '通义万相', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'wan2.2-t2i-flash', hint: '阿里云百炼，国内直连' },
  { name: 'SiliconFlow', base: 'https://api.siliconflow.cn/v1', model: 'Kwai-Kolors/Kolors', hint: '硅基流动，国内直连' },
  { name: 'OpenAI', base: 'https://api.openai.com/v1', model: 'gpt-image-1', hint: '需要能访问 OpenAI' },
];

/** 图像生成：给「AI 科研插图生成」技能用的 OpenAI 兼容 images 接口 */
function ImageSection() {
  const { settings, updateSettings, restartDsh } = useApp();
  const { t } = useT();
  const help = useHelp('image');
  const [d, setD] = useState<{ imageBaseUrl?: string; imageModel?: string; imageApiKey?: string }>({});
  const [saved, setSaved] = useState(false);
  if (!settings) return null;
  const cur = { imageBaseUrl: d.imageBaseUrl ?? settings.imageBaseUrl ?? '', imageModel: d.imageModel ?? settings.imageModel ?? '', imageApiKey: d.imageApiKey ?? settings.imageApiKey ?? '' };
  const save = async () => { await updateSettings(cur); setD({}); setSaved(true); setTimeout(() => setSaved(false), 2500); void restartDsh(); };
  return (
    <Section title={help.title} desc={help.summary} tip={help.detail} wide>
      <div className="omaax-provider-row">
        {IMAGE_PRESETS.map((p) => <button key={p.name} className={'btn ' + (cur.imageBaseUrl === p.base ? 'btn-tint' : 'btn-ghost')} title={p.hint} onClick={() => setD({ imageBaseUrl: p.base, imageModel: p.model, imageApiKey: cur.imageApiKey })}>{p.name}</button>)}
      </div>
      <div className="flex flex-col gap-3">
        <label className="omaax-field-label">{t('服务接口地址')}<input className="field t-mono" placeholder="https://…" value={cur.imageBaseUrl} onChange={(e) => setD((x) => ({ ...x, imageBaseUrl: e.target.value }))} /><small>{t('预设会填写示例，请以服务商提供的接口信息为准。')}</small></label>
        <div className="omaax-form-grid">
          <label className="omaax-field-label">{t('图像模型')}<input className="field t-mono" placeholder={t('填写图像模型名称')} value={cur.imageModel} onChange={(e) => setD((x) => ({ ...x, imageModel: e.target.value }))} /></label>
          <label className="omaax-field-label">{t('服务访问密钥')}<input className="field t-mono" type="password" autoComplete="off" placeholder={t('填写对应服务的 API Key')} value={cur.imageApiKey} onChange={(e) => setD((x) => ({ ...x, imageApiKey: e.target.value }))} /></label>
        </div>
        <div className="flex items-center gap-3">
          <button className="btn btn-primary" disabled={Object.keys(d).length === 0} onClick={save}>{saved ? t('已保存') : t('保存图像连接')}</button>
          {settings.imageBaseUrl && <span className="t-caption">{t('已启用：在对话里说「帮我画一张 … 的示意图」即可')}</span>}
        </div>
      </div>
    </Section>
  );
}

const MCP_PRESETS: { name: string; server: McpServer; hint: string }[] = [
  { name: 'Zotero 文献库', hint: '在对话中使用自己的文献库；需打开 Zotero 并启用本地接口。', server: { name: 'zotero', transport: 'stdio', command: 'uv', args: ['tool', 'run', 'zotero-mcp', 'serve'], env: { ZOTERO_LOCAL: 'true' } } },
  { name: '本机文件夹', hint: '让助手使用指定的本机文件夹；在连接参数中填写实际目录。', server: { name: 'files', transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', '.'] } },
  { name: 'PubMed 文献检索', hint: '为对话添加文献检索工具；需要可用的服务和网络。', server: { name: 'pubmed', transport: 'stdio', command: 'uv', args: ['tool', 'run', 'pubmedmcp'] } },
  { name: '已有工具服务', hint: '连接课题组或服务商已经提供的 MCP 服务地址。', server: { name: 'remote', transport: 'streamable-http', url: 'http://localhost:3000/mcp' } },
];

/** MCP 接入：每个服务写成 dsh 的一个 mcp-client 插件实例，工具名 mcp__<name>__<tool> */
function McpSection() {
  const { settings, updateSettings, restartDsh } = useApp();
  const { t } = useT();
  const help = useHelp('mcp');
  const [list, setList] = useState<McpServer[] | null>(null);
  const [dirty, setDirty] = useState(false);
  if (!settings) return null;
  const cur = list ?? settings.mcpServers ?? [];
  const set = (next: McpServer[]) => { setList(next); setDirty(true); };
  const upd = (i: number, p: Partial<McpServer>) => set(cur.map((m, j) => (j === i ? { ...m, ...p } : m)));
  const save = async () => { await updateSettings({ mcpServers: cur.filter((m) => m.name.trim()) }); setList(null); setDirty(false); void restartDsh(); };
  return (
    <Section title={t('外部工具连接（MCP）')} desc={help.summary} tip={help.detail} wide>
      <div className="t-caption mb-2">{t('选择用途，添加连接配置')}</div>
      <div className="omaax-connection-presets">
        {MCP_PRESETS.map((p) => (
          <button key={p.name} className="mcp-preset" onClick={() => set([...cur, { ...p.server, args: [...(p.server.args ?? [])], env: { ...(p.server.env ?? {}) } }])}>
            <span className="flex items-center gap-1.5" style={{ color: 'var(--accent)' }}><Plus size={13} /><span className="t-body" style={{ fontWeight: 600, color: 'var(--text)' }}>{t(p.name)}</span></span>
            <span className="t-caption">{t(p.hint)}</span>
          </button>
        ))}
      </div>
      {cur.length === 0 && (
        <div className="rounded-xl px-4 py-6 flex flex-col items-center gap-1" style={{ border: '1px dashed var(--border)' }}>
          <Plug size={20} style={{ color: 'var(--text-3)' }} />
          <div className="t-body" style={{ color: 'var(--text-2)' }}>{t('还没有外部工具连接')}</div>
          <div className="t-caption">{t('按需要添加；不配置也能使用 OMaaX 的其他功能。')}</div>
        </div>
      )}
      <div className="flex flex-col gap-2">
        {cur.map((m, i) => (
          <div key={i} className="p-3 rounded-xl flex flex-col gap-2" style={{ background: 'var(--surface-2)' }}>
            <div className="omaax-connection-heading">
              <input className="field t-mono !w-[140px]" placeholder={t('名称（字母数字）')} value={m.name} onChange={(e) => upd(i, { name: e.target.value.replace(/[^A-Za-z0-9_-]/g, '') })} />
              <div className="seg">
                {(['stdio', 'streamable-http'] as const).map((k) => <button key={k} className={m.transport === k ? 'active' : ''} onClick={() => upd(i, { transport: k })}>{k === 'stdio' ? t('本机启动') : t('服务地址')}</button>)}
              </div>
              <label className="flex items-center gap-1.5 t-caption ml-auto"><input type="checkbox" checked={m.enabled !== false} onChange={(e) => upd(i, { enabled: e.target.checked })} /> {t('启用')}</label>
              <button className="btn btn-ghost !px-2" aria-label={t('移除此连接')} title={t('移除此连接')} onClick={() => set(cur.filter((_, j) => j !== i))}><Trash2 size={13} /></button>
            </div>
            {m.transport === 'streamable-http'
              ? <label className="omaax-field-label">{t('服务地址')}<input className="field t-mono" placeholder="http://…/mcp" value={m.url ?? ''} onChange={(e) => upd(i, { url: e.target.value })} /></label>
              : <div className="omaax-form-grid">
                  <label className="omaax-field-label">{t('启动程序')}<input className="field t-mono" placeholder={t('例如 uv 或 npx')} value={m.command ?? ''} onChange={(e) => upd(i, { command: e.target.value })} /></label>
                  <label className="omaax-field-label">{t('启动参数')}<input className="field t-mono" placeholder={t('按服务说明填写，以空格分隔')} value={(m.args ?? []).join(' ')} onChange={(e) => upd(i, { args: e.target.value.split(/\s+/).filter(Boolean) })} /></label>
                </div>}
            {m.transport !== 'streamable-http' && <input className="field t-mono" placeholder={t('环境变量（可选）：KEY=value KEY2=value2')} value={Object.entries(m.env ?? {}).map(([k, v]) => `${k}=${v}`).join(' ')} onChange={(e) => upd(i, { env: Object.fromEntries(e.target.value.split(/\s+/).filter((x) => x.includes('=')).map((x) => [x.slice(0, x.indexOf('=')), x.slice(x.indexOf('=') + 1)])) })} />}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 mt-3">
        <button className="btn btn-primary" disabled={!dirty} onClick={save}>{t('保存工具连接')}</button>
        <span className="t-caption">{t('保存后会重启助手，使新连接生效。')}</span>
      </div>
    </Section>
  );
}
