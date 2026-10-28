import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { syncLanguage, useT } from './i18n';
import type { AppSettings, CatalogSkill, CredentialStatus, DshStatus, EnvStatus, PlatformSession, SkillStatus } from '@shared/types';

export type Tab = 'drawing' | 'chat' | 'knowledge' | 'data' | 'workflows' | 'db' | 'hosts' | 'tasks' | 'servers' | 'store' | 'account' | 'education' | 'env' | 'settings';

interface AppState {
  ready: boolean;
  info: { version: string; platform: string; paths: Record<string, string>; dark: boolean } | null;
  settings: AppSettings | null;
  platform: PlatformSession;
  credential: CredentialStatus;
  env: EnvStatus;
  dsh: DshStatus;
  catalog: CatalogSkill[];
  statuses: Record<string, SkillStatus>;
  busy: Record<string, boolean>;
  tab: Tab;
  setTab: (t: Tab) => void;
  overlay: number;
  pushOverlay: () => void;
  popOverlay: () => void;
  currentSession: string | null;
  setCurrentSession: (id: string | null) => void;
  refresh: () => Promise<void>;
  updateSettings: (p: Partial<AppSettings>) => Promise<void>;
  platformLogin: (username: string, password: string) => Promise<void>;
  platformLogout: () => Promise<void>;
  refreshPlatform: () => Promise<void>;
  saveKey: (k: string) => Promise<void>;
  installEnv: () => Promise<void>;
  startDsh: () => Promise<void>;
  restartDsh: () => Promise<void>;
  install: (id: string) => Promise<void>;
  uninstall: (id: string) => Promise<void>;
}

const Ctx = createContext<AppState | null>(null);
export const useApp = () => useContext(Ctx)!;

const emptyEnv: EnvStatus = { ready: false, step: 'idle', message: '', progress: 0, log: [] };

