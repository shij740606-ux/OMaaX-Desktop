import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Plug, Plus, Trash2, Palette, Info, ChevronRight } from 'lucide-react';
import InfoDot, { useHelp } from '../components/HelpTip';
export { default as InfoDot } from '../components/HelpTip';
import AccountView from './AccountView';
import AboutHelp from './AboutHelp';
import OmaaxWordmark from '../components/OmaaxWordmark';
import ModelUsage from '../components/ModelUsage';
import { ExternalLink, FolderOpen, CreditCard } from 'lucide-react';
import {useAuth} from '../AuthGate';
import { useApp } from '../store';
import type { McpServer } from '@shared/types';
import { useT } from '../i18n';

const SETTINGS_PANES = [
  { id: 'account', label: '账号和用量', detail: '账户信息与模型使用情况', icon: CreditCard },
  { id: 'tools', label: '工具与服务', detail: '分析工具使用说明', icon: Plug },
  { id: 'workspace', label: '文件与软件', detail: '保存位置与软件下载', icon: FolderOpen },
  { id: 'appearance', label: '界面偏好', detail: '主题与显示语言', icon: Palette },
  { id: 'about', label: '关于 OMaaX', detail: '帮助、版本与许可', icon: Info },
] as const;
type SettingsPane = typeof SETTINGS_PANES[number]['id'];

