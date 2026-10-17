import { useEffect } from 'react';
import { SudoInstallProvider } from './components/SudoInstall';
import { AppProvider, useApp } from './store';
import Sidebar, { WindowControls } from './components/Sidebar';
import ChatView from './views/ChatView';
import StoreView from './views/StoreView';
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
  const { ready, settings, tab } = useApp();
  if (!ready || !settings) return <div className="h-full w-full drag" />;
  return (
    <LangContext.Provider value={resolveLang(settings.language)}>
    <div className="h-full w-full flex">
      <Sidebar />
      <main className="app-main flex-1 min-w-0 h-full relative">
        <div className="absolute right-3 top-[11px] z-40 no-drag"><WindowControls compact /></div>
        {tab === 'chat'  && <ChatView />}
        {tab === 'data' && <DataView />}
        <WorkflowPanel active={tab === 'workflows'}/>
        {tab === 'db' && <DatabaseView />}
        {tab === 'hosts' && <ServerManagementView />}
        {tab === 'tasks' && <ServerManagementView initial="tasks" />}
        {tab === 'servers' && <ServerManagementView initial="resources" />}
        {tab === 'store' && <StoreView />}
        {tab === 'env' && <EnvView />}
        {tab === 'settings' && <SettingsView />}
      </main>
      {!settings.onboarded && <Onboarding />}
      <AskPopover />
    </div>
    </LangContext.Provider>
  );
}

export default function App() {
  return <AppProvider><SudoInstallProvider><Shell /></SudoInstallProvider></AppProvider>;
}
