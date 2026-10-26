import {serverDisplayMessage} from '../serverDisplay';
import { Check, Download, FolderOpen, RefreshCw } from 'lucide-react';
import { PackIcon } from '../icons';
import { CheckCircle2, Package as PackageIcon } from 'lucide-react';
import { useApp } from '../store';
import { useT } from '../i18n';
import HelpTip, { useHelp } from '../components/HelpTip';

const STEPS: { id: string; label: string }[] = [
  { id: 'python', label: '下载 Python 3.12' },
  { id: 'venv', label: '创建独立环境' },
  { id: 'packages', label: '安装分析软件包（scanpy 等）' },
  { id: 'verify', label: '验证' },
];
const ORDER = ['idle', 'python', 'venv', 'packages', 'verify', 'ready'];

export default function EnvView() {
  const { env, installEnv, info, refresh } = useApp();
  const { t } = useT();
  const help = useHelp('env');
  const installing = ['python', 'venv', 'packages', 'verify'].includes(env.step);
  const idx = ORDER.indexOf(env.step);
  return (
    <div className="h-full flex flex-col omaax-page omaax-environment">
      <header className="omaax-hero drag"><span className="omaax-eyebrow">OMaaX / COMPUTE</span><h1>{t("计算环境")}<HelpTip topic="env"/></h1><p>{help.summary}</p><button className="btn btn-tint" aria-label={t("刷新环境状态")} onClick={()=>refresh()}><RefreshCw size={14}/>{t("检查状态")}</button></header>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="omaax-env-grid">
          <aside className="omaax-guide omaax-env-guide"><strong>{t('按运行位置准备环境')}</strong><p>{t('在本机分析：安装本页组件。在服务器分析：选择服务器后按流程准备。两边的环境不会自动共用。')}</p></aside>
          <div className="card p-6 flex flex-wrap items-center gap-5 rise omaax-env-status">
            <div className="w-[56px] h-[56px] rounded-2xl flex items-center justify-center text-[28px]" style={{ background: env.ready ? 'rgba(52,199,89,.14)' : 'var(--surface-2)' }}>{env.ready ? <CheckCircle2 size={28} strokeWidth={1.6} style={{ color: 'var(--green,#34c759)' }} /> : <PackageIcon size={28} strokeWidth={1.6} style={{ color: 'var(--text-3)' }} />}</div>
            <div className="flex-1 min-w-0">
              <div className="t-title2">{env.ready ? t('计算环境已就绪') : installing ? t('正在准备计算环境…') : env.step === 'error' ? t('安装没有成功') : t('还没有安装计算环境')}</div>
              <div className="t-body mt-1" style={{ color: env.step === 'error' ? 'var(--red)' : 'var(--text-2)' }}>
                {env.ready ? `Python ${env.pythonVersion} · scanpy ${env.packages?.scanpy}` : installing ? serverDisplayMessage(t,env.message || '') : env.step === 'error' ? serverDisplayMessage(t,env.message || '') : t('本机基础环境包含 Python、scanpy 等常用软件。需要在这台电脑分析时再安装，不会安装全部工具。')}
              </div>
              {installing && <div className="mt-3 h-[6px] rounded-full overflow-hidden" style={{ background: 'var(--fill)' }}><div className="h-full rounded-full transition-all" style={{ width: `${Math.round(env.progress * 100)}%`, background: 'var(--accent)' }} /></div>}
            </div>
            {!env.ready && !installing && <button className="btn btn-primary btn-lg" onClick={() => installEnv()}><Download size={14} /> {env.step === 'error' ? t('重试') : t('准备本机环境')}</button>}
            {env.ready && <button className="btn btn-fill" onClick={() => installEnv()}>{t('修复 / 重新安装')}</button>}
          </div>

          <details className="card p-5 rise omaax-env-details" open={installing || env.step === 'error'}>
            <summary className="t-headline cursor-pointer">{t('安装进度')}</summary>
            <div className="omaax-steps">
              {STEPS.map((s, i) => {
                const si = ORDER.indexOf(s.id);
                const done = env.ready || idx > si;
                const active = env.step === s.id;
                return (
                  <div key={s.id} className="flex items-center gap-3 t-body">
                    <span className="w-[20px] h-[20px] rounded-full flex items-center justify-center text-[11px] font-semibold" style={{ background: done ? 'var(--green)' : active ? 'var(--accent)' : 'var(--fill)', color: done || active ? '#fff' : 'var(--text-2)' }}>{done ? <Check size={12} strokeWidth={3} /> : active ? <span className="ring spin" style={{ ['--p' as string]: 30, width: 12, height: 12 }} /> : i + 1}</span>
                    <span style={{ color: done || active ? 'var(--text)' : 'var(--text-2)' }}>{t(s.label)}</span>
                  </div>
                );
              })}
            </div>
          </details>

          {env.ready && env.packages && (
            <details className="card p-5 rise omaax-env-details">
              <summary className="t-headline cursor-pointer">{t('已安装的软件与版本')}</summary>
              <div className="omaax-package-list">
                {Object.entries(env.packages).map(([k, v]) => <div key={k} className="t-mono px-2.5 py-1.5 rounded-lg flex justify-between" style={{ background: 'var(--surface-2)' }}><span>{k}</span><span style={{ color: 'var(--text-2)' }}>{v}</span></div>)}
              </div>
              <div className="flex gap-2 mt-4">
                <button className="btn btn-ghost" onClick={() => info && window.biodsh.openPath(info.paths.bioenv)}><FolderOpen size={13} /> {t('打开环境文件夹')}</button>
              </div>
            </details>
          )}

          {env.ready && (
            <div className="card p-5 rise omaax-env-extras">
              <div className="omaax-section-heading"><div><h2>{t('按研究需要添加组件')}</h2><p>{t('仅安装你选择的软件包；添加工具说明与安装软件是两件事。')}</p></div><HelpTip topic="env" align="right"/></div>
              <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
                {[
                  { name: "单细胞进阶", desc: "细胞类型自动注释、双细胞去除、批次整合、通路活性", pkgs: ['celltypist', 'scrublet', 'harmonypy', 'decoupler'] },
                  { name: 'Bulk RNA-seq', desc: "差异表达（DESeq2 的 Python 实现）、富集分析", pkgs: ['pydeseq2', 'gseapy'] },
                  { name: "空间转录组", desc: "Squidpy 空间分析", pkgs: ['squidpy'] },
                  { name: "统计与临床", desc: "生存分析、统计检验、SPSS/R 文件读写", pkgs: ['lifelines', 'pingouin', 'pyreadstat', 'pyreadr', 'openpyxl'] },
                  { name: "数据库访问", desc: "NCBI / Ensembl / UniProt 等接口客户端", pkgs: ['biopython', 'pybiomart', 'requests', 'mygene'] },
                  { name: "作图增强", desc: "交互图、韦恩图、火山图工具", pkgs: ['plotly', 'matplotlib-venn', 'adjustText', 'upsetplot'] },
                  { name: "电脑控制", desc: "让智能体操作电脑：找窗口、读屏幕文字、点击输入，把数据贴进 Excel / SPSS / Prism", pkgs: ['pyautogui', 'pywinauto', 'mss', 'pyperclip', 'pillow', 'rapidocr-onnxruntime'] },
                  { name: "文献与格式桥接", desc: "PDF 全文提取、Zotero 联动、SPSS/R/Stata 文件读写", pkgs: ['pymupdf', 'pyreadstat', 'pyreadr', 'openpyxl', 'habanero'] },
                ].map((g) => (
                  <div key={g.name} className="rounded-xl p-3 flex flex-col gap-1.5" style={{ background: 'var(--surface-2)' }}>
                    <div className="t-headline flex items-center gap-2"><PackIcon name={g.name} size={18} /> {t(g.name)}</div>
                    <div className="t-caption">{t(g.desc)}</div>
                    <details className="t-caption"><summary className="cursor-pointer">{t("包含的软件")}</summary><div className="t-mono mt-2">{g.pkgs.join(' · ')}</div></details>
                    <button className="btn btn-tint mt-1 self-start" disabled={['python', 'venv', 'packages', 'verify'].includes(env.step)} onClick={() => { void window.biodsh.envInstallExtra(g.pkgs); }}><Download size={13} /> {t('安装')}</button>
                  </div>
                ))}
              </div>
            </div>
          )}
          {env.log.length > 0 && (
            <details className="rise">
              <summary className="t-caption cursor-pointer">{t('安装日志')}</summary>
              <pre className="log mt-2 max-h-[280px]">{env.log.slice(-200).join('\n')}</pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
