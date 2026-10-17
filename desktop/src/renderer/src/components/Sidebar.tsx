import {useAuth,hasUnit} from '../AuthGate';
import omaaxIcon from '../assets/omaax-icon.svg';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SquarePen, Plus, Cpu, Store, Settings, Minus, Square, X, Folder, FolderPlus, GitBranch, GitCommitHorizontal, ChevronRight, ChevronDown, Wallet, MessageSquare, RefreshCw, Pin, Pencil, Archive, Share2, Trash2, FolderOpen, Sparkles, Loader2, Server, CreditCard, GraduationCap } from 'lucide-react';
import { useApp, type Tab } from '../store';
import { Database, Globe } from 'lucide-react';
import ContextMenu, { type MenuState } from './ContextMenu';
import { useT } from '../i18n';

interface Workspace { workspaceId: string; path: string; title: string; sessionIds: string[]; updatedAt: string }
interface SessionRow { sessionId: string; updatedAt: number; running: boolean; blank: boolean; cwd?: string; projections?: { values: Record<string, unknown> } }
interface GitInfo { available: boolean; isRepo: boolean; branch?: string; dirty: number; lastCommit?: string }
interface Balance { total?: string; granted?: string; topped?: string; currency?: string; error?: string }

function sessionTitle(s: SessionRow): string {
  const t = s.projections?.values?.title;
  return typeof t === 'string' && t.trim() ? t : (s.blank ? '新对话' : '未命名对话');
}
function sessionTokens(s: SessionRow): number {
  const u = s.projections?.values?.tokenUsage as { uncachedInputTokens?: number; outputTokens?: number; cacheReadTokens?: number } | undefined;
  return u ? (u.uncachedInputTokens ?? 0) + (u.outputTokens ?? 0) + (u.cacheReadTokens ?? 0) : 0;
}
const fmtTokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));
const loadPins = (): string[] => { try { return JSON.parse(localStorage.getItem('biodsh.pins') ?? '[]'); } catch { return []; } };

