//! Persist the complete task before queueing its short, user-visible message.
use crate::paths::AppPaths;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs, io::{Read, Write}, path::{Path, PathBuf}, sync::Mutex, time::{SystemTime, UNIX_EPOCH}};

const INVALID: &str = "研究交接内容不完整，请重新选择研究建议";
const STORAGE: &str = "无法保存完整研究任务，原研究内容已保留，请重试";
const BINDING: &str = "研究项目与目标对话不匹配，请重新打开项目后重试";
const READY: &str = "RESEARCH_HANDOFF_NOT_READY";
const ACCOUNT: &str = "登录已变化，请重新打开项目后再交接";
const MAX_RECORD: usize = 2 * 1024 * 1024;
static WRITES: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Handoff { session_id: String, request_id: String, project: String, summary: String, context: String }

fn reject_link(path: &Path) -> Result<(), String> {
    let meta = fs::symlink_metadata(path).map_err(|_| STORAGE)?;
    #[cfg(windows)] let linked = { use std::os::windows::fs::MetadataExt; meta.file_attributes() & 0x400 != 0 };
    #[cfg(not(windows))] let linked = meta.file_type().is_symlink();
    if linked || meta.file_type().is_symlink() { return Err(STORAGE.into()); }
    Ok(())
}
fn read_json(file: &Path, limit: usize) -> Result<Value, String> {
    reject_link(file)?;
    let file = fs::File::open(file).map_err(|_| STORAGE)?;
    let mut bytes = Vec::new(); file.take((limit + 1) as u64).read_to_end(&mut bytes).map_err(|_| STORAGE)?;
    if bytes.len() > limit { return Err(STORAGE.into()); }
    serde_json::from_slice(&bytes).map_err(|_| STORAGE.into())
}
fn canonical_project(project: &str) -> Result<PathBuf, String> {
    crate::files::validate_local_path(project)?;
    let path = fs::canonicalize(project).map_err(|_| BINDING)?;
    if !path.is_dir() { return Err(BINDING.into()); }
    Ok(path)
}
fn validate(home: &Path, payload: Value) -> Result<Handoff, String> {
    let mut task: Handoff = serde_json::from_value(payload).map_err(|_| INVALID)?;
    if task.session_id.is_empty() || task.session_id.len() > 128 || !task.session_id.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-'|b'_'))
        || task.summary.trim().is_empty() || task.summary.chars().count() > 512 || task.summary.contains(['\0', '\r', '\n'])
        || task.context.trim().is_empty() || task.context.len() > 1_500_000 || task.context.contains('\0')
        || task.project.len() > 8192 { return Err(INVALID.into()); }
    task.request_id = uuid::Uuid::parse_str(&task.request_id).map_err(|_| INVALID)?.to_string();
    if !home.is_absolute() || !home.is_dir() { return Err(STORAGE.into()); }
    for parent in home.ancestors() { reject_link(parent)?; }
    task.project = crate::paths::strip_unc(canonical_project(&task.project)?).to_string_lossy().to_string();
    Ok(task)
}
fn check_binding(home: &Path, task: &Handoff) -> Result<(), String> {
    reject_link(&home.join("storages"))?;
    let document = read_json(&home.join("storages/workspace.json"), 8 * 1024 * 1024)?;
    if document.pointer("/global/archivedSessionIds").and_then(Value::as_array).is_some_and(|ids| ids.iter().any(|id| id == &task.session_id)) { return Err(BINDING.into()); }
    let project = canonical_project(&task.project)?;
    let matches = document.pointer("/tables/workspaces").and_then(Value::as_object).into_iter().flat_map(|rows| rows.iter()).filter(|(id, row)| {
        let active = document.pointer("/global/workspaceIds").and_then(Value::as_array).is_none_or(|ids| ids.iter().any(|value| value == *id));
        active && row["sessionIds"].as_array().is_some_and(|ids| ids.iter().any(|id| id == &task.session_id))
    }).collect::<Vec<_>>();
    if matches.len() != 1 { return Err(BINDING.into()); }
    let selected = matches[0].1["path"].as_str().ok_or(BINDING)?;
    if canonical_project(selected)? != project { return Err(BINDING.into()); }
    Ok(())
}
fn check_ready(home: &Path) -> Result<PathBuf, String> {
    let directory = home.join("workflow-bindings");
    reject_link(&directory).map_err(|_| READY)?;
    let ready = read_json(&directory.join("ready.json"), 8192).map_err(|_| READY)?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_| READY)?.as_millis() as u64;
    if ready["schema"] != 1 || ready["researchContext"] != true || !ready["at"].as_u64().is_some_and(|at| at <= now && now - at <= 20000) { return Err(READY.into()); }
    Ok(directory)
}
fn record_path(directory: &Path, session: &str) -> Result<PathBuf, String> {
    let mut directory = directory.to_path_buf();
    for name in ["research-handoffs", session] {
        directory.push(name);
        match fs::create_dir(&directory) {
            Ok(()) => {}, Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}, Err(_) => return Err(STORAGE.into()),
        }
        reject_link(&directory)?;
        if !directory.is_dir() { return Err(STORAGE.into()); }
    }
    Ok(directory)
}
fn submit(home: &Path, payload: Value, owner: &str, check_owner: impl Fn() -> Result<(), String>, send: impl FnOnce(Value) -> Result<Value, String>) -> Result<Value, String> {
    check_owner()?;
    let task = validate(home, payload)?;
    let guard = check_ready(home)?;
    check_binding(home, &task)?;
    let mut record = serde_json::to_value(&task).map_err(|_| INVALID)?;
    record["schema"] = json!(1); record["ownerId"] = json!(owner);
    let bytes = serde_json::to_vec(&record).map_err(|_| INVALID)?;
    if bytes.len() > MAX_RECORD { return Err(INVALID.into()); }
    {
        let _lock = WRITES.lock().map_err(|_| STORAGE)?;
        let directory = record_path(&guard, &task.session_id)?;
        let target = directory.join(format!("{}.json", task.request_id));
        match fs::symlink_metadata(&target) {
            Ok(_) => {
                if read_json(&target, MAX_RECORD)? != record { return Err("此交接编号已对应其他研究任务，请重新选择建议".into()); }
            },
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let temp = directory.join(format!("{}.tmp", uuid::Uuid::new_v4()));
                let result = (|| -> Result<(), String> {
                    let mut file = fs::OpenOptions::new().create_new(true).write(true).open(&temp).map_err(|_| STORAGE)?;
                    file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|_| STORAGE)?; drop(file);
                    check_owner()?; reject_link(&directory)?;
                    fs::rename(&temp, &target).map_err(|_| STORAGE)?;
                    Ok(())
                })();
                if result.is_err() { let _ = fs::remove_file(&temp); }
                result?;
            },
            Err(_) => return Err(STORAGE.into()),
        }
    }
    check_owner()?; check_ready(home)?; check_binding(home, &task)?;
    // A lost acknowledgement must retry this exact ID; the durable record stays available.
    let result = send(json!({"sessionId":task.session_id,"requestId":task.request_id,"mode":"queue","content":[{"type":"text","text":task.summary}]}))
        .map_err(|_| "交接状态暂时无法确认，请重试同一研究任务；完整内容已保留")?;
    if result["accepted"] != true { return Err("主对话尚未接收研究任务，请稍后重试；完整内容已保留".into()); }
    check_owner()?;
    Ok(json!({"accepted":true,"sessionId":task.session_id,"requestId":task.request_id}))
}
pub fn run(paths: &AppPaths, url: &str, payload: Value) -> Result<Value, String> {
    let user = crate::hub_auth::require(&paths.root, "")?;
    crate::hub_auth::audit(&paths.root, &user, "research_handoff");
    let result = submit(&paths.dsh_home, payload, &user.id, || {
        if crate::hub_auth::require(&paths.root, "")?.id != user.id { return Err(ACCOUNT.into()); }
        Ok(())
    }, |body| crate::dsh_call(url, "session.prompt", body));
    crate::hub_auth::audit(&paths.root, &user, if result.is_ok() { "research_handoff_accepted" } else { "research_handoff_unconfirmed" });
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (PathBuf, Value) {
        let root = std::env::temp_dir().join(format!("omaax-handoff-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("storages")).unwrap(); fs::create_dir(root.join("project")).unwrap(); fs::create_dir(root.join("other-project")).unwrap(); fs::create_dir(root.join("workflow-bindings")).unwrap();
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64;
        fs::write(root.join("workflow-bindings/ready.json"), json!({"schema":1,"researchContext":true,"at":now}).to_string()).unwrap();
        fs::write(root.join("storages/workspace.json"), json!({"global":{"workspaceIds":["p1"],"archivedSessionIds":[]},"tables":{"workspaces":{"p1":{"path":root.join("project"),"sessionIds":["session-a"]}}}}).to_string()).unwrap();
        let payload = json!({"sessionId":"session-a","requestId":uuid::Uuid::new_v4().to_string(),"project":root.join("project"),"summary":"请执行所选研究建议。","context":"Full task: requirements, steps, evidence and limitations.\n原始数据保持不变。"});
        (root, payload)
    }
    fn path(home: &Path, payload: &Value) -> PathBuf { home.join("workflow-bindings/research-handoffs/session-a").join(format!("{}.json", payload["requestId"].as_str().unwrap())) }
    #[test]
    fn short_message_is_queued_only_after_full_context_is_durable() {
        let (root, payload) = fixture(); let expected = payload.clone();
        let result = submit(&root, payload.clone(), "account-a", || Ok(()), |prompt| {
            let record = read_json(&path(&root, &expected), MAX_RECORD).unwrap();
            assert_eq!(record["context"], expected["context"]); assert_eq!(record["ownerId"], "account-a");
            assert_eq!(prompt["requestId"], expected["requestId"]); assert_eq!(prompt["mode"], "queue");
            assert_eq!(prompt["content"], json!([{"type":"text","text":expected["summary"]}]));
            assert!(!prompt.to_string().contains("Full task")); Ok(json!({"accepted":true}))
        }).unwrap();
        assert_eq!(result["accepted"], true); fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn retry_keeps_identity_and_refuses_different_context_or_owner() {
        let (root, payload) = fixture();
        assert!(submit(&root, payload.clone(), "account-a", || Ok(()), |_| Err("lost response".into())).is_err());
        let file = path(&root, &payload); let before = fs::read(&file).unwrap();
        assert!(submit(&root, payload.clone(), "account-a", || Ok(()), |prompt| { assert_eq!(prompt["requestId"], payload["requestId"]); Ok(json!({"accepted":true})) }).is_ok());
        assert_eq!(fs::read(&file).unwrap(), before);
        let mut changed = payload.clone(); changed["context"] = json!("another task");
        for (body, owner) in [(changed,"account-a"),(payload,"account-b")] { assert!(submit(&root, body, owner, || Ok(()), |_| panic!("must not submit conflicting task")).is_err()); }
        assert_eq!(fs::read(file).unwrap(), before); fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn incomplete_acknowledgement_is_not_reported_as_success() {
        let (root, payload) = fixture();
        for response in [json!({}),json!({"ok":true}),json!({"accepted":false}),Value::Null] {
            assert!(submit(&root, payload.clone(), "account-a", || Ok(()), |_| Ok(response)).is_err());
            assert!(path(&root, &payload).exists());
        }
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn account_project_session_and_plugin_checks_prevent_submission() {
        let (root, payload) = fixture();
        assert!(submit(&root, payload.clone(), "account-a", || Err(ACCOUNT.into()), |_| panic!()).is_err());
        for (key,value) in [("sessionId",json!("other-session")),("sessionId",json!("../escape")),("requestId",json!("../escape")),("project",json!(root.join("other-project")))] {
            let mut changed = payload.clone(); changed[key] = value;
            assert!(submit(&root, changed, "account-a", || Ok(()), |_| panic!()).is_err());
        }
        assert!(!root.join("workflow-bindings/research-handoffs").exists());
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64;
        for ready in [json!({"schema":1,"at":now}),json!({"schema":1,"researchContext":true,"at":now-20001})] {
            fs::write(root.join("workflow-bindings/ready.json"), ready.to_string()).unwrap();
            assert_eq!(submit(&root, payload.clone(), "account-a", || Ok(()), |_| panic!()).unwrap_err(), READY);
        }
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn account_change_during_commit_removes_temporary_record_without_queueing() {
        let (root, payload) = fixture(); let checks = std::cell::Cell::new(0);
        assert!(submit(&root, payload.clone(), "account-a", || { checks.set(checks.get()+1); if checks.get()>1 { Err(ACCOUNT.into()) } else { Ok(()) } }, |_| panic!()).is_err());
        assert!(!path(&root, &payload).exists());
        assert_eq!(fs::read_dir(root.join("workflow-bindings/research-handoffs/session-a")).unwrap().count(), 0);
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(windows)]
    #[test]
    fn linked_record_directory_is_rejected_without_touching_target() {
        use std::os::windows::process::CommandExt;
        let (root, payload) = fixture(); let target = root.join("outside"); fs::create_dir(&target).unwrap(); fs::write(target.join("original"), b"preserve").unwrap();
        let link = root.join("workflow-bindings").join("research-handoffs");
        let output = std::process::Command::new("cmd.exe").args(["/D","/C","mklink","/J"]).arg(&link).arg(&target).creation_flags(0x08000000).output().unwrap(); assert!(output.status.success(), "junction fixture: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        assert!(submit(&root, payload, "account-a", || Ok(()), |_| panic!()).is_err());
        assert_eq!(fs::read_dir(&target).unwrap().count(), 1); assert_eq!(fs::read(target.join("original")).unwrap(), b"preserve");
        fs::remove_dir(link).unwrap(); fs::remove_dir_all(root).unwrap();
    }
}
