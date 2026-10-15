use keyring::Entry;
use reqwest::blocking::{multipart, Client, Response};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const SERVICE: &str = "BioDSH RAGFlow";
const ACCOUNT: &str = "api-key";

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialStatus { configured: bool, #[serde(skip_serializing_if = "Option::is_none")] masked: Option<String> }

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Folder { dataset_id: String, path: String, enabled: bool, file_count: usize, #[serde(default)] last_sync_at: Option<String>, #[serde(default)] error: Option<String>, #[serde(default)] files: BTreeMap<String, String> }

#[derive(Default, Serialize)]
pub struct SyncResult { pub uploaded: usize, pub skipped: usize, pub failed: usize }

fn entry() -> Result<Entry, String> { Entry::new(SERVICE, ACCOUNT).map_err(|e| format!("系统安全存储不可用：{e}")) }
fn key() -> Result<String, String> { entry()?.get_password().map_err(|_| "请先填写 RAGFlow API 密钥".into()) }

pub fn credential() -> CredentialStatus {
    match key() {
        Ok(value) => CredentialStatus { configured: true, masked: Some(if value.len() > 8 { format!("{}••••{}", &value[..5], &value[value.len() - 4..]) } else { "••••".into() }) },
        Err(_) => CredentialStatus { configured: false, masked: None },
    }
}

pub fn set_credential(value: &str) -> Result<CredentialStatus, String> {
    let item = entry()?;
    if value.trim().is_empty() { let _ = item.delete_credential(); }
    else { item.set_password(value.trim()).map_err(|e| format!("保存 RAGFlow 密钥失败：{e}"))?; }
    Ok(credential())
}

fn api_root(configured: &str) -> Result<String, String> {
    let base = configured.trim().trim_end_matches('/');
    if base.is_empty() { return Err("请先填写 RAGFlow 地址".into()); }
    if !base.starts_with("http://") && !base.starts_with("https://") { return Err("RAGFlow 地址必须使用 HTTP 或 HTTPS".into()); }
    Ok(if base.ends_with("/api/v1") { base.into() } else { format!("{base}/api/v1") })
}

fn client() -> Result<Client, String> { Client::builder().timeout(Duration::from_secs(1800)).build().map_err(|e| e.to_string()) }

fn parse(response: Response) -> Result<Value, String> {
    let status = response.status();
    if !status.is_success() { return Err(format!("RAGFlow 请求失败：HTTP {}", status.as_u16())); }
    let value: Value = response.json().map_err(|e| format!("RAGFlow 响应无效：{e}"))?;
    if value.get("code").and_then(Value::as_i64) != Some(0) { return Err(value.get("message").and_then(Value::as_str).unwrap_or("RAGFlow 返回错误").into()); }
    Ok(value)
}

fn get(url: &str, resource: &str) -> Result<Value, String> { parse(client()?.get(format!("{}{}", api_root(url)?, resource)).bearer_auth(key()?).send().map_err(|e| e.to_string())?) }
fn post(url: &str, resource: &str, body: Value) -> Result<Value, String> { parse(client()?.post(format!("{}{}", api_root(url)?, resource)).bearer_auth(key()?).json(&body).send().map_err(|e| e.to_string())?) }

pub fn datasets(url: &str) -> Result<Value, String> {
    let value = get(url, "/datasets?page=1&page_size=100&orderby=update_time&desc=true")?;
    let rows = value.get("data").and_then(Value::as_array).cloned().unwrap_or_default().into_iter().map(|row| json!({
        "id": row["id"], "name": row["name"], "description": row.get("description").unwrap_or(&Value::Null),
        "documentCount": row.get("document_count").and_then(Value::as_u64).unwrap_or(0), "chunkCount": row.get("chunk_count").and_then(Value::as_u64).unwrap_or(0),
        "createdAt": row.get("create_date"), "updatedAt": row.get("update_date")
    })).collect::<Vec<_>>();
    Ok(Value::Array(rows))
}

pub fn create_dataset(url: &str, name: &str, description: &str) -> Result<Value, String> {
    let value = post(url, "/datasets", json!({ "name": name.trim(), "description": description.trim(), "chunk_method": "general" }))?;
    let row = &value["data"];
    Ok(json!({ "id": row["id"], "name": row["name"], "description": row.get("description"), "documentCount": 0, "chunkCount": 0 }))
}

pub fn documents(root: &Path, url: &str, dataset_id: &str) -> Result<Value, String> {
    let value = get(url, &format!("/datasets/{dataset_id}/documents?page=1&page_size=100&orderby=create_time&desc=true"))?;
    let rows = value.pointer("/data/docs").and_then(Value::as_array).cloned().unwrap_or_default().into_iter().map(|row| {
        let run = row.get("run").map(|v| v.as_str().map(str::to_owned).unwrap_or_else(|| v.to_string())).unwrap_or_default().to_lowercase();
        let progress = row.get("progress").and_then(Value::as_f64).unwrap_or(0.0);
        let status = if run == "3" || run == "done" || progress >= 1.0 { "ready" } else if run == "4" || run == "fail" { "failed" } else if run == "1" || run == "running" { "parsing" } else { "waiting" };
        let name = row.get("name").and_then(Value::as_str).unwrap_or_default();
        let local = root.join("knowledge/originals").join(dataset_id).join(Path::new(name).file_name().unwrap_or_default());
        json!({ "id": row["id"], "datasetId": dataset_id, "name": name, "sizeBytes": row.get("size").and_then(Value::as_u64).unwrap_or(0), "chunkCount": row.get("chunk_count").and_then(Value::as_u64).unwrap_or(0), "tokenCount": row.get("token_count").and_then(Value::as_u64).unwrap_or(0), "status": status, "progress": progress, "createdAt": row.get("create_date"), "localPath": if local.exists() { Some(local.to_string_lossy().to_string()) } else { None } })
    }).collect::<Vec<_>>();
    Ok(Value::Array(rows))
}

fn supported(path: &Path) -> bool { matches!(path.extension().and_then(|v| v.to_str()).unwrap_or_default().to_lowercase().as_str(), "pdf" | "doc" | "docx") }

pub fn upload_files(root: &Path, url: &str, dataset_id: &str, paths: &[PathBuf]) -> SyncResult {
    let directory = root.join("knowledge/originals").join(dataset_id); let _ = fs::create_dir_all(&directory);
    let mut result = SyncResult::default();
    for source in paths {
        if !supported(source) { result.failed += 1; continue; }
        let Some(name) = source.file_name() else { result.failed += 1; continue };
        let target = directory.join(name);
        let attempt = (|| -> Result<(), String> {
            if source != &target { fs::copy(source, &target).map_err(|e| e.to_string())?; }
            let form = multipart::Form::new().file("file", &target).map_err(|e| e.to_string())?;
            let upload = parse(client()?.post(format!("{}/datasets/{dataset_id}/documents", api_root(url)?)).bearer_auth(key()?).multipart(form).send().map_err(|e| e.to_string())?)?;
            let ids = upload.get("data").and_then(Value::as_array).cloned().unwrap_or_default().into_iter()
                .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_owned)).collect::<Vec<_>>();
            if !ids.is_empty() { post(url, &format!("/datasets/{dataset_id}/chunks"), json!({ "document_ids": ids }))?; }
            Ok(())
        })();
        if attempt.is_ok() { result.uploaded += 1; } else { result.failed += 1; }
    }
    result
}