export default function SettingsView({initialPane='account'}:{initialPane?:SettingsPane}={}) {
  const { settings, updateSettings, info, restartDsh } = useApp();
  const { t } = useT();
  const auth=useAuth();
  const [pane, setPane] = useState<SettingsPane>(initialPane);
  const dshVersion = (info as { dshVersion?: string } | null)?.dshVersion ?? '?';
  if (!settings) return null;
  return (
    <div className="h-full flex flex-col omaax-page omaax-settings">
      <header className="omaax-settings-topbar drag"><div><h1>{t('设置')}</h1><p>{t('让 OMaaX 适合你的工作方式。')}</p></div><span className="omaax-settings-version">OMaaX {info?.version}</span></header>
      <div className="omaax-settings-layout">
        <nav className="omaax-settings-nav" role="tablist" aria-label={t('设置分类')} aria-orientation="vertical">
          {SETTINGS_PANES.map(({id,label,detail,icon:Icon},index)=><button type="button" role="tab" id={'settings-tab-'+id} aria-selected={pane===id} aria-controls={'settings-pane-'+id} tabIndex={pane===id?0:-1} className={pane===id?'is-active':''} key={id} onClick={()=>setPane(id)} onKeyDown={e=>{let next=index;if(e.key==='ArrowDown')next=(index+1)%SETTINGS_PANES.length;else if(e.key==='ArrowUp')next=(index-1+SETTINGS_PANES.length)%SETTINGS_PANES.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=SETTINGS_PANES.length-1;else return;e.preventDefault();setPane(SETTINGS_PANES[next].id);document.getElementById('settings-tab-'+SETTINGS_PANES[next].id)?.focus();}}><span className="omaax-settings-nav-icon"><Icon size={18}/></span><span><strong>{t(label)}</strong><small>{t(detail)}</small></span><ChevronRight size={14}/></button>)}
        </nav>
        <div className="omaax-settings-panel">
          {pane==='account'&&<div className="flex flex-col gap-4 p-5" role="tabpanel" id="settings-pane-account" aria-labelledby="settings-tab-account">{!auth.localAccess&&<Section title={t("当前账号")}><div className="flex flex-wrap items-center gap-3"><span>{auth.user?.username}</span>{auth.user?.role==='admin'&&<button className="btn btn-ghost" onClick={()=>void window.biodsh.hubAdmin().catch(e=>window.alert(String(e)))}>{t("管理后台")}</button>}<button className="btn btn-fill" onClick={()=>void auth.logout?.()}>{t("退出 / 切换账号")}</button></div></Section>}<ModelUsage/><AccountView embedded/></div>}
          <div className="omaax-settings-content" data-pane="tools" role="tabpanel" id="settings-pane-tools" aria-labelledby="settings-tab-tools" hidden={pane!=='tools'} tabIndex={0}><ImageSection/><McpSection/></div>
          <div className="omaax-settings-content" data-pane="workspace" role="tabpanel" id="settings-pane-workspace" aria-labelledby="settings-tab-workspace" hidden={pane!=='workspace'} tabIndex={0}>          <Section title={t('本机工作目录')} desc={t('本机项目和分析文件的默认保存位置。')} tip={t('可选择本机有权限访问的文件夹。更改位置不会移动已有文件。服务器上的分析文件请在“服务器管理”中查看。')}>
            <div className="flex items-center gap-3">
              <div className="omaax-workspace-label"><FolderOpen size={20}/><div><strong>{t('本机项目文件夹')}</strong><small>{t('默认保存本机项目和分析文件，可直接打开查看。')}</small></div></div>
              <button className="btn btn-fill" onClick={async () => { const p = await window.biodsh.pickFolder(); if (p) { await updateSettings({ workspace: p }); void restartDsh(); } }}>{t('更改…')}</button>
              <button className="btn btn-ghost" aria-label={t('打开工作目录')} onClick={() => window.biodsh.openPath(settings.workspace)}><FolderOpen size={13} />{t('打开')}</button>
            </div>
          </Section>

          <Section title={t('软件下载')} desc={t('按网络情况选择 Python 和软件包的下载来源。')} tip={t('国内镜像可能提高下载成功率；实际速度取决于网络和镜像服务。此设置不会自动下载工具，也不会把本机环境同步到服务器。')}>
            <Toggle checked={settings.useChinaMirror} onChange={(v) => updateSettings({ useChinaMirror: v })} label={t('使用国内镜像')} />
          </Section>

</div>
          <div className="omaax-settings-content" data-pane="appearance" role="tabpanel" id="settings-pane-appearance" aria-labelledby="settings-tab-appearance" hidden={pane!=='appearance'} tabIndex={0}>          <Section title={t('外观')}>
            <div className="seg">
              {(['system', 'light', 'dark'] as const).map((k) => <button key={k} className={settings.theme === k ? 'active' : ''} onClick={() => updateSettings({ theme: k })}>{t({ system: '跟随系统', light: '浅色', dark: '深色' }[k])}</button>)}
            </div>
          </Section>

          <Section title={t('语言 / Language')} desc={t('界面与智能体界面一起切换；智能体回答会跟随你提问的语言。')}>
            <div className="seg">
              {(['system', 'zh', 'en'] as const).map((k) => <button key={k} className={(settings.language ?? 'system') === k ? 'active' : ''} onClick={() => updateSettings({ language: k })}>{{ system: t('跟随系统'), zh: t('中文'), en: 'English' }[k]}</button>)}
            </div>
          </Section>

</div>
          <div className="omaax-settings-content" data-pane="about" role="tabpanel" id="settings-pane-about" aria-labelledby="settings-tab-about" hidden={pane!=='about'} tabIndex={0}>          <Section title={t("关于 OMaaX")} wide><OmaaxWordmark className="omaax-wordmark--about" /><p className="t-body mt-2">One Model, Beyond the Max.</p><p className="t-caption mt-2">{t("连接数据、工具与计算资源的智能分析工作台。")}</p><div className="flex gap-3 mt-4"><button className="btn btn-tint" onClick={()=>info&&window.biodsh.openPath(info.paths.root)}><FolderOpen size={14}/>{t("打开数据目录")}</button><button className="btn btn-ghost" onClick={()=>updateSettings({onboarded:false})}>{t("使用引导")}</button></div><details className="mt-4 t-caption"><summary>{t("版本与许可说明")}</summary><p className="mt-2">OMaaX {info?.version} · {t("助手组件")} {dshVersion}</p><p>{t("基于开源组件构建。原始版权和 MIT 许可证随软件保留。")}</p><p>{t("应用文件由 OMaaX 管理，可通过“打开数据目录”查看。")}</p></details><AboutHelp/></Section></div>

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
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  if (!settings) return null;
  const cur = { imageBaseUrl: d.imageBaseUrl ?? settings.imageBaseUrl ?? '', imageModel: d.imageModel ?? settings.imageModel ?? '', imageApiKey: d.imageApiKey ?? settings.imageApiKey ?? '' };
  const save = async () => { setBusy(true);setError('');try {await updateSettings(cur);await restartDsh();setD({});setSaved(true);}catch {setError('保存或连接失败，请检查配置后重试');}finally{setBusy(false);} };
  return (
    <Section title={help.title} desc={help.summary} tip={help.detail} wide>
      <div className="omaax-provider-row">
        {IMAGE_PRESETS.map((p) => <button key={p.name} className={'btn ' + (cur.imageBaseUrl === p.base ? 'btn-tint' : 'btn-ghost')} title={t(p.hint)} onClick={() => setD({ imageBaseUrl: p.base, imageModel: p.model, imageApiKey: cur.imageApiKey })}>{t(p.name)}</button>)}
      </div>
      <div className="flex flex-col gap-3">
        <label className="omaax-field-label">{t('服务接口地址')}<input className="field t-mono" placeholder="https://…" value={cur.imageBaseUrl} onChange={(e) => setD((x) => ({ ...x, imageBaseUrl: e.target.value }))} /><small>{t('预设会填写示例，请以服务商提供的接口信息为准。')}</small></label>
        <div className="omaax-form-grid">
          <label className="omaax-field-label">{t('图像模型')}<input className="field t-mono" placeholder={t('填写图像模型名称')} value={cur.imageModel} onChange={(e) => setD((x) => ({ ...x, imageModel: e.target.value }))} /></label>
          <label className="omaax-field-label">{t('服务访问密钥')}<input className="field t-mono" type="password" autoComplete="off" placeholder={t('填写对应服务的 API Key')} value={cur.imageApiKey} onChange={(e) => setD((x) => ({ ...x, imageApiKey: e.target.value }))} /></label>
        </div>
        <div className="flex items-center gap-3">
          <button className="btn btn-primary" disabled={busy||Object.keys(d).length === 0} onClick={save}>{saved ? t('已保存') : t('保存图像连接')}</button>
          {error&&<p role="alert">{t(error)}</p>}{settings.imageBaseUrl && <span className="t-caption">{t('已配置：在普通对话中描述需要生成的图片')}</span>}
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
  const [formRevision,setFormRevision]=useState(0);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    form.current?.querySelectorAll<HTMLInputElement>('input[data-validation-key]').forEach(input => {
      if (input.validity.customError) input.setCustomValidity(t(input.dataset.validationKey!));
    });
  }, [t]);
  if (!settings) return null;
  const cur = list ?? settings.mcpServers ?? [];
  const set = (next: McpServer[]) => { setList(next); setDirty(true); };
  const upd = (i: number, p: Partial<McpServer>) => set(cur.map((m, j) => (j === i ? { ...m, ...p } : m)));
  const save = async () => {setBusy(true);setError('');try {await updateSettings({mcpServers:cur});await restartDsh();setList(null);setDirty(false);}catch {setError('保存或连接失败，请检查名称是否重复、地址和启动参数是否正确后重试');}finally{setBusy(false);}};
  return (
    <Section title={t('外部工具连接（MCP）')} desc={help.summary} tip={help.detail} wide>
      <form ref={form} key={formRevision} onSubmit={e=>{e.preventDefault();void save();}}>
      <div className="t-caption mb-2">{t('仅添加可信服务。外部工具每次执行需确认，请核对操作及参数；不用于绕过服务器删除保护。')}</div>
      <div className="omaax-connection-presets">
        {MCP_PRESETS.map((p) => (
          <button type="button" key={p.name} className="mcp-preset" onClick={() => set([...cur, { ...p.server, args: [...(p.server.args ?? [])], env: { ...(p.server.env ?? {}) } }])}>
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
                {(['stdio', 'streamable-http'] as const).map((k) => <button type="button" key={k} className={m.transport === k ? 'active' : ''} onClick={() => upd(i, { transport: k })}>{k === 'stdio' ? t('本机启动') : t('服务地址')}</button>)}
              </div>
              <label className="flex items-center gap-1.5 t-caption ml-auto"><input type="checkbox" checked={m.enabled !== false} onChange={(e) => upd(i, { enabled: e.target.checked })} /> {t('启用')}</label>
              <button type="button" className="btn btn-ghost !px-2" aria-label={t('移除此连接')} title={t('移除此连接')} onClick={() => {set(cur.filter((_, j) => j !== i));setFormRevision(v=>v+1);}}><Trash2 size={13} /></button>
            </div>
            {m.transport === 'streamable-http'
              ? <label className="omaax-field-label">{t('服务地址')}<input className="field t-mono" placeholder="http://…/mcp" value={m.url ?? ''} onChange={(e) => upd(i, { url: e.target.value })} /></label>
              : <div className="omaax-form-grid">
                  <label className="omaax-field-label">{t('启动程序')}<input className="field t-mono" placeholder={t('例如 uv 或 npx')} value={m.command ?? ''} onChange={(e) => upd(i, { command: e.target.value })} /></label>
                  <label className="omaax-field-label">{t('启动参数')}<input className="field t-mono" placeholder={t('JSON 数组，例如 ["--port", "3000"]')} data-validation-key="请输入字符串 JSON 数组" defaultValue={JSON.stringify(m.args??[])} onChange={e=>{try{const value=JSON.parse(e.target.value);if(!Array.isArray(value)||!value.every(v=>typeof v==='string'))throw Error();upd(i,{args:value});e.target.setCustomValidity('');}catch{e.target.setCustomValidity(t('请输入字符串 JSON 数组'));e.target.reportValidity();}}} /></label>
                </div>}
            {m.transport !== 'streamable-http' && <label className="omaax-field-label">{t("环境变量（JSON 对象）")}<input className="field t-mono" type="password" autoComplete="off" data-validation-key="请输入字符串值的 JSON 对象" defaultValue={JSON.stringify(m.env??{})} onChange={e=>{try{const value=JSON.parse(e.target.value);if(!value||Array.isArray(value)||typeof value!=='object'||!Object.values(value).every(v=>typeof v==='string'))throw Error();upd(i,{env:value});e.target.setCustomValidity('');}catch{e.target.setCustomValidity(t('请输入字符串值的 JSON 对象'));}}}/></label>}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 mt-3">
        <button type="submit" className="btn btn-primary" disabled={busy||!dirty}>{t('保存工具连接')}</button>
        <span className="t-caption">{t('保存后会重启助手，使新连接生效。')}</span>{error&&<p role="alert">{t(error)}</p>}
      </div>
    </form></Section>
  );
}
