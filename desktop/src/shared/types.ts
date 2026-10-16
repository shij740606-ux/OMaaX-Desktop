// 主进程 ↔ 渲染进程共享的类型。所有 IPC 都只传这些纯数据。

export type SkillCategory = 'qc' | 'analysis' | 'clinical' | 'audit' | 'other';

export interface SkillInput { name: string; format: string; required: boolean }

export interface CatalogSkill {
  id: string;
  name: string;
  summary: string;
  description: string;
  domain: string;
  category: SkillCategory | string;
  icon: string;
  tags: string[];
  inputs: SkillInput[];
  outputs: string[];
  offline: boolean;
  mutates_input: boolean;
  version: string;
  evidence: { tests?: string; offline?: boolean; reproducible?: boolean; dataset?: string } | null;
  score?: number | null;
  scoreBreakdown?: { correctness?: number; robustness?: number; reproducibility?: number; offline?: number; efficiency?: number } | null;
  score_source?: string;
  benchmarks?: string[] | null;
  featured: boolean;
  requires: { python: boolean; env: string };
  tier: 'official' | 'community';
  bundle: 'skills' | 'community-skills';
  origin?: { repo: string; url: string; path: string; license: string };
  has_scripts?: boolean;
  modelInvocable?: boolean;
  sourceGuide?: { files: string[]; instructions: string; requirements: string; revision: string; licenseFiles: string[] };
  domain_zh?: string;
  subcategory?: string;
  level?: string;
  name_en?: string;
}

export type InstallState = 'not_installed' | 'installing' | 'installed' | 'update_available' | 'error';

export interface SkillStatus { id: string; state: InstallState; installedVersion?: string; error?: string }

export type EnvStep = 'idle' | 'python' | 'venv' | 'packages' | 'verify' | 'ready' | 'error';

export interface EnvStatus {
  ready: boolean;
  step: EnvStep;
  message: string;
  progress: number; // 0..1
  pythonPath?: string;
  pythonVersion?: string;
  packages?: Record<string, string>;
  log: string[];
}

export type DshState = 'stopped' | 'starting' | 'running' | 'error';

export interface DshStatus { state: DshState; url?: string; error?: string; log: string[] }

export interface AppSettings {
  useChinaMirror: boolean;
  workspace: string;
  onboarded: boolean;
  theme: 'system' | 'light' | 'dark';
  language: 'system' | 'zh' | 'en';
  /** 在线模式：DeepSeek 云端 API + 余额/更新等联网功能；离线模式：内网/本地模型接口，不发起任何外网请求 */
  mode: 'online' | 'offline';
  offlineBaseUrl: string;
  offlineModel: string;
  offlineApiKey: string;
  /** 离线模式可选：连接已部署在 Linux 服务器上的 dsh（不在本机启动） */
  remoteDshUrl: string;
  /** BioDSH 统一平台地址；多用户和多服务器功能通过该入口访问 */
  platformUrl: string;
  /** 当前选中的平台项目 */
  currentProjectId: string;
  /** RAGFlow 服务地址；API 密钥单独保存在系统安全存储 */
  ragflowUrl: string;
  /** 知识库解析位置：本机 RAGFlow 或远程服务器 RAGFlow */
  ragflowMode: 'local' | 'server';
  /** 图像生成：OpenAI 兼容 images 接口（智谱 CogView / OpenAI / SiliconFlow / 通义万相） */
  imageBaseUrl: string;
  imageApiKey: string;
  imageModel: string;
  /** 外接 MCP 服务 */
  mcpServers: McpServer[];
  demosSeeded: boolean;
}

export interface PlatformStatus {
  status: string;
  service: string;
  version: string;
  database: string;
}

export type PlatformRole = 'admin' | 'owner' | 'analyst' | 'viewer';
export interface PlatformUser { id: string; username: string; displayName?: string; avatarUrl?: string; isActive: boolean; isAdmin: boolean }
export interface PlatformProject { id: string; name: string; description: string; role: PlatformRole }
export interface PlatformSession {
  authenticated: boolean;
  deviceAuthorized?: boolean;
  deviceId?: string;
  connectionError?: string;
  user?: PlatformUser;
  projects: PlatformProject[];
}

export type PlatformNodeStatus = 'online' | 'offline' | 'draining' | 'disabled';
export type PlatformNodeControlStatus = Exclude<PlatformNodeStatus, 'offline'>;
export interface PlatformGpuMetric {
  index: number;
  name: string;
  uuid?: string;
  utilization_percent?: number;
  memory_used_mib?: number;
  memory_total_mib?: number;
  temperature_c?: number;
}
export interface PlatformNodeMetric {
  id: number;
  nodeId: string;
  cpuPercent: number;
  loadOne?: number;
  memoryUsedBytes: number;
  memoryTotalBytes: number;
  memoryPercent: number;
  diskUsedBytes: number;
  diskTotalBytes: number;
  diskPercent: number;
  networkRxBytes: number;
  networkTxBytes: number;
  runningJobs: number;
  gpus: PlatformGpuMetric[];
  collectedAt: string;
}
export interface PlatformNode {
  id: string;
  name: string;
  status: PlatformNodeStatus;
  hostname: string;
  operatingSystem: string;
  agentVersion: string;
  capabilities: Record<string, unknown>;
  latestMetric?: PlatformNodeMetric;
  lastHeartbeatAt?: string;
}