fn folder_file(root: &Path) -> PathBuf { root.join("knowledge/folders.json") }
fn load_folders(root: &Path) -> Vec<Folder> { fs::read_to_string(folder_file(root)).ok().and_then(|v| serde_json::from_str(&v).ok()).unwrap_or_default() }
fn save_folders(root: &Path, rows: &[Folder]) { let file = folder_file(root); if let Some(parent) = file.parent() { let _ = fs::create_dir_all(parent); } let _ = fs::write(file, serde_json::to_string_pretty(rows).unwrap_or_default()); }
pub fn folders(root: &Path) -> Vec<Folder> { load_folders(root) }

fn walk(path: &Path, out: &mut Vec<PathBuf>) {
    if let Ok(entries) = fs::read_dir(path) { for entry in entries.flatten() { let current = entry.path(); if current.is_dir() { walk(&current, out); } else if supported(&current) { out.push(current); } } }
}

pub fn add_folder(root: &Path, url: &str, dataset_id: &str, path: &str) -> SyncResult {
    let mut rows = load_folders(root);
    if !rows.iter().any(|row| row.dataset_id == dataset_id && row.path == path) { rows.push(Folder { dataset_id: dataset_id.into(), path: path.into(), enabled: true, file_count: 0, last_sync_at: None, error: None, files: BTreeMap::new() }); }
    save_folders(root, &rows); sync_folders(root, url)
}

