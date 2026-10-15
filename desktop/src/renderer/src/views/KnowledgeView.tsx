import { useCallback, useEffect, useState } from 'react';
import { BookOpen, FileText, FolderSync, Loader2, MessageSquareText, Plus, RefreshCw, Trash2, Upload } from 'lucide-react';
import type { KnowledgeCitation, KnowledgeCredentialStatus, KnowledgeDataset, KnowledgeDocument, KnowledgeFolder } from '@shared/types';
import { useApp } from '../store';
import { useT } from '../i18n';

const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${Math.max(0, Math.round(value / 1024))} KB`;
const stateColor = { waiting: 'var(--orange)', parsing: 'var(--accent)', ready: 'var(--green)', failed: 'var(--red)' };

export default function KnowledgeView() {
  const { settings, updateSettings, currentSession, setTab } = useApp();
  const { t } = useT();
  const [credential, setCredential] = useState<KnowledgeCredentialStatus>({ configured: false });
  const [url, setUrl] = useState(settings?.ragflowUrl ?? '');
  const [mode, setMode] = useState<'local' | 'server'>(settings?.ragflowMode ?? 'local');
  const [key, setKey] = useState('');
  const [datasets, setDatasets] = useState<KnowledgeDataset[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [folders, setFolders] = useState<KnowledgeFolder[]>([]);
  const [name, setName] = useState('');
  const [question, setQuestion] = useState('');
  const [citations, setCitations] = useState<KnowledgeCitation[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async (datasetId = selectedId) => {
    try {
      const [nextCredential, nextDatasets, nextFolders] = await Promise.all([
        window.biodsh.knowledgeCredential(), window.biodsh.knowledgeDatasets(), window.biodsh.knowledgeFolders(),
      ]);
      setCredential(nextCredential); setDatasets(nextDatasets); setFolders(nextFolders);
      const id = nextDatasets.some((row) => row.id === datasetId) ? datasetId : nextDatasets[0]?.id ?? '';
      setSelectedId(id); setDocuments(id ? await window.biodsh.knowledgeDocuments(id) : []); setError('');
    } catch (reason) { setError(String(reason).slice(0, 180)); }
  }, [selectedId]);

  useEffect(() => { void window.biodsh.knowledgeCredential().then(setCredential); if (settings?.ragflowUrl) void load(); }, []);
  useEffect(() => { if (selectedId) void window.biodsh.knowledgeDocuments(selectedId).then(setDocuments).catch(() => undefined); }, [selectedId]);

  const configure = async () => {
    if (!url.trim() || (!key.trim() && !credential.configured)) return;
    setBusy('config');
    try {
      await updateSettings({ ragflowUrl: url.trim().replace(/\/+$/, ''), ragflowMode: mode });
      if (key.trim()) setCredential(await window.biodsh.knowledgeSetCredential(key));
      setKey(''); await load();
    } catch (reason) { setError(String(reason).slice(0, 180)); } finally { setBusy(''); }
  };

  const create = async () => {
    if (!name.trim()) return; setBusy('create');
    try { const row = await window.biodsh.knowledgeCreateDataset(name.trim()); setName(''); await load(row.id); }
    catch (reason) { setError(String(reason).slice(0, 180)); } finally { setBusy(''); }
  };

  const addFiles = async () => {
    if (!selectedId) return; setBusy('files');
    try { await window.biodsh.knowledgeAddFiles(selectedId); await load(selectedId); }
    catch (reason) { setError(String(reason).slice(0, 180)); } finally { setBusy(''); }
  };

  const addFolder = async () => {
    if (!selectedId) return; setBusy('folder');
    try { await window.biodsh.knowledgeAddFolder(selectedId); await load(selectedId); }
    catch (reason) { setError(String(reason).slice(0, 180)); } finally { setBusy(''); }
  };

  const sync = async () => {
    setBusy('sync'); try { await window.biodsh.knowledgeSyncFolders(); await load(selectedId); }
    catch (reason) { setError(String(reason).slice(0, 180)); } finally { setBusy(''); }
  };

  const search = async (send = false) => {
    if (!selectedId || !question.trim()) return; setBusy(send ? 'ask' : 'search');
    try {
      const rows = await window.biodsh.knowledgeSearch([selectedId], question.trim()); setCitations(rows); setError('');
      if (send) {
        if (!currentSession) throw new Error(t('请先新建或打开一个对话'));
        const context = rows.slice(0, 8).map((row, index) => `[资料${index + 1}] ${row.documentName}${row.page ? `，第 ${row.page} 页` : ''}\n${row.content}`).join('\n\n');
        const content = `${question.trim()}\n\n请优先根据以下知识库原文回答，并在结论后标注[资料编号]；资料不足时明确说明。\n\n${context}`;
        await window.biodsh.dshRpc('session.prompt', { sessionId: currentSession, mode: 'queue', content: [{ type: 'text', text: content }] });
        setTab('chat');
      }
    } catch (reason) { setError(String(reason).slice(0, 180)); } finally { setBusy(''); }
  };

  const selected = datasets.find((row) => row.id === selectedId);
  const selectedFolders = folders.filter((row) => row.datasetId === selectedId);
  return <div className="h-full flex flex-col">
    <header className="drag h-[52px] flex items-center pl-5 hairline-b" style={{ background: 'var(--bg)' }}><span className="t-title2">{t('知识库')}</span></header>
    <div className="flex-1 min-h-0 overflow-y-auto p-5">
      <div className="max-w-[1180px] mx-auto flex flex-col gap-4">
        <div className="card p-4 flex flex-col gap-3">
          <div className="seg self-start"><button className={mode === 'local' ? 'active' : ''} onClick={() => { setMode('local'); if (!url.trim() || url === settings?.ragflowUrl) setUrl('http://127.0.0.1:9380'); }}>{t('本机解析')}</button><button className={mode === 'server' ? 'active' : ''} onClick={() => { setMode('server'); if (url === 'http://127.0.0.1:9380') setUrl(''); }}>{t('服务器解析')}</button></div>
          <div className="t-caption">{mode === 'local' ? t('使用本机 RAGFlow，原文件和索引保留在当前电脑。') : t('文件上传到服务器，由服务器完成文档解析和向量计算。')}</div>
          <div className="grid md:grid-cols-[1fr_1fr_auto] gap-3 items-end">
          <label className="t-caption">{t('RAGFlow 服务器地址')}<input className="field mt-1 w-full" placeholder="http://服务器地址:9380" value={url} onChange={(e) => setUrl(e.target.value)} /></label>
          <label className="t-caption">{t('RAGFlow API 密钥')}<input className="field mt-1 w-full" type="password" placeholder={credential.masked ?? 'ragflow-...'} value={key} onChange={(e) => setKey(e.target.value)} /></label>
          <button className="btn btn-primary" disabled={busy === 'config' || !url.trim()} onClick={() => void configure()}>{busy === 'config' && <Loader2 size={14} className="spin" />}{t('连接')}</button>
          </div>
        </div>
        {error && <div className="card p-3 t-caption" style={{ color: 'var(--red)' }}>{error}</div>}
        <div className="grid lg:grid-cols-[280px_1fr] gap-4 min-h-[560px]">
          <aside className="card p-3 flex flex-col gap-2">
            <div className="flex items-center gap-2"><BookOpen size={16} /><span className="t-headline flex-1">{t('我的知识库')}</span><button className="p-1" onClick={() => void load()}><RefreshCw size={14} /></button></div>
            <div className="flex gap-2"><input className="field min-w-0 flex-1" placeholder={t('新知识库名称')} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void create(); }} /><button className="btn btn-primary !px-2" disabled={!name.trim() || !!busy} onClick={() => void create()}><Plus size={14} /></button></div>
            <div className="flex flex-col gap-1 mt-1">{datasets.map((row) => <button key={row.id} className={`text-left p-3 rounded-lg ${selectedId === row.id ? 'bg-[var(--accent-soft)]' : 'hover:bg-[var(--fill)]'}`} onClick={() => setSelectedId(row.id)}><div className="font-medium truncate">{row.name}</div><div className="t-caption mt-1">{row.documentCount} {t('个文件')} · {row.chunkCount} {t('个片段')}</div></button>)}{datasets.length === 0 && <div className="t-caption p-4 text-center">{t('连接后创建第一个知识库')}</div>}</div>
          </aside>
          <main className="flex flex-col gap-4 min-w-0">
            {!selected ? <div className="card p-10 text-center t-caption">{t('请选择或创建知识库')}</div> : <>
              <div className="card p-4 flex flex-wrap items-center gap-2"><div className="flex-1"><div className="t-headline">{selected.name}</div><div className="t-caption">{selected.description || t('Word 和 PDF 资料将由 RAGFlow 解析并建立索引')}</div></div><button className="btn btn-ghost" disabled={!!busy} onClick={() => void addFiles()}><Upload size={14} />{t('添加文件')}</button><button className="btn btn-ghost" disabled={!!busy} onClick={() => void addFolder()}><FolderSync size={14} />{t('监控文件夹')}</button><button className="btn btn-ghost" disabled={!!busy} onClick={() => void sync()}>{busy === 'sync' ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}{t('立即同步')}</button></div>
              {selectedFolders.length > 0 && <div className="card p-3"><div className="t-caption mb-2">{t('自动同步文件夹')}</div>{selectedFolders.map((row) => <div className="flex items-center gap-2 py-1" key={row.path}><FolderSync size={13} /><span className="flex-1 truncate" title={row.path}>{row.path}</span><span className="t-caption">{row.fileCount} {t('个文件')}</span><button className="p-1" title={t('停止监控')} onClick={() => void window.biodsh.knowledgeRemoveFolder(row.datasetId, row.path).then(setFolders)}><Trash2 size={13} /></button></div>)}</div>}
              <div className="card p-4"><div className="flex items-end gap-2"><label className="flex-1 t-caption">{t('检索或向知识库提问')}<textarea className="field mt-1 w-full min-h-[62px]" value={question} onChange={(e) => setQuestion(e.target.value)} /></label><button className="btn btn-ghost" disabled={!question.trim() || !!busy} onClick={() => void search(false)}>{t('检索')}</button><button className="btn btn-primary" disabled={!question.trim() || !!busy} onClick={() => void search(true)}>{busy === 'ask' ? <Loader2 size={14} className="spin" /> : <MessageSquareText size={14} />}{t('在对话中提问')}</button></div>
                {citations.length > 0 && <div className="mt-3 flex flex-col gap-2">{citations.map((row) => <div className="p-3 rounded-lg" style={{ background: 'var(--fill)' }} key={row.id}><div className="flex gap-2 t-caption"><span className="font-medium">{row.documentName}</span>{row.page && <span>· {t('第 {page} 页', { page: row.page })}</span>}<span className="ml-auto">{Math.round(row.similarity * 100)}%</span></div><div className="t-body mt-1 whitespace-pre-wrap line-clamp-4">{row.content}</div></div>)}</div>}
              </div>
              <div className="card overflow-hidden"><div className="p-3 hairline-b t-headline">{t('文件')}</div>{documents.length === 0 ? <div className="p-8 text-center t-caption">{t('还没有文件')}</div> : documents.map((row) => <div key={row.id} className="flex items-center gap-3 px-4 py-3 hairline-b last:border-0"><FileText size={16} /><div className="flex-1 min-w-0"><div className="truncate">{row.name}</div><div className="t-caption">{bytes(row.sizeBytes)} · {row.chunkCount} {t('个片段')}{row.localPath ? ` · ${t('本地已保存')}` : ''}</div></div><span className="w-2 h-2 rounded-full" style={{ background: stateColor[row.status] }} /><span className="t-caption">{t(row.status)}</span>{row.localPath && <button className="btn btn-ghost !px-2" onClick={() => void window.biodsh.openPath(row.localPath!)}>{t('打开')}</button>}</div>)}</div>
            </>}
          </main>
        </div>
      </div>
    </div>
  </div>;
}