export type PlatformJobStatus = 'queued' | 'assigned' | 'preparing' | 'running' | 'cancelling' | 'cancelled' | 'succeeded' | 'failed' | 'unknown';
export interface PlatformJob {
  id: string;
  projectId: string;
  nodeId?: string;
  kind: string;
  status: PlatformJobStatus;
  payload: Record<string, unknown>;
  requestedResources: Record<string, number>;
  priority: number;
  attempt: number;
  assignedAt?: string;
  startedAt?: string;
  finishedAt?: string;
  exitCode?: number;
  resultManifest: Record<string, unknown>;
  errorMessage: string;
  createdAt: string;
  updatedAt: string;
}
export interface PlatformJobEvent {
  id: number;
  jobId: string;
  sequence: number;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
  createdAt: string;
}
export interface PlatformJobArtifact {
  id: string;
  jobId: string;
  relativePath: string;
  sizeBytes: number;
  sha256: string;
  contentType: string;
  createdAt: string;
}
export interface PlatformJobCreate {
  dataVersionIds?: string[];
  nodeIds?: string[];
  projectId: string;
  kind: string;
  arguments: string[];
  requestedResources: Record<string, number>;
  priority?: number;
}

export interface KnowledgeCredentialStatus { configured: boolean; masked?: string }
export interface KnowledgeDataset {
  id: string;
  name: string;
  description: string;
  documentCount: number;
  chunkCount: number;
  createdAt?: string;
  updatedAt?: string;
}
export interface KnowledgeDocument {
  id: string;
  datasetId: string;
  name: string;
  sizeBytes: number;
  chunkCount: number;
  tokenCount: number;
  status: 'waiting' | 'parsing' | 'ready' | 'failed';
  progress: number;
  createdAt?: string;
  localPath?: string;
}
export interface KnowledgeCitation {
  id: string;
  datasetId: string;
  documentId: string;
  documentName: string;
  content: string;
  similarity: number;
  page?: number;
}
export interface KnowledgeFolder {
  datasetId: string;
  path: string;
  enabled: boolean;
  fileCount: number;
  lastSyncAt?: string;
  error?: string;
}
export interface KnowledgeSyncResult { uploaded: number; skipped: number; failed: number }

export interface SshHost {
  id: string; name: string; host: string; port: number; username: string;
  workerNodeId?: string; auth: 'password' | 'key'; selected: boolean; fingerprint?: string; hostKey?: string;
}
export interface SshHostDraft {
  id?: string; name: string; host: string; port: number; username: string;
  auth: 'password' | 'key'; password?: string; privateKey?: string; passphrase?: string;
}
export interface SshProbe { fingerprint: string; hostKey: string; connected?: boolean }
export interface SshDirectoryRequest {path?:string;offset?:number;limit?:number;showHidden?:boolean}
export interface SshDirectoryPage {path:string;home:string;parent:string|null;files:{name:string;path:string;directory:boolean;link:boolean;size:number;modified:number;permissions:string}[];nextOffset:number|null}

export interface McpServer {
  name: string;
  transport: 'stdio' | 'streamable-http';
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  enabled?: boolean;
}

export interface RefPack { id: string; name: string; desc: string; group: string; sizeMb: number; installed: boolean; path: string }
export interface DemoInfo { id: string; title: string; path: string; registered: boolean }

export interface AppPaths { root: string; dshHome: string; bioenv: string; workspace: string; skills: string; logs: string }

export interface CredentialStatus { hasKey: boolean; masked?: string }

export interface Rect { x: number; y: number; width: number; height: number }

export type IpcEvent =
  | { type: 'env'; status: EnvStatus }
  | { type: 'dsh'; status: DshStatus }
  | { type: 'skills'; statuses: SkillStatus[] }
  | { type: 'skill-progress'; id: string; message: string }
  | { type: 'refdata'; id: string; received: number; total: number; done?: boolean };

export interface SshMetrics {
 sampledAt: number; hostname: string; system: string; architecture: string; cpuModel: string;
 logicalCores: number; physicalCores: number | null; cpuPercent: number | null; load: number[]; uptimeSeconds: number;
 memoryTotal: number; memoryUsed: number; swapTotal: number; swapUsed: number;
 disks: { mount: string; total: number; used: number; free: number }[];
 network: { name: string; received: number; sent: number }[]; gpu: string | null;
}

export interface SchedulingNode { id: string; name: string; hostname: string; status: string }
export interface SchedulingDecision { selected_node_id: string | null; reason: string; nodes: {node_id: string; name: string; eligible: boolean; reason: string; cpu_free: number; memory_free: number; reserved_cpu: number}[] }