pub fn remove_folder(root: &Path, dataset_id: &str, path: &str) -> Vec<Folder> {
    let rows = load_folders(root).into_iter().filter(|row| row.dataset_id != dataset_id || row.path != path).collect::<Vec<_>>(); save_folders(root, &rows); rows
}

pub fn sync_folders(root: &Path, url: &str) -> SyncResult {
    let mut rows = load_folders(root); let mut result = SyncResult::default();
    for row in &mut rows {
        let directory = PathBuf::from(&row.path);
        if !row.enabled || !directory.exists() { row.error = Some("文件夹不存在".into()); continue; }
        let mut files = Vec::new(); walk(&directory, &mut files); row.file_count = files.len(); row.error = None;
        for file in files {
            let Ok(meta) = fs::metadata(&file) else { result.failed += 1; continue };
            let relative = file.strip_prefix(&directory).unwrap_or(&file).to_string_lossy().to_string();
            let modified = meta.modified().ok().and_then(|v| v.duration_since(UNIX_EPOCH).ok()).map(|v| v.as_millis()).unwrap_or(0);
            let fingerprint = format!("{}:{modified}", meta.len());
            if row.files.get(&relative) == Some(&fingerprint) { result.skipped += 1; continue; }
            let one = upload_files(root, url, &row.dataset_id, &[file]); result.uploaded += one.uploaded; result.failed += one.failed;
            if one.uploaded > 0 { row.files.insert(relative, fingerprint); }
        }
        row.last_sync_at = Some(SystemTime::now().duration_since(UNIX_EPOCH).map(|v| v.as_secs().to_string()).unwrap_or_default());
    }
    save_folders(root, &rows); result
}

pub fn search(url: &str, dataset_ids: Vec<String>, question: &str) -> Result<Value, String> {
    let value = post(url, "/retrieval", json!({ "dataset_ids": dataset_ids, "question": question.trim(), "page": 1, "page_size": 8, "similarity_threshold": 0.2, "vector_similarity_weight": 0.3, "keyword": true }))?;
    let rows = value.pointer("/data/chunks").and_then(Value::as_array).cloned().unwrap_or_default().into_iter().map(|row| {
        let page = row.pointer("/positions/0/0").and_then(Value::as_u64).map(|v| v + 1);
        json!({ "id": row["id"], "datasetId": row.get("dataset_id").or_else(|| row.get("kb_id")), "documentId": row.get("document_id"), "documentName": row.get("document_name").or_else(|| row.get("document_keyword")).or_else(|| row.get("docnm_kwd")), "content": row.get("content").or_else(|| row.get("content_with_weight")), "similarity": row.get("similarity").and_then(Value::as_f64).unwrap_or(0.0), "page": page })
    }).collect::<Vec<_>>();
    Ok(Value::Array(rows))
}

#[cfg(test)]
mod tests {
    use super::walk;
    use std::fs;

    #[test]
    fn folder_scan_only_includes_supported_documents() {
        let root = std::env::temp_dir().join(format!("biodsh-knowledge-{}", uuid::Uuid::new_v4()));
        let nested = root.join("nested");
        fs::create_dir_all(&nested).unwrap();
        fs::write(root.join("paper.pdf"), b"pdf").unwrap();
        fs::write(nested.join("notes.docx"), b"docx").unwrap();
        fs::write(nested.join("ignore.txt"), b"txt").unwrap();

        let mut files = Vec::new();
        walk(&root, &mut files);
        files.sort();

        assert_eq!(files.len(), 2);
        assert!(files.iter().any(|path| path.ends_with("paper.pdf")));
        assert!(files.iter().any(|path| path.ends_with("notes.docx")));
        fs::remove_dir_all(root).unwrap();
    }
}
