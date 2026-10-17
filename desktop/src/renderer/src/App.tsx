import { useEffect } from 'react';
import AuthGate,{useAuth,hasUnit} from './AuthGate';
import { SudoInstallProvider } from './components/SudoInstall';
import { AppProvider, useApp } from './store';
import Sidebar, { WindowControls } from './components/Sidebar';
import ChatView from './views/ChatView';
import StoreView from './views/StoreView';
import AccountView from './views/AccountView';
import DataView from './views/DataView';
import DatabaseView from './views/DatabaseView';
import EnvView from './views/EnvView';
import SettingsView from './views/SettingsView';
import Onboarding from './views/Onboarding';
import ServerManagementView from './views/ServerManagementView';
import WorkflowPanel from './views/WorkflowPanel';
import AskPopover from './components/AskPopover';
import { LangContext, resolveLang } from './i18n';

function useTheme() {
  const { settings, info } = useApp();
  useEffect(() => {
    const apply = (dark: boolean) => document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const sys = () => apply(mq.matches);
    if (!settings || settings.theme === 'system') { sys(); mq.addEventListener('change', sys); return () => mq.removeEventListener('change', sys); }
    apply(settings.theme === 'dark');
  }, [settings?.theme, info?.dark]);
}

function Shell() {
  useTheme();
  const {user}=useAuth();
  const bioflow=hasUnit(user,"bioflow"), chat=hasUnit(user,"qa")||bioflow;
  const { ready, settings, tab } = useApp();
  if (!ready || !settings) return <div className="h-full w-full drag" />;
  return (
    <LangContext.Provider value={resolveLang(settings.language)}>
    <div className="h-full w-full flex">
      <Sidebar />
      <main className="app-main flex-1 min-w-0 h-full relative">
        <div className="absolute right-3 top-[11px] z-40 no-drag"><WindowControls compact /></div>
        {tab === 'chat' && chat && <ChatView />}{tab === 'chat' && !chat && <p className="p-12">账号尚未获得对话或分析权限，请联系管理员授权。</p>}
        {tab === 'data' && bioflow && <DataView />}
        {bioflow && <WorkflowPanel active={tab === 'workflows'}/>}
        {tab === 'db' && bioflow && <DatabaseView />}
        {tab === 'hosts' && bioflow && <ServerManagementView />}
        {tab === 'tasks' && bioflow && <ServerManagementView initial="tasks" />}
        {tab === 'servers' && bioflow && <ServerManagementView initial="resources" />}
        {tab === 'store' && bioflow && <StoreView />}
        {tab === 'env' && bioflow && <EnvView />}
        {tab === 'account' && <AccountView />}
        {tab === 'settings' && <SettingsView />}
      </main>
      {!settings.onboarded && chat && <Onboarding />}
      {chat && <AskPopover />}
    </div>
    </LangContext.Provider>
  );
}

export default function App() {
  return <AuthGate><AppProvider><SudoInstallProvider><Shell /></SudoInstallProvider></AppProvider></AuthGate>;
}
