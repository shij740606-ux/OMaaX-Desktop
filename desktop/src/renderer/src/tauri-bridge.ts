// Tauri 桥：把 window.biodsh 接口实现为 invoke/listen，与 Electron preload 暴露的接口保持一致。
import { invoke } from '@tauri-apps/api/core';
import type { PlatformJobCreate, PlatformNodeControlStatus } from '@shared/types';
import { listen } from '@tauri-apps/api/event';
import type { AppSettings, IpcEvent, Rect } from '@shared/types';
import {dshLayoutQueue} from './dshLayoutQueue';

export function installTauriBridge() {
  const reportDshBounds=dshLayoutQueue((rect,visible)=>invoke('dsh_bounds',{rect,visible}),error=>console.error('Chat layout failed',error));
  const api = {
    personalCompute: (id:string, action:string, body:unknown = {}) => invoke('personal_compute', {id, action, body}),
    platformStorage: (action:string, body:unknown = {}) => invoke('platform_storage', {action, body}),
    platformProjectCollaboration: (projectId:string, action:string, body:unknown = null) => invoke('platform_project_collaboration', {projectId, action, body}),
    platformSchedulingNodes: () => invoke('platform_scheduling_nodes'),
    platformScheduling: (jobId: string) => invoke('platform_scheduling', {jobId}),
    sshHostBind: (id: string, nodeId: string | null) => invoke('ssh_hosts_bind', {id, nodeId}),
    mode: 'tauri' as const,
    sshHostMetrics: (id: string) => invoke('ssh_hosts_metrics', { id }),
    sshHosts: () => invoke('ssh_hosts_list'),
    sshHostSave: (draft: import('@shared/types').SshHostDraft) => invoke('ssh_hosts_save', { draft }),
    sshHostRemove: (id: string) => invoke('ssh_hosts_remove', { id }),
    sshHostSelect: (id: string, selected: boolean) => invoke('ssh_hosts_select', { id, selected }),
    sshHostTrust: (id: string, hostKey: string, fingerprint: string) => invoke('ssh_hosts_trust', { id, hostKey, fingerprint }),
    sshHostTest: (id: string, probe = true) => invoke('ssh_hosts_test', { id, probe }),
    appInfo: () => invoke('app_info'),
    getSettings: () => invoke('settings_get'),
    setSettings: (patch: Partial<AppSettings>) => invoke('settings_set', { patch }),
    platformSession: () => invoke('platform_session'),
    platformLogin: (username: string, password: string) => invoke('platform_login', { username, password }),
    platformLogout: () => invoke('platform_logout'),
    platformNodes: () => invoke('platform_nodes'),
    platformNodeMetrics: (nodeId: string, minutes = 60) => invoke('platform_node_metrics', { nodeId, minutes }),
    platformUpdateNode: (nodeId: string, status: PlatformNodeControlStatus) => invoke('platform_update_node', { nodeId, status }),
    platformJobs: (projectId: string) => invoke('platform_jobs', { projectId }),
    platformJobKinds: () => invoke('platform_job_kinds'),
    platformCreateJob: (input: PlatformJobCreate) => invoke('platform_create_job', { input }),
    platformCancelJob: (jobId: string) => invoke('platform_cancel_job', { jobId }),
    platformJobEvents: (jobId: string) => invoke('platform_job_events', { jobId }),
    platformJobArtifacts: (jobId: string) => invoke('platform_job_artifacts', { jobId }),
    platformDownloadJobArtifact: (jobId: string, artifact: { id: string; relativePath: string }) => invoke('platform_download_job_artifact', { jobId, artifactId: artifact.id, relativePath: artifact.relativePath }),
    knowledgeCredential: () => invoke('knowledge_credential'),
    knowledgeSetCredential: (key: string) => invoke('knowledge_set_credential', { key }),
    knowledgeDatasets: () => invoke('knowledge_datasets'),
    knowledgeCreateDataset: (name: string, description = '') => invoke('knowledge_create_dataset', { name, description }),
    knowledgeDocuments: (datasetId: string) => invoke('knowledge_documents', { datasetId }),
    knowledgeAddFiles: (datasetId: string) => invoke('knowledge_add_files', { datasetId }),
    knowledgeFolders: () => invoke('knowledge_folders'),
    knowledgeAddFolder: (datasetId: string) => invoke('knowledge_add_folder', { datasetId }),
    knowledgeRemoveFolder: (datasetId: string, path: string) => invoke('knowledge_remove_folder', { datasetId, path }),
    knowledgeSyncFolders: () => invoke('knowledge_sync_folders'),
    knowledgeSearch: (datasetIds: string[], question: string) => invoke('knowledge_search', { datasetIds, question }),
    getCredential: () => invoke('credential_get'),
    setCredential: (key: string) => invoke('credential_set', { key }),
    envStatus: () => invoke('env_status'),
    envInstall: () => invoke('env_install'),
    dshStatus: () => invoke('dsh_status'),
    dshStart: () => invoke('dsh_start'),
    dshRestart: () => invoke('dsh_restart'),
    dshStop: () => invoke('dsh_stop'),
    dshReload: () => invoke('dsh_reload'),
    dshBounds: reportDshBounds,
    skillsCatalog: () => invoke('skills_catalog'),
    skillsStatuses: () => invoke('skills_statuses'),
    skillReadme: (id: string) => invoke('skills_readme', { id }),
    skillInstall: (id: string) => invoke('skills_install', { id }),
    skillUninstall: (id: string) => invoke('skills_uninstall', { id }),
    openPath: (p: string) => invoke('open_path', { path: p }),
    openExternal: (u: string) => invoke('open_external', { url: u }),
    pickFolder: () => invoke('pick_folder'),
    dshRpc: (method: string, payload: unknown = {}) => invoke('dsh_rpc', { method, payload }),
    dshOpenSession: (sessionId: string) => invoke('dsh_open_session', { sessionId }),
    dshNewSession: (workspaceId: string) => invoke('dsh_new_session', { workspaceId }),
    gitStatus: (path: string) => invoke('git_status', { path }),
    gitInit: (path: string) => invoke('git_init', { path }),
    gitCommit: (path: string, message: string) => invoke('git_commit', { path, message }),
    deepseekBalance: () => invoke('deepseek_balance'),
    workspaceFiles: (path?: string) => invoke('workspace_files', { path: path ?? null }),
    migrateScan: () => invoke('migrate_scan'),
    migrateImport: (sourceId: string) => invoke('migrate_import', { sourceId }),
    sessionDelete: (sessionId: string) => invoke('session_delete', { sessionId }),
    envInstallExtra: (packages: string[]) => invoke('env_install_extra', { packages }),
    refdataList: () => invoke('refdata_list'),
    refdataInstall: (id: string) => invoke('refdata_install', { id }),
    refdataRemove: (id: string) => invoke('refdata_remove', { id }),
    demosSeed: () => invoke('demos_seed'),
    dbCatalog: () => invoke('db_catalog'),
    embedQuery: (text: string) => invoke('embed_query', { text }),
    lightUpdateCheck: () => invoke('light_update_check'),
    lightUpdateApply: (delta: unknown) => invoke('light_update_apply', { delta }),
    dshSetContext: (workspaces: Record<string, string>) => invoke('dsh_set_context', { workspaces }),
    ratingsGet: () => invoke('ratings_get'),
    ratingsSet: (id: string, vote: number, comment: string) => invoke('ratings_set', { id, vote, comment }),
    readWorkspaceImage: (rel: string, path?: string) => invoke('read_workspace_image', { rel, path: path ?? null }),
    sessionExport: (sessionId: string) => invoke('session_export', { sessionId }),
    checkUpdates: () => invoke('check_updates'),
    assistantAsk: (model: string, question: string, context: string) => invoke('assistant_ask', { model, question, context }),
    windowControl: (a: 'minimize' | 'maximize' | 'close') => invoke('window_control', { action: a }),
    onEvent: (cb: (e: IpcEvent) => void) => {
      const p = listen<IpcEvent>('event', (e) => cb(e.payload));
      return () => { void p.then((un) => un()); };
    },
    onDebugTab: (_cb: (tab: string) => void) => { /* Tauri 版无截图调试 */ },
    onTheme: (_cb: (dark: boolean) => void) => () => { /* 用 CSS 媒体查询即可 */ },
  };
  (window as unknown as { biodsh: typeof api }).biodsh = api;
  const log = (m: string) => { void invoke('client_log', { line: `${new Date().toISOString()} ${m}` }); };
  window.addEventListener('error', (e) => log(`error: ${e.message} @${e.filename}:${e.lineno}`));
  window.addEventListener('unhandledrejection', (e) => log(`unhandledrejection: ${String((e as PromiseRejectionEvent).reason)}`));
  log('bridge installed');
  // 顶栏拖拽：Tauri 无边框窗口靠 data-tauri-drag-region 属性
  const mark = () => document.querySelectorAll('.drag').forEach((el) => el.setAttribute('data-tauri-drag-region', ''));
  new MutationObserver(mark).observe(document.documentElement, { childList: true, subtree: true });
  mark();
}
