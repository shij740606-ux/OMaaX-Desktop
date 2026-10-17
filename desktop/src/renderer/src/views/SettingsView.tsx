import { useEffect, useState } from 'react';
import { RefreshCw, Plug, Plus, Trash2, SlidersHorizontal, Palette, Info, ChevronRight } from 'lucide-react';
import InfoDot, { useHelp } from '../components/HelpTip';
export { default as InfoDot } from '../components/HelpTip';
import AccountView from './AccountView';
import ModelUsage from '../components/ModelUsage';
import { ExternalLink, FolderOpen, KeyRound, CreditCard } from 'lucide-react';
import {hasUnit,useAuth} from '../AuthGate';
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
  { id: 'account', label: '账号和用量', detail: '账户信息与模型使用情况', icon: CreditCard },
  { id: 'model', label: '对话模型', detail: '连接方式与访问密钥', icon: KeyRound },
  { id: 'tools', label: '工具与服务', detail: '分析工具使用说明', icon: Plug },
  { id: 'workspace', label: '文件与软件', detail: '保存位置与软件下载', icon: FolderOpen },
  { id: 'appearance', label: '界面偏好', detail: '主题与显示语言', icon: Palette },
  { id: 'about', label: '关于 OMaaX', detail: '版本、引导与许可', icon: Info },
] as const;
type SettingsPane = typeof SETTINGS_PANES[number]['id'];

export default function SettingsView({initialPane='model'}:{initialPane?:SettingsPane}={}) {
  const { settings, updateSettings, credential, saveKey, info, restartDsh } = useApp();
  const { t } = useT();
  const bioflow=hasUnit(useAuth().user,'bioflow');
  const [pane, setPane] = useState<SettingsPane>(initialPane);
  const [key, setKey] = useState('');
  const offline = settings?.mode === 'offline';
  const modelHelp = useHelp('model');
  const [draft, setDraft] = useState<{ offlineBaseUrl?: string; offlineModel?: string; offlineApiKey?: string }>({});
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
          {pane==='account'&&<div className="flex flex-col gap-4 p-5" role="tabpanel" id="settings-pane-account" aria-labelledby="settings-tab-account"><ModelUsage/><AccountView embedded/></div>}
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
          <div className="omaax-settings-content" data-pane="tools" role="tabpanel" id="settings-pane-tools" aria-labelledby="settings-tab-tools" hidden={pane!=='tools'} tabIndex={0}><Section title="工具与服务" desc="分析工具请在“工具中心”浏览，并在对话中说明分析需求。"><p className="t-caption">图像生成与外部应用连接暂不可用。</p></Section></div>
          <div className="omaax-settings-content" data-pane="workspace" role="tabpanel" id="settings-pane-workspace" aria-labelledby="settings-tab-workspace" hidden={pane!=='workspace'} tabIndex={0}>          <Section title={t('本机工作目录')} desc={t('本机项目和分析文件的默认保存位置。')} tip={t('请选择当前账号文件夹中的目录。更改位置不会移动已有文件。服务器上的分析文件请在“服务器管理”中查看。')}>
            <div className="flex items-center gap-3">
              <div className="omaax-workspace-label"><FolderOpen size={20}/><div><strong>{t('本机项目文件夹')}</strong><small>{t('默认保存本机项目和分析文件，可直接打开查看。')}</small></div></div>
              <button className="btn btn-fill" disabled={!bioflow} onClick={async () => { const p = await window.biodsh.pickFolder(); if (p) { await updateSettings({ workspace: p }); void restartDsh(); } }}>{t('更改…')}</button>
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
              {(['system', 'zh', 'en'] as const).map((k) => <button key={k} className={(settings.language ?? 'system') === k ? 'active' : ''} onClick={() => updateSettings({ language: k })}>{{ system: t('跟随系统'), zh: '中文', en: 'English' }[k]}</button>)}
            </div>
          </Section>

</div>
          <div className="omaax-settings-content" data-pane="about" role="tabpanel" id="settings-pane-about" aria-labelledby="settings-tab-about" hidden={pane!=='about'} tabIndex={0}>          <Section title="关于 OMaaX" wide><div className="omaax-wordmark">OMaa<span>X</span></div><p className="t-body mt-2">One Model, Beyond the Max.</p><p className="t-caption mt-2">连接数据、工具与计算资源的智能分析工作台。</p><div className="flex gap-3 mt-4"><button className="btn btn-tint" onClick={()=>info&&window.biodsh.openPath(info.paths.root)}><FolderOpen size={14}/>打开数据目录</button><button className="btn btn-ghost" onClick={()=>updateSettings({onboarded:false})}>使用引导</button></div><details className="mt-4 t-caption"><summary>版本与许可说明</summary><p className="mt-2">OMaaX {info?.version} · 助手组件 {dshVersion}</p><p>基于开源组件构建。原始版权和 MIT 许可证随软件保留。</p><p>应用文件由 OMaaX 管理，可通过“打开数据目录”查看。</p></details></Section></div>

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
