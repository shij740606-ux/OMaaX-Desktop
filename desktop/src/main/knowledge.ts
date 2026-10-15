import { copyFileSync, existsSync, mkdirSync, openAsBlob, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { safeStorage } from 'electron';
import type { AppPaths, KnowledgeCitation, KnowledgeCredentialStatus, KnowledgeDataset, KnowledgeDocument, KnowledgeFolder, KnowledgeSyncResult } from '../shared/types';

interface StoredFolder extends KnowledgeFolder { files: Record<string, string> }

const supported = new Set(['.pdf', '.doc', '.docx']);

function walk(directory: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const current = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...walk(current));
    else if (entry.isFile() && supported.has(path.extname(entry.name).toLowerCase())) files.push(current);
  }
  return files;
}

export class KnowledgeClient {
  constructor(private readonly paths: AppPaths, private readonly getBaseUrl: () => string) {}

  private get keyFile() { return path.join(this.paths.root, 'ragflow-key.bin'); }
  private get folderFile() { return path.join(this.paths.root, 'knowledge', 'folders.json'); }
  private originals(datasetId: string) { return path.join(this.paths.root, 'knowledge', 'originals', datasetId); }

  credential(): KnowledgeCredentialStatus {
    const key = this.readKey();
    return key ? { configured: true, masked: key.length > 8 ? `${key.slice(0, 5)}••••${key.slice(-4)}` : '••••' } : { configured: false };
  }

  setCredential(value: string): KnowledgeCredentialStatus {
    const key = value.trim();
    if (!key) { if (existsSync(this.keyFile)) unlinkSync(this.keyFile); return { configured: false }; }
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统安全存储不可用');
    writeFileSync(this.keyFile, safeStorage.encryptString(key).toString('base64'), { mode: 0o600 });
    return this.credential();
  }

  private readKey(): string | undefined {
    if (!existsSync(this.keyFile) || !safeStorage.isEncryptionAvailable()) return undefined;
    try { return safeStorage.decryptString(Buffer.from(readFileSync(this.keyFile, 'utf8'), 'base64')); } catch { return undefined; }
  }

  private root(): string {
    const value = this.getBaseUrl().trim().replace(/\/+$/, '');
    if (!value) throw new Error('请先填写 RAGFlow 地址');
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('RAGFlow 地址必须使用 HTTP 或 HTTPS');
    return value.endsWith('/api/v1') ? value : `${value}/api/v1`;
  }

  private async request(resource: string, init: RequestInit = {}): Promise<Response> {
    const key = this.readKey();
    if (!key) throw new Error('请先填写 RAGFlow API 密钥');
    const response = await fetch(`${this.root()}${resource}`, { ...init, headers: { ...init.headers, Authorization: `Bearer ${key}` } });
    if (!response.ok) throw new Error(`RAGFlow 请求失败：HTTP ${response.status}`);
    return response;
  }

  private async json(resource: string, init: RequestInit = {}): Promise<Record<string, unknown>> {
    const data = await (await this.request(resource, init)).json() as Record<string, unknown>;
    if (data.code !== 0) throw new Error(String(data.message ?? 'RAGFlow 返回错误'));
    return data;
  }

  async datasets(): Promise<KnowledgeDataset[]> {
    const response = await this.json('/datasets?page=1&page_size=100&orderby=update_time&desc=true');
    const rows = Array.isArray(response.data) ? response.data as Array<Record<string, unknown>> : [];
    return rows.map((row) => ({
      id: String(row.id), name: String(row.name ?? ''), description: String(row.description ?? ''),
      documentCount: Number(row.document_count ?? 0), chunkCount: Number(row.chunk_count ?? 0),
      createdAt: row.create_date ? String(row.create_date) : undefined, updatedAt: row.update_date ? String(row.update_date) : undefined,
    }));
  }