type SidebarOrders = Record<string, string[]>;
const loadOrders = (): SidebarOrders => { try {
  const value = JSON.parse(localStorage.getItem('biodsh.sidebar-orders.v1') || '{}');
  const orders = Object.fromEntries(Object.entries(value).filter(([, ids]) => Array.isArray(ids) && ids.every(id => typeof id === 'string'))) as SidebarOrders;
  if (orders.nav?.length && !orders.nav.includes('workflows')) {
    const afterAnalysis = orders.nav.indexOf('data');
    orders.nav.splice(afterAnalysis < 0 ? orders.nav.length : afterAnalysis + 1, 0, 'workflows');
  }
  return orders;
} catch { return {}; } };
function ordered<T>(items: T[], ids: string[], key: (item: T) => string): T[] {
 const ranks = new Map(ids.map((id, index) => [id, index]));
 return [...items].sort((a, b) => (ranks.get(key(a)) ?? ids.length) - (ranks.get(key(b)) ?? ids.length));
}
export default function Sidebar() {
  const { tab, setTab, env, dsh, info, settings, restartDsh, currentSession: current, setCurrentSession: setCurrent } = useApp();
  const {user}=useAuth();
  const bioflow=hasUnit(user,"bioflow"), chat=hasUnit(user,"qa")||bioflow;
  const offline = settings?.mode === 'offline';
  const { t, lang } = useT();
  const api = window.biodsh;
  const isMac = info?.platform === 'darwin';
  const [orders, setOrders] = useState<SidebarOrders>(loadOrders);
  const dragSource = useRef<{ group: string; id: string } | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const sortable = (group: string, id: string, ids: string[]) => ({
    draggable: true,
    onDragStart: (event: React.DragEvent) => { event.stopPropagation(); dragSource.current = {group, id}; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', id); },
    onDragOver: (event: React.DragEvent) => { if (dragSource.current?.group !== group) return; event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'move'; setDropTarget(group + ':' + id); },
    onDragLeave: () => setDropTarget(null),
    onDragEnd: () => { dragSource.current = null; setDropTarget(null); },
    onDrop: (event: React.DragEvent) => {
      event.stopPropagation(); const source = dragSource.current;
      if (!source || source.group !== group) return;
      event.preventDefault(); dragSource.current = null; setDropTarget(null);
      if (source.id === id) return;
      const next = ids.filter(value => value !== source.id);
      const rect = event.currentTarget.getBoundingClientRect();
      next.splice(next.indexOf(id) + (event.clientY >= rect.top + rect.height / 2 ? 1 : 0), 0, source.id);
      setOrders(previous => { const value = {...previous, [group]: next}; try { localStorage.setItem('biodsh.sidebar-orders.v1', JSON.stringify(value)); } catch { toast('排序保存失败'); } return value; });
    },
    'data-sort-target': dropTarget === group + ':' + id ? 'true' : undefined,
  });
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [sessions, setSessions] = useState<Record<string, SessionRow>>({});
  const [archived, setArchived] = useState<Set<string>>(new Set());
  const [git, setGit] = useState<Record<string, GitInfo>>({});
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [pins, setPins] = useState<string[]>(loadPins);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [usageOpen, setUsageOpen] = useState(false);
  const refreshPending=useRef(false);

  const toast = (m: string) => { setNotice(m); setTimeout(() => setNotice(null), 4000); };
  const savePins = (p: string[]) => { setPins(p); try { localStorage.setItem('biodsh.pins', JSON.stringify(p)); } catch { /* */ } };

  const refresh = useCallback(async (manual = false) => {
    if(refreshPending.current)return;
    if (dsh.state !== 'running') { if (manual) toast(t('智能体还没运行')); return; }
    refreshPending.current=true;
    if (manual) setRefreshing(true);
    try {
      const [ws, ss] = await Promise.all([
        api.dshRpc('workspace.list') as Promise<{ items: Workspace[]; archivedSessionIds: string[] }>,
        api.dshRpc('session.list') as Promise<{ items: SessionRow[] }>,
      ]);
      const demoRoot = `${info?.paths.root.replace(/\\/g, '/').replace(/\/$/, '')}/demos/`; const visible = ws.items.filter(w => !w.path.replace(/\\/g, '/').startsWith(demoRoot)); setWorkspaces(visible); setArchived(new Set(ws.archivedSessionIds));
      // 告诉 dsh 视图「工作区标题 → 路径」，对话里提到的图片文件才能拼出地址显示缩略图
      try { await api.dshSetContext(Object.fromEntries(ws.items.map((w) => [w.title, w.path]))); } catch { /* electron 版无此功能 */ }
      setSessions(Object.fromEntries(ss.items.map((s) => [s.sessionId, s])));
      const g: Record<string, GitInfo> = {};
      await Promise.all(ws.items.map(async (w) => { g[w.workspaceId] = (await api.gitStatus(w.path)) as GitInfo; }));
      setGit(g);
    } catch (e) { if (manual) toast(t('刷新失败：{msg}', { msg: String(e).slice(0, 80) })); }
    finally { refreshPending.current=false;if (manual) setTimeout(() => setRefreshing(false), 400); }
  }, [dsh.state, lang, info?.paths.root]);

  useEffect(() => { void refresh(); const timer = setInterval(() => { void refresh(); }, 5000); return () => clearInterval(timer); }, [refresh]);
  useEffect(() => {
    let alive = true;
    const load = () => api.deepseekBalance().then((b: { balance_infos?: { currency: string; total_balance: string; granted_balance: string; topped_up_balance: string }[] }) => {
      if (!alive) return; const i = b.balance_infos?.[0]; setBalance(i ? { total: i.total_balance, granted: i.granted_balance, topped: i.topped_up_balance, currency: i.currency } : { error: t('无数据') });
    }).catch((e: unknown) => alive && setBalance({ error: String(e).includes('no-key') ? t('未填 API Key') : t('查询失败') }));
    load(); const timer = setInterval(load, 60_000); return () => { alive = false; clearInterval(timer); };
  }, [lang]);

  const openSession = (id: string) => { setCurrent(id); setTab('chat'); void api.dshOpenSession(id); };
  const newSession = async (workspaceId?: string) => {
    const ws = workspaceId ?? workspaces[0]?.workspaceId; if (!ws || busy) return;
    const w = workspaces.find((x) => x.workspaceId === ws);
    const blank = w?.sessionIds.map((id) => sessions[id]).filter((s): s is SessionRow => !!s && s.blank && !archived.has(s.sessionId)).sort((a, b) => b.updatedAt - a.updatedAt)[0];
    if (blank) { openSession(blank.sessionId); return; }
    setBusy(true);
    try { const id = await api.dshNewSession(ws) as string; setCurrent(id); setTab('chat'); void refresh(); } catch (e) { toast(t('新建失败：{msg}', { msg: String(e).slice(0, 80) })); } finally { setBusy(false); }
  };
  const addProject = async () => {
    if (dsh.state !== 'running') { toast(t('智能体还没运行，稍等')); return; }
    try {
      const p = await api.pickFolder(); if (!p) return;
      await api.dshRpc('workspace.create', { path: p }); await refresh(true); toast(t('已添加项目'));
    } catch (e) { toast(t('添加失败：{msg}', { msg: String(e).slice(0, 100) })); }
  };
  const gitAction = async (w: Workspace, action: 'init' | 'commit') => {
    try {
      const r = action === 'init' ? await api.gitInit(w.path) : await api.gitCommit(w.path, t('OMaaX 快照 {time}', { time: new Date().toLocaleString() }));
      setGit((g) => ({ ...g, [w.workspaceId]: r as GitInfo }));
      toast(action === 'init' ? t('已开启版本控制') : t('已保存快照'));
    } catch (e) { toast(t('失败：{msg}', { msg: String(e).slice(0, 80) })); }
  };
  const rpc = async (method: string, payload: unknown, ok?: string) => { try { await api.dshRpc(method, payload); await refresh(); if (ok) toast(ok); } catch (e) { toast(t('失败：{msg}', { msg: String(e).slice(0, 80) })); } };
  const ask = (x: number, y: number, context: string, hint: string) => window.dispatchEvent(new CustomEvent('biodsh:ask', { detail: { x, y, context, hint } }));

  const sessionMenu = (e: React.MouseEvent, s: SessionRow, w: Workspace) => {
    e.preventDefault(); e.stopPropagation();
    const pinned = pins.includes(s.sessionId);
    setMenu({ x: e.clientX, y: e.clientY, items: [
      { label: pinned ? t('取消置顶') : t('置顶'), icon: <Pin size={13} />, onClick: () => savePins(pinned ? pins.filter((p) => p !== s.sessionId) : [s.sessionId, ...pins]) },
      { label: t('重命名'), icon: <Pencil size={13} />, onClick: () => { const v = window.prompt(t('新的对话名称'), t(sessionTitle(s))); if (v && v.trim()) void rpc('session.rename', { sessionId: s.sessionId, title: v.trim() }, t('已重命名')); } },
      { label: t('分享（导出对话记录）'), icon: <Share2 size={13} />, onClick: () => { void (api.sessionExport(s.sessionId) as Promise<string | null>).then((p) => p && toast(t('已导出到 {path}', { path: p }))).catch((err: unknown) => toast(t('导出失败：{msg}', { msg: String(err).slice(0, 80) }))); } },
      'sep',
      { label: t('归档'), icon: <Archive size={13} />, onClick: () => { void rpc('workspace.archiveSession', { sessionId: s.sessionId }, t('已归档')); if (current === s.sessionId) setCurrent(null); } },
      { label: t('永久删除'), icon: <Trash2 size={13} />, danger: true, onClick: () => { if (window.confirm(t('永久删除对话「{title}」？记录文件会从磁盘删除，无法恢复。', { title: t(sessionTitle(s)) }))) { void (api.sessionDelete(s.sessionId) as Promise<number>).then(() => { if (current === s.sessionId) setCurrent(null); return refresh(); }).then(() => toast(t('已永久删除'))).catch((err: unknown) => toast(`${t('删除失败')}: ${String(err).slice(0, 80)}`)); } } },
      'sep',
      { label: t('问一下：这个对话是什么？'), icon: <Sparkles size={13} />, onClick: () => ask(e.clientX, e.clientY, t('项目「{project}」里的对话「{session}」', { project: w.title, session: t(sessionTitle(s)) }), t('这个对话是关于什么的？')) },
    ] });
  };
  const projectMenu = (e: React.MouseEvent, w: Workspace) => {
    e.preventDefault(); e.stopPropagation();
    const g = git[w.workspaceId];
    setMenu({ x: e.clientX, y: e.clientY, items: [
      { label: t('新对话'), icon: <Plus size={13} />, onClick: () => { void newSession(w.workspaceId); } },
      { label: t('重命名项目'), icon: <Pencil size={13} />, onClick: () => { const v = window.prompt(t('项目名称'), w.title); if (v && v.trim()) void rpc('workspace.rename', { workspaceId: w.workspaceId, title: v.trim() }, t('已重命名')); } },
      { label: t('打开文件夹'), icon: <FolderOpen size={13} />, onClick: () => { void api.openPath(w.path); } },
      'sep',
      ...(g?.available ? [g.isRepo
        ? { label: g.dirty ? t('保存版本快照（{n} 处改动）', { n: g.dirty }) : t('保存版本快照（没有改动）'), icon: <GitCommitHorizontal size={13} />, disabled: !g.dirty, onClick: () => { void gitAction(w, 'commit'); } }
        : { label: t('开启版本控制（git）'), icon: <GitBranch size={13} />, onClick: () => { void gitAction(w, 'init'); } }, 'sep' as const] : []),
      { label: t('从列表移除（不删文件）'), icon: <Trash2 size={13} />, danger: true, onClick: () => { if (window.confirm(t('把项目「{project}」从列表移除？文件夹和文件都不会删除。', { project: w.title }))) void rpc('workspace.delete', { workspaceId: w.workspaceId }, t('已移除')); } },
      'sep',
      { label: t('问一下：版本控制是什么？'), icon: <Sparkles size={13} />, onClick: () => ask(e.clientX, e.clientY, t('项目的版本控制（git）：分支、快照、改动数'), t('版本控制、分支、快照是什么意思？我需要用吗？')) },
    ] });
  };

  const grouped = useMemo(() => ordered(workspaces, orders.projects || [], w => w.workspaceId).map((w) => {
    const rows = w.sessionIds.map((id) => sessions[id]).filter((s): s is SessionRow => !!s && !archived.has(s.sessionId)).sort((a, b) => b.updatedAt - a.updatedAt);
    let blankSeen = false;
    const dedup = rows.filter((s) => { if (!s.blank) return true; if (blankSeen) return false; blankSeen = true; return true; });
    return { w, rows: ordered([...dedup.filter((s) => pins.includes(s.sessionId)), ...dedup.filter((s) => !pins.includes(s.sessionId))], orders["sessions:" + w.workspaceId] || [], s => s.sessionId) };
  }), [workspaces, sessions, archived, pins, orders]);

  // 用量统计（token）：本对话 / 本项目 / 全部
  const usage = useMemo(() => {
    const all = Object.values(sessions).reduce((n, s) => n + sessionTokens(s), 0);
    const cur = current && sessions[current] ? sessionTokens(sessions[current]) : 0;
    const w = workspaces.find((x) => current && x.sessionIds.includes(current));
    const proj = w ? w.sessionIds.reduce((n, id) => n + (sessions[id] ? sessionTokens(sessions[id]) : 0), 0) : 0;
    return { all, cur, proj, projName: w?.title };
  }, [sessions, workspaces, current]);

  const nav = (id: Tab, label: string, Icon: typeof Cpu, right?: React.ReactNode) => (
    <button className={`nav-item no-drag text-left ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}><Icon size={16} strokeWidth={2} /><span className="flex-1">{label}</span>{right}</button>
  );
  const envDot = env.step === 'error' ? 'var(--red)' : (env.ready || env.step === 'idle') ? null : 'var(--orange)';

  return (
    <aside className="w-[264px] shrink-0 h-full flex flex-col relative" style={{ background: 'var(--sidebar)', borderRight: '0.5px solid var(--border)', backdropFilter: 'blur(20px)' }}>
      <div className={`drag h-[52px] flex items-center ${isMac ? 'pl-[84px]' : 'pl-4'} pr-3`}>
        <div className="omaax-wordmark" aria-label="OMaaX">OMaa<span>X</span></div>
        <div className="flex-1" />
        <div className="no-drag flex items-center gap-1">
          <button className="btn btn-ghost !px-2" title={t('重新加载页面')} onClick={() => window.biodsh.dshReload()}><RefreshCw size={14} /></button>
          <button className="btn btn-ghost !px-2" onClick={() => restartDsh()}>{t('重启智能体')}</button>
        </div>
      </div>

      <nav className="px-3 flex flex-col gap-[2px]">
        {ordered([{id:'new', content: <button className="nav-item no-drag text-left" onClick={() => newSession()} disabled={busy || dsh.state !== 'running'}><SquarePen size={16} strokeWidth={2} /><span className="flex-1">{t('新对话')}</span></button>},
{id:'data', content: nav('data', t('分析'), Database)},
{id:'workflows', content: nav('workflows', t('一体化分析流程'), GitBranch)},
{id:'db', content: nav('db', t('数据资源'), Globe)},
{id:'hosts', content: nav('hosts', t('服务器管理'), Server)},
{id:'env', content: nav('env', t('计算环境'), Cpu, envDot ? <span className="inline-block w-[7px] h-[7px] rounded-full" style={{ background: envDot }} /> : undefined)},
{id:'store', content: nav('store', t('工具中心'), Store)},
{id:'education', content: nav('education', t('教育科研'), GraduationCap)},
{id:'account', content: nav('account', t('账户与用量'), CreditCard)}].filter(item=>item.id==='account'||(item.id==='new'?chat:bioflow)), orders.nav || [], item => item.id).map(item => item.content && <div key={item.id} {...sortable('nav', item.id, ordered(['new','data','workflows','db','hosts','env','store','education','account'], orders.nav || [], id => id))}>{item.content}</div>)}
      </nav>

      <div className="flex-1 min-h-0 overflow-y-auto px-3 mt-3">
        <div className="flex items-center justify-between px-2 pb-1">
          <span className="t-caption" style={{ color: 'var(--text-3)' }}>{t('项目')}</span>
          <span className="flex items-center gap-0.5">
            <button className="p-1 rounded-md hover:bg-[var(--fill)]" title={t('新建项目（选一个文件夹）')} onClick={addProject}><FolderPlus size={13} style={{ color: 'var(--text-2)' }} /></button>
            <button className="p-1 rounded-md hover:bg-[var(--fill)]" title={t('刷新')} onClick={() => refresh(true)}>{refreshing ? <Loader2 size={13} className="spin" style={{ color: 'var(--accent)' }} /> : <RefreshCw size={13} style={{ color: 'var(--text-2)' }} />}</button>
          </span>
        </div>
        {dsh.state !== 'running' && <div className="t-caption px-2 py-2">{dsh.state === 'starting' ? t('智能体启动中…') : t('智能体未运行')}</div>}
        {grouped.map(({ w, rows }) => {
          const g = git[w.workspaceId]; const open = !collapsed[w.workspaceId];
          return (
            <div key={w.workspaceId} className="mb-1">
              <div {...sortable('projects', w.workspaceId, grouped.map(item => item.w.workspaceId))} className="group flex items-center gap-1.5 h-[30px] px-2 rounded-lg hover:bg-[var(--fill)] cursor-default" onContextMenu={(e) => projectMenu(e, w)} onDoubleClick={() => newSession(w.workspaceId)}>
                <button className="p-0.5" onClick={() => setCollapsed((c) => ({ ...c, [w.workspaceId]: open }))}>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
                <Folder size={14} style={{ color: 'var(--accent)' }} />
                <span className="t-body truncate flex-1" title={w.path}>{w.title || w.path.split(/[\\/]/).pop()}</span>
                {g?.isRepo && <span className="badge !h-[18px] !px-1.5 !text-[10px]" title={`${t('版本控制已开启 · 分支 {branch}', { branch: g.branch ?? '' })}${g.dirty ? ` · ${t('{n} 处改动未保存快照', { n: g.dirty })}` : ` · ${t('没有未保存的改动')}`}${g.lastCommit ? `\n${t('最近快照：{commit}', { commit: g.lastCommit })}` : ''}`}><GitBranch size={10} /> {g.branch}{g.dirty ? ` · ${g.dirty}` : ''}</span>}
                <button className="hidden group-hover:block p-1 rounded-md hover:bg-[var(--fill-2)]" title={t('在此项目新对话')} onClick={() => newSession(w.workspaceId)}><Plus size={13} /></button>
              </div>
              {open && rows.map((s) => {
                const active = current === s.sessionId && tab === 'chat';
                return (
                  <button key={s.sessionId} {...sortable('sessions:' + w.workspaceId, s.sessionId, rows.map(row => row.sessionId))} className="w-full text-left flex items-center gap-2 h-[28px] pl-8 pr-2 rounded-lg" style={{ background: active ? 'var(--accent-soft)' : undefined, color: active ? 'var(--accent)' : undefined }} onClick={() => openSession(s.sessionId)} onContextMenu={(e) => sessionMenu(e, s, w)} onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLElement).style.background = 'var(--fill)'; }} onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLElement).style.background = ''; }}>
                    {pins.includes(s.sessionId) ? <Pin size={11} style={{ color: 'var(--orange)' }} /> : <MessageSquare size={12} style={{ color: s.running ? 'var(--green)' : active ? 'var(--accent)' : 'var(--text-3)' }} />}
                    <span className="t-body truncate flex-1" style={{ fontSize: 12.5, fontWeight: active ? 600 : 400 }}>{t(sessionTitle(s))}</span>
                    {sessionTokens(s) > 0 && <span className="t-caption" style={{ color: active ? 'var(--accent)' : 'var(--text-3)' }}>{fmtTokens(sessionTokens(s))}</span>}
                  </button>
                );
              })}
              {open && rows.length === 0 && <div className="t-caption pl-8 py-1" style={{ color: 'var(--text-3)' }}>{t('还没有对话')}</div>}
            </div>
          );
        })}
        {dsh.state === 'running' && workspaces.length === 0 && <div className="t-caption px-2 py-2">{t('还没有项目，点上面的')} <FolderPlus size={11} className="inline" /> {t('选一个文件夹')}</div>}
      </div>

      <div className="px-3 pb-3 pt-2 hairline-t flex flex-col gap-1.5 relative">
        <button className="flex items-center gap-2 px-2 h-[30px] rounded-lg text-left hover:brightness-95" style={{ background: 'var(--fill)' }} onClick={() => setUsageOpen((v) => !v)} title={t('点击查看用量明细')}>
          <Wallet size={14} style={{ color: 'var(--accent)' }} />
          <span className="t-body flex-1">{offline ? t('本地／内网模型') : balance?.total != null ? t('余额 {amount}', { amount: `${balance.currency === 'CNY' ? '¥' : '$'}${Number(balance.total).toFixed(2)}` }) : balance?.error ?? t('查询余额…')}</span>
          <span className="t-caption">{offline ? t('本地/内网') : 'DeepSeek'}</span>
        </button>
        {usageOpen && <UsagePanel balance={balance} usage={usage} onClose={() => setUsageOpen(false)} />}
        <div className="flex items-center justify-between px-2">
          <span className="t-caption flex items-center gap-1.5"><span className="inline-block w-[6px] h-[6px] rounded-full" style={{ background: dsh.state === 'running' ? 'var(--green)' : dsh.state === 'starting' ? 'var(--orange)' : 'var(--text-3)' }} />{dsh.state === 'running' ? t('智能体运行中') : dsh.state === 'starting' ? t('启动中') : dsh.state === 'error' ? t('智能体出错') : t('未启动')}</span>
          <button className="p-1 rounded-md hover:bg-[var(--fill)]" title={t('设置')} onClick={() => setTab('settings')}><Settings size={13} style={{ color: 'var(--text-2)' }} /></button>
        </div>
      </div>

      {notice && <div className="absolute left-3 right-3 bottom-[76px] z-[65] px-3 py-2 rounded-lg t-caption rise" style={{ background: 'var(--text)', color: 'var(--bg)' }}>{notice}</div>}
      {menu && <ContextMenu menu={menu} onClose={() => setMenu(null)} />}
    </aside>
  );
}

function UsagePanel({ balance, usage, onClose }: { balance: Balance | null; usage: { all: number; cur: number; proj: number; projName?: string }; onClose: () => void }) {
  const { pushOverlay, popOverlay } = useApp();
  const { t } = useT();
  useEffect(() => { pushOverlay(); return () => popOverlay(); }, [pushOverlay, popOverlay]);
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
  const sym = balance?.currency === 'CNY' ? '¥' : '$';
  const row = (l: string, v: string, sub?: string) => <div className="flex items-baseline justify-between gap-3"><span className="t-body" style={{ color: 'var(--text-2)' }}>{l}</span><span className="t-body text-right"><span className="t-mono">{v}</span>{sub && <span className="t-caption ml-1">{sub}</span>}</span></div>;
  return (
    <div className="absolute left-3 right-3 bottom-[76px] z-[66] card p-3 flex flex-col gap-2 rise" style={{ boxShadow: 'var(--shadow-sheet)' }}>
      <div className="flex items-center justify-between"><span className="t-headline">{t('用量与额度')}</span><button className="p-1 rounded-md hover:bg-[var(--fill)]" onClick={onClose}><X size={12} /></button></div>
      <div className="t-caption" style={{ color: 'var(--text-3)' }}>{t('DeepSeek 账户')}</div>
      {balance?.total != null ? <>{row(t('可用余额'), `${sym}${Number(balance.total).toFixed(2)}`)}{balance.granted != null && row(t('赠送'), `${sym}${Number(balance.granted).toFixed(2)}`)}{balance.topped != null && row(t('充值'), `${sym}${Number(balance.topped).toFixed(2)}`)}</> : <div className="t-body">{balance?.error ?? t('查询中…')}</div>}
      <div className="t-caption mt-1" style={{ color: 'var(--text-3)' }}>{t('Token 用量（含缓存命中）')}</div>
      {row(t('本对话'), fmtTokens(usage.cur))}
      {row(usage.projName ? t('本项目「{project}」', { project: usage.projName }) : t('本项目'), fmtTokens(usage.proj))}
      {row(t('全部对话'), fmtTokens(usage.all))}
      <div className="t-caption" style={{ color: 'var(--text-3)' }}>{t('费用按 DeepSeek 官网价目计算，余额每分钟刷新。')}</div>
    </div>
  );
}

export function WindowControls({ compact = false }: { compact?: boolean }) {
  const { info } = useApp() || {info:null};
  if (info?.platform === 'darwin') return null;
  const size = compact ? 'w-[30px] h-[30px]' : 'w-[46px] h-[44px]';
  const b = (a: 'minimize' | 'maximize' | 'close', I: typeof Minus) => (
    <button className={`no-drag ${size} flex items-center justify-center rounded-md hover:bg-[var(--fill)]`} onClick={() => window.biodsh.windowControl(a)}><I size={13} strokeWidth={1.5} /></button>
  );
  return <div className="flex" style={{ color: 'var(--text-2)' }}>{b('minimize', Minus)}{b('maximize', Square)}{b('close', X)}</div>;
}

export function Logo({size=22}:{size?:number}) { return <img src={omaaxIcon} width={size} height={size} alt="OMaaX" />; }