export function AppProvider({ children }: { children: React.ReactNode }) {
  const { t } = useT();
  const api = window.biodsh;
  const [ready, setReady] = useState(false);
  const [loadError,setLoadError]=useState(false);
  const [info, setInfo] = useState<AppState['info']>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  useEffect(() => { if (settings) syncLanguage(settings.language ?? 'system'); }, [settings?.language]);
  const [platform, setPlatform] = useState<PlatformSession>({ authenticated: false, projects: [] });
  const [credential, setCredential] = useState<CredentialStatus>({ hasKey: false });
  const [env, setEnv] = useState<EnvStatus>(emptyEnv);
  const [dsh, setDsh] = useState<DshStatus>({ state: 'stopped', log: [] });
  const [catalog, setCatalog] = useState<CatalogSkill[]>([]);
  const [statuses, setStatuses] = useState<Record<string, SkillStatus>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [tab, setTab] = useState<Tab>('chat');
  const [overlay, setOverlay] = useState(0);
  const pushOverlay = useCallback(() => setOverlay((n) => n + 1), []);
  const popOverlay = useCallback(() => setOverlay((n) => Math.max(0, n - 1)), []);
  const [currentSession, setCurrentSession] = useState<string | null>(null);

  const toMap = (arr: SkillStatus[]) => Object.fromEntries(arr.map((s) => [s.id, s]));

  const refresh = useCallback(async () => {
    setLoadError(false);
    try {
    const [i, s, c, e, d, cat, st] = await Promise.all([
      api.appInfo(), api.getSettings(), api.getCredential(), api.envStatus(), api.dshStatus(), api.skillsCatalog(), api.skillsStatuses(),
    ]);
    const session: PlatformSession = { authenticated: false, projects: [] };
    let nextSettings = s;
    if (session.authenticated && !session.projects.some((project) => project.id === s.currentProjectId)) {
      nextSettings = await api.setSettings({ currentProjectId: session.projects[0]?.id ?? '' });
    }
    setInfo(i); setSettings(nextSettings); setCredential(c); setEnv(e); setDsh(d); setCatalog(cat); setStatuses(toMap(st));
    setPlatform(session);
    setReady(true);
    } catch {setLoadError(true);}
  }, []);

  useEffect(() => {
    void refresh();
    const off = api.onEvent((ev) => {
      if (ev.type === 'env') setEnv(ev.status);
      else if (ev.type === 'dsh') setDsh(ev.status);
      else if (ev.type === 'skills') setStatuses(toMap(ev.statuses));
    });
    window.biodsh.onDebugTab((t) => { if (t.startsWith('onboard')) { void api.setSettings({ onboarded: false }).then(setSettings); } else { void api.setSettings({ onboarded: true }).then(setSettings); if (t === 'community') { try { localStorage.setItem('biodsh.storeTier', 'community'); } catch { /* */ } setTab('store'); setTimeout(() => window.dispatchEvent(new CustomEvent('biodsh:tier', { detail: 'community' })), 300); } else setTab(t as Tab); } });
    return off;
  }, [refresh]);

  const updateSettings = async (p: Partial<AppSettings>) => {
    try { setSettings(await api.setSettings(p)); }
    catch (error) {
      // The native host may have saved the preference before live chat synchronization failed.
      try { setSettings(await api.getSettings()); } catch { /* Keep the last known settings if reading also fails. */ }
      throw error;
    }
  };
  const platformLogin = async (username: string, password: string) => {
    const session = await api.platformLogin(username, password);
    setPlatform(session);
    const selected = session.projects.some((project) => project.id === settings?.currentProjectId)
      ? settings?.currentProjectId
      : session.projects[0]?.id ?? '';
    if (session.authenticated && selected !== (settings?.currentProjectId ?? '')) {
      setSettings(await api.setSettings({ currentProjectId: selected }));
    }
  };
  const platformLogout = async () => {
    setPlatform(await api.platformLogout());
    setSettings(await api.setSettings({ currentProjectId: '' }));
  };
  const refreshPlatform = async () => {
    const session = await api.platformSession();
    setPlatform(session);
    const selected = session.projects.some(project => project.id === settings?.currentProjectId)
      ? settings?.currentProjectId : session.projects[0]?.id ?? '';
    if (session.authenticated && selected !== (settings?.currentProjectId ?? '')) {
      setSettings(await api.setSettings({ currentProjectId: selected }));
    }
  };
  const saveKey = async (k: string) => setCredential(await api.setCredential(k));
  const installEnv = async () => { setEnv(await api.envInstall()); };
  const startDsh = async () => { setDsh(await api.dshStart()); };
  const restartDsh = async () => { setDsh(await api.dshRestart()); };
  const install = async (id: string) => {
    setBusy((b) => ({ ...b, [id]: true }));
    try { const r = await api.skillInstall(id); setStatuses((s) => ({ ...s, [id]: r })); }
    catch (e) { setStatuses(s => ({ ...s, [id]: { id, state: 'error', error: String(e) } })); }
    finally { setBusy((b) => ({ ...b, [id]: false })); }
  };
  const uninstall = async (id: string) => {
    const r = await api.skillUninstall(id);
    setStatuses((s) => ({ ...s, [id]: r }));
  };

  return (
    <Ctx.Provider value={{ ready, info, settings, platform, credential, env, dsh, catalog, statuses, busy, tab, setTab, overlay, pushOverlay, popOverlay, currentSession, setCurrentSession, refresh, updateSettings, platformLogin, platformLogout, refreshPlatform, saveKey, installEnv, startDsh, restartDsh, install, uninstall }}>
      {loadError?<div role="alert" className="h-full flex flex-col items-center justify-center gap-4"><p>{t('暂时无法加载工作空间，请检查连接后重试。')}</p><button className="btn btn-primary" onClick={()=>void refresh()}>{t('重新加载')}</button></div>:children}
    </Ctx.Provider>
  );
}