  async createDataset(name: string, description = ''): Promise<KnowledgeDataset> {
    const response = await this.json('/datasets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim(), description: description.trim(), chunk_method: 'general' }) });
    const row = response.data as Record<string, unknown>;
    return { id: String(row.id), name: String(row.name), description: String(row.description ?? ''), documentCount: 0, chunkCount: 0 };
  }

  async documents(datasetId: string): Promise<KnowledgeDocument[]> {
    const response = await this.json(`/datasets/${encodeURIComponent(datasetId)}/documents?page=1&page_size=100&orderby=create_time&desc=true`);
    const data = response.data as Record<string, unknown> | undefined;
    const rows = Array.isArray(data?.docs) ? data.docs as Array<Record<string, unknown>> : [];
    const localRoot = this.originals(datasetId);
    return rows.map((row) => {
      const run = String(row.run ?? '').toLowerCase();
      const progress = Number(row.progress ?? 0);
      const status: KnowledgeDocument['status'] = run === '3' || run === 'done' || progress >= 1 ? 'ready' : run === '4' || run === 'fail' ? 'failed' : run === '1' || run === 'running' ? 'parsing' : 'waiting';
      const localPath = path.join(localRoot, path.basename(String(row.name ?? '')));
      return { id: String(row.id), datasetId, name: String(row.name ?? ''), sizeBytes: Number(row.size ?? 0), chunkCount: Number(row.chunk_count ?? 0), tokenCount: Number(row.token_count ?? 0), status, progress, createdAt: row.create_date ? String(row.create_date) : undefined, localPath: existsSync(localPath) ? localPath : undefined };
    });
  }

  async uploadFiles(datasetId: string, filePaths: string[]): Promise<KnowledgeSyncResult> {
    const directory = this.originals(datasetId); mkdirSync(directory, { recursive: true });
    let uploaded = 0; let failed = 0;
    for (const source of filePaths) {
      if (!supported.has(path.extname(source).toLowerCase())) { failed++; continue; }
      try {
        const target = path.join(directory, path.basename(source));
        if (path.resolve(source) !== path.resolve(target)) copyFileSync(source, target);
        const form = new FormData();
        form.append('file', await openAsBlob(target), path.basename(target));
        const upload = await this.json(`/datasets/${encodeURIComponent(datasetId)}/documents`, { method: 'POST', body: form });
        const docs = Array.isArray(upload.data) ? upload.data as Array<Record<string, unknown>> : [];
        const ids = docs.map((doc) => String(doc.id)).filter(Boolean);
        if (ids.length) await this.json(`/datasets/${encodeURIComponent(datasetId)}/chunks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_ids: ids }) });
        uploaded++;
      } catch { failed++; }
    }
    return { uploaded, skipped: 0, failed };
  }

  private loadFolders(): StoredFolder[] {
    try { return JSON.parse(readFileSync(this.folderFile, 'utf8')) as StoredFolder[]; } catch { return []; }
  }

  private saveFolders(rows: StoredFolder[]): void {
    mkdirSync(path.dirname(this.folderFile), { recursive: true });
    writeFileSync(this.folderFile, JSON.stringify(rows, null, 2));
  }

  folders(): KnowledgeFolder[] { return this.loadFolders().map(({ files: _files, ...row }) => row); }

  async addFolder(datasetId: string, directory: string): Promise<KnowledgeSyncResult> {
    const rows = this.loadFolders();
    if (!rows.some((row) => row.datasetId === datasetId && path.resolve(row.path) === path.resolve(directory))) rows.push({ datasetId, path: directory, enabled: true, fileCount: 0, files: {} });
    this.saveFolders(rows);
    return this.syncFolders();
  }

  removeFolder(datasetId: string, directory: string): KnowledgeFolder[] {
    const rows = this.loadFolders().filter((row) => row.datasetId !== datasetId || path.resolve(row.path) !== path.resolve(directory));
    this.saveFolders(rows); return this.folders();
  }

  async syncFolders(): Promise<KnowledgeSyncResult> {
    const rows = this.loadFolders(); let uploaded = 0; let skipped = 0; let failed = 0;
    for (const row of rows) {
      if (!row.enabled || !existsSync(row.path)) { row.error = existsSync(row.path) ? undefined : '文件夹不存在'; continue; }
      const files = walk(row.path); row.fileCount = files.length; row.error = undefined;
      for (const file of files) {
        const stat = statSync(file); const relative = path.relative(row.path, file); const fingerprint = `${stat.size}:${stat.mtimeMs}`;
        if (row.files[relative] === fingerprint) { skipped++; continue; }
        const result = await this.uploadFiles(row.datasetId, [file]);
        uploaded += result.uploaded; failed += result.failed;
        if (result.uploaded) row.files[relative] = fingerprint;
      }
      row.lastSyncAt = new Date().toISOString();
    }
    this.saveFolders(rows); return { uploaded, skipped, failed };
  }

  async search(datasetIds: string[], question: string): Promise<KnowledgeCitation[]> {
    const response = await this.json('/retrieval', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_ids: datasetIds, question: question.trim(), page: 1, page_size: 8, similarity_threshold: 0.2, vector_similarity_weight: 0.3, keyword: true }) });
    const data = response.data as Record<string, unknown> | undefined;
    const rows = Array.isArray(data?.chunks) ? data.chunks as Array<Record<string, unknown>> : [];
    return rows.map((row) => {
      const positions = row.positions as unknown[][] | undefined;
      const pageValue = positions?.[0]?.[0];
      return { id: String(row.id), datasetId: String(row.dataset_id ?? row.kb_id ?? ''), documentId: String(row.document_id ?? ''), documentName: String(row.document_name ?? row.document_keyword ?? row.docnm_kwd ?? ''), content: String(row.content ?? row.content_with_weight ?? ''), similarity: Number(row.similarity ?? 0), page: typeof pageValue === 'number' ? pageValue + 1 : undefined };
    });
  }
}
