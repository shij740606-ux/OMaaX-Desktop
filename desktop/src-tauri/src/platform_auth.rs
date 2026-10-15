use keyring::Entry;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io;
use std::path::Path;

const SERVICE: &str = "BioDSH Platform";
const ACCOUNT: &str = "desktop-session";
const INVITATION_ACCOUNT: &str = "pending-invitation";
static SESSION_STORAGE: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredSession {
    base_url: String,
    api_path: String,
    access_token: String,
    refresh_token: String,
    #[serde(default)]
    device_token: String,
    #[serde(default)]
    device_id: String,
}

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, ACCOUNT).map_err(|e| format!("系统安全存储不可用：{e}"))
}

fn invitation_entry() -> Result<Entry, String> {
    Entry::new(SERVICE, INVITATION_ACCOUNT).map_err(|e| format!("系统安全存储不可用：{e}"))
}

fn clear_invitation() {
    if let Ok(item) = invitation_entry() { let _ = item.delete_credential(); }
}

fn load() -> Option<StoredSession> {
    entry()
        .ok()?
        .get_password()
        .ok()
        .and_then(|v| serde_json::from_str(&v).ok())
}

fn save(session: &StoredSession) -> Result<(), String> {
    let _guard = SESSION_STORAGE.lock().map_err(|_| "安全存储忙，请重试")?;
    save_unlocked(session)
}

fn save_unlocked(session: &StoredSession) -> Result<(), String> {
    let value = serde_json::to_string(session).map_err(|e| e.to_string())?;
    entry()?
        .set_password(&value)
        .map_err(|e| format!("保存登录凭据失败：{e}"))
}

fn same_session(left: &StoredSession, right: &StoredSession) -> bool {
    left.base_url == right.base_url && left.device_token == right.device_token
        && left.refresh_token == right.refresh_token
}

fn clear_if_current(session: &StoredSession) {
    let Ok(_guard) = SESSION_STORAGE.lock() else { return; };
    if !load().is_some_and(|current| same_session(&current, session)) { return; }
    clear_agent_grant();
    if let Ok(item) = entry() {
        let _ = item.delete_credential();
    }
}

fn clear_agent_grant() {
    if let Ok(item) = Entry::new("BioDSH.ProtectedAgent", "desktop-agent-capability") {
        let _ = item.delete_credential();
    }
}

fn parse(response: ureq::Response) -> Result<serde_json::Value, String> {
    response
        .into_json()
        .map_err(|e| format!("平台响应无效：{e}"))
}

fn discover(configured_url: &str) -> Result<(String, String), String> {
    let base = configured_url.trim().trim_end_matches('/').to_string();
    if base.is_empty() {
        return Err("请先填写平台地址".into());
    }
    let metadata = ureq::get(&format!("{base}/.well-known/biodsh-platform"))
        .timeout(std::time::Duration::from_secs(15))
        .call()
        .map_err(|e| format!("平台发现失败：{e}"))
        .and_then(parse)?;
    let api = metadata
        .pointer("/api_versions/v1")
        .and_then(|v| v.as_str())
        .filter(|v| v.starts_with('/'))
        .ok_or_else(|| "平台没有提供兼容的 v1 API".to_string())?;
    Ok((base, api.to_string()))
}

fn refresh(session: &mut StoredSession) -> Result<(), String> {
    let previous = session.clone();
    let (path, body) = if session.device_token.is_empty() {
        ("/auth/refresh", serde_json::json!({"refresh_token": session.refresh_token}))
    } else {
        ("/auth/devices/exchange", serde_json::json!({"device_token": session.device_token}))
    };
    let response = ureq::post(&format!(
        "{}{}{}",
        session.base_url, session.api_path, path
    ))
    .timeout(std::time::Duration::from_secs(15))
    .send_json(body)
    .map_err(|e| match e {
        ureq::Error::Status(401, _) => "登录已过期，设备授权失效，请重新授权".to_string(),
        _ => "计算服务暂时无法连接，凭据已保留，稍后自动重试".to_string(),
    })
    .and_then(parse)?;
    session.access_token = response["access_token"]
        .as_str()
        .ok_or("平台登录响应无效")?
        .to_string();
    if session.device_token.is_empty() { session.refresh_token = response["refresh_token"]
        .as_str()
        .ok_or("平台登录响应无效")?
        .to_string();
    }
    let _guard = SESSION_STORAGE.lock().map_err(|_| "安全存储忙，请重试")?;
    if !load().is_some_and(|current| same_session(&current, &previous)) {
        return Err("连接状态已变化，请刷新后重试".into());
    }
    save_unlocked(session)
}

fn get(session: &StoredSession, resource: &str) -> Result<serde_json::Value, u16> {
    match ureq::get(&format!(
        "{}{}{}",
        session.base_url, session.api_path, resource
    ))
    .set("Authorization", &format!("Bearer {}", session.access_token))
    .timeout(std::time::Duration::from_secs(15))
    .call()
    {
        Ok(response) => parse(response).map_err(|_| 502),
        Err(ureq::Error::Status(code, _)) => Err(code),
        Err(_) => Err(503),
    }
}

fn get_with_refresh(
    session: &mut StoredSession,
    resource: &str,
) -> Result<serde_json::Value, String> {
    match get(session, resource) {
        Ok(value) => Ok(value),
        Err(401) => {
            refresh(session)?;
            get(session, resource).map_err(|code| platform_status_message(code))
        }
        Err(code) => Err(platform_status_message(code)),
    }
}

fn patch(
    session: &StoredSession,
    resource: &str,
    body: serde_json::Value,
) -> Result<serde_json::Value, u16> {
    match ureq::patch(&format!(
        "{}{}{}",
        session.base_url, session.api_path, resource
    ))
    .set("Authorization", &format!("Bearer {}", session.access_token))
    .timeout(std::time::Duration::from_secs(15))
    .send_json(body)
    {
        Ok(response) => parse(response).map_err(|_| 502),
        Err(ureq::Error::Status(code, _)) => Err(code),
        Err(_) => Err(503),
    }
}

fn post(
    session: &StoredSession,
    resource: &str,
    body: serde_json::Value,
) -> Result<serde_json::Value, u16> {
    match ureq::post(&format!(
        "{}{}{}",
        session.base_url, session.api_path, resource
    ))
    .set("Authorization", &format!("Bearer {}", session.access_token))
    .timeout(std::time::Duration::from_secs(15))
    .send_json(body)
    {
        Ok(response) => parse(response).map_err(|_| 502),
        Err(ureq::Error::Status(code, _)) => Err(code),
        Err(_) => Err(503),
    }
}

fn post_with_refresh(
    session: &mut StoredSession,
    resource: &str,
    body: serde_json::Value,
) -> Result<serde_json::Value, String> {
    match post(session, resource, body.clone()) {
        Ok(value) => Ok(value),
        Err(401) => {
            refresh(session)?;
            post(session, resource, body).map_err(|code| platform_status_message(code))
        }
        Err(code) => Err(platform_status_message(code)),
    }
}

fn configured_session(configured_url: &str) -> Result<StoredSession, String> {
    let configured = configured_url.trim().trim_end_matches('/');
    let session = load().ok_or_else(|| "请先登录平台".to_string())?;
    if session.base_url != configured {
        return Err("请先登录平台".into());
    }
    Ok(session)
}

fn normalize_metric(value: &serde_json::Value) -> serde_json::Value {
    serde_json::json!({
        "id": value["id"], "nodeId": value["node_id"],
        "cpuPercent": value["cpu_percent"], "loadOne": value["load_one"],
        "memoryUsedBytes": value["memory_used_bytes"], "memoryTotalBytes": value["memory_total_bytes"],
        "memoryPercent": value["memory_percent"], "diskUsedBytes": value["disk_used_bytes"],
        "diskTotalBytes": value["disk_total_bytes"], "diskPercent": value["disk_percent"],
        "networkRxBytes": value["network_rx_bytes"], "networkTxBytes": value["network_tx_bytes"],
        "runningJobs": value["running_jobs"], "gpus": value["gpus"], "collectedAt": value["collected_at"]
    })
}

fn normalize_node(value: &serde_json::Value) -> serde_json::Value {
    let metric = if value["latest_metric"].is_null() {
        serde_json::Value::Null
    } else {
        normalize_metric(&value["latest_metric"])
    };
    serde_json::json!({
        "id": value["id"], "name": value["name"], "status": value["status"],
        "hostname": value["hostname"], "operatingSystem": value["operating_system"],
        "agentVersion": value["agent_version"], "capabilities": value["capabilities"],
        "latestMetric": metric, "lastHeartbeatAt": value["last_heartbeat_at"]
    })
}

fn normalize_job(value: &serde_json::Value) -> serde_json::Value {
    serde_json::json!({
        "id": value["id"], "projectId": value["project_id"], "nodeId": value["node_id"],
        "kind": value["kind"], "status": value["status"], "payload": value["payload"],
        "requestedResources": value["requested_resources"], "priority": value["priority"],
        "attempt": value["attempt"], "assignedAt": value["assigned_at"],
        "startedAt": value["started_at"], "finishedAt": value["finished_at"],
        "exitCode": value["exit_code"], "resultManifest": value["result_manifest"],
        "errorMessage": value["error_message"], "createdAt": value["created_at"],
        "updatedAt": value["updated_at"]
    })
}

fn normalize_job_event(value: &serde_json::Value) -> serde_json::Value {
    serde_json::json!({
        "id": value["id"], "jobId": value["job_id"], "sequence": value["sequence"],
        "kind": value["kind"], "message": value["message"], "payload": value["payload"],
        "createdAt": value["created_at"]
    })
}

fn normalize_artifact(value: &serde_json::Value) -> serde_json::Value {
    serde_json::json!({
        "id": value["id"], "jobId": value["job_id"], "relativePath": value["relative_path"],
        "sizeBytes": value["size_bytes"], "sha256": value["sha256"],
        "contentType": value["content_type"], "createdAt": value["created_at"]
    })
}

pub fn login(
    configured_url: &str,
    username: &str,
    password: &str,
) -> Result<serde_json::Value, String> {
    let target = tauri::Url::parse(configured_url.trim()).map_err(|_| "计算服务地址无效")?;
    if target.scheme() != "https" && !(target.scheme() == "http" && matches!(target.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"))) {
        return Err("设备授权需要 HTTPS 地址（本机回环地址除外）".into());
    }
    let (base_url, api_path) = discover(configured_url)?;
    let device_name = std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")).unwrap_or_else(|_| "OMaaX Desktop".into());
    let response = ureq::post(&format!("{base_url}{api_path}/auth/devices/authorize"))
        .timeout(std::time::Duration::from_secs(15))
        .send_json(serde_json::json!({ "username": username, "password": password, "device_name": device_name }))
        .map_err(|e| match e {
            ureq::Error::Status(401, _) => "用户名或密码错误".into(),
            ureq::Error::Status(404, _) => "计算服务尚未升级，暂不支持设备授权".into(),
            _ => format!("登录失败：{e}"),
        })
        .and_then(parse)?;
    let session = StoredSession {
        base_url,
        api_path,
        access_token: response["access_token"]
            .as_str()
            .ok_or("平台登录响应无效")?
            .to_string(),
        refresh_token: String::new(),
        device_token: response["device_token"].as_str().ok_or("设备授权响应无效")?.to_string(),
        device_id: response["device_id"].as_str().ok_or("设备授权响应无效")?.to_string(),
    };
    clear_agent_grant();
    if let Err(error) = save(&session) {
        let _ = ureq::post(&format!("{}{}/auth/devices/disconnect", session.base_url, session.api_path))
            .timeout(std::time::Duration::from_secs(5))
            .send_json(serde_json::json!({"device_token": session.device_token}));
        return Err(error);
    }
    clear_invitation();
    status(configured_url)
}

pub fn status(configured_url: &str) -> Result<serde_json::Value, String> {
    let configured = configured_url.trim().trim_end_matches('/');
    let Some(mut session) = load() else {
        return Ok(serde_json::json!({ "authenticated": false, "projects": [] }));
    };
    if session.base_url != configured {
        return Ok(serde_json::json!({ "authenticated": false, "projects": [] }));
    }
    let me = match get_with_refresh(&mut session, "/auth/me") {
        Ok(value) => value,
        Err(error) if error.contains("登录已过期") => {
            clear_if_current(&session);
            return Ok(serde_json::json!({ "authenticated": false, "projects": [] }));
        }
        Err(error) => return Ok(serde_json::json!({ "authenticated": false, "deviceAuthorized": !session.device_token.is_empty(), "connectionError": error, "projects": [] })),
    };
    let projects = match get_with_refresh(&mut session, "/projects") {
        Ok(value) => value,
        Err(error) => return Ok(serde_json::json!({ "authenticated": false, "deviceAuthorized": !session.device_token.is_empty(), "connectionError": error, "projects": [] })),
    };
    Ok(
        serde_json::json!({ "authenticated": true, "deviceAuthorized": !session.device_token.is_empty(), "deviceId": session.device_id, "user": { "id": me["id"], "username": me["username"], "displayName": me["display_name"], "avatarUrl": me["avatar_url"], "isActive": me["is_active"], "isAdmin": me["is_admin"] }, "projects": projects }),
    )
}

pub fn nodes(configured_url: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let values = get_with_refresh(&mut session, "/nodes")?;
    let rows = values
        .as_array()
        .ok_or_else(|| "服务器列表响应无效".to_string())?;
    Ok(serde_json::Value::Array(
        rows.iter().map(normalize_node).collect(),
    ))
}

pub fn node_metrics(
    configured_url: &str,
    node_id: &str,
    minutes: u32,
) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let resource = format!("/nodes/{node_id}/metrics?minutes={}", minutes.max(1));
    let values = get_with_refresh(&mut session, &resource)?;
    let rows = values
        .as_array()
        .ok_or_else(|| "资源历史响应无效".to_string())?;
    Ok(serde_json::Value::Array(
        rows.iter().map(normalize_metric).collect(),
    ))
}

pub fn update_node(
    configured_url: &str,
    node_id: &str,
    status: &str,
) -> Result<serde_json::Value, String> {
    if !matches!(status, "online" | "draining" | "disabled") {
        return Err("不支持的服务器状态".into());
    }
    let mut session = configured_session(configured_url)?;
    let resource = format!("/nodes/{node_id}");
    let body = serde_json::json!({ "status": status });
    let value = match patch(&session, &resource, body.clone()) {
        Ok(value) => value,
        Err(401) => {
            refresh(&mut session)?;
            patch(&session, &resource, body)
                .map_err(|code| format!("服务器状态更新失败：HTTP {code}"))?
        }
        Err(code) => return Err(format!("服务器状态更新失败：HTTP {code}")),
    };
    Ok(normalize_node(&value))
}

pub fn jobs(configured_url: &str, project_id: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let resource = format!("/jobs?project_id={project_id}");
    let values = get_with_refresh(&mut session, &resource)?;
    let rows = values.as_array().ok_or("任务列表响应无效")?;
    Ok(serde_json::Value::Array(
        rows.iter().map(normalize_job).collect(),
    ))
}

pub fn job_kinds(configured_url: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    get_with_refresh(&mut session, "/job-kinds")
}

pub fn create_job(
    configured_url: &str,
    input: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let body = serde_json::json!({
        "project_id": input["projectId"], "kind": input["kind"],
        "payload": { "arguments": input["arguments"], "data_version_ids": input.get("dataVersionIds").cloned().unwrap_or(serde_json::json!([])) },
        "requested_resources": input["requestedResources"],
        "node_ids": input["nodeIds"],
        "priority": input.get("priority").cloned().unwrap_or(serde_json::json!(0))
    });
    Ok(normalize_job(&post_with_refresh(
        &mut session,
        "/jobs",
        body,
    )?))
}

pub fn cancel_job(configured_url: &str, job_id: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let value = post_with_refresh(
        &mut session,
        &format!("/jobs/{job_id}/cancel"),
        serde_json::json!({}),
    )?;
    Ok(normalize_job(&value))
}

pub fn job_events(configured_url: &str, job_id: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let values = get_with_refresh(&mut session, &format!("/jobs/{job_id}/events"))?;
    let rows = values.as_array().ok_or("任务日志响应无效")?;
    Ok(serde_json::Value::Array(
        rows.iter().map(normalize_job_event).collect(),
    ))
}

pub fn job_artifacts(configured_url: &str, job_id: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(configured_url)?;
    let values = get_with_refresh(&mut session, &format!("/jobs/{job_id}/artifacts"))?;
    let rows = values.as_array().ok_or("任务结果响应无效")?;
    Ok(serde_json::Value::Array(
        rows.iter().map(normalize_artifact).collect(),
    ))
}

pub fn download_job_artifact(
    configured_url: &str,
    job_id: &str,
    artifact_id: &str,
    target: &Path,
) -> Result<(), String> {
    let mut session = configured_session(configured_url)?;
    let resource = format!("/jobs/{job_id}/artifacts/{artifact_id}");
    for attempt in 0..2 {
        let response = ureq::get(&format!(
            "{}{}{}",
            session.base_url, session.api_path, resource
        ))
        .set("Authorization", &format!("Bearer {}", session.access_token))
        .timeout(std::time::Duration::from_secs(120))
        .call();
        match response {
            Ok(response) => {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
                }
                let temporary = target.with_extension("part");
                let mut output = fs::File::create(&temporary).map_err(|e| e.to_string())?;
                if let Err(error) = io::copy(&mut response.into_reader(), &mut output) {
                    let _ = fs::remove_file(&temporary);
                    return Err(error.to_string());
                }
                if target.exists() {
                    fs::remove_file(target).map_err(|e| e.to_string())?;
                }
                fs::rename(&temporary, target).map_err(|e| e.to_string())?;
                return Ok(());
            }
            Err(ureq::Error::Status(401, _)) if attempt == 0 => refresh(&mut session)?,
            Err(ureq::Error::Status(code, _)) => {
                return Err(format!("结果下载失败：HTTP {code}"));
            }
            Err(_) => return Err("结果下载失败：网络连接不可用".into()),
        }
    }
    Err("结果下载失败".into())
}

pub fn logout(configured_url: &str) -> Result<serde_json::Value, String> {
    if let Some(session) = load() {
        if session.base_url == configured_url.trim().trim_end_matches('/') {
            if !session.device_token.is_empty() {
                ureq::post(&format!("{}{}/auth/devices/disconnect", session.base_url, session.api_path))
                    .timeout(std::time::Duration::from_secs(10))
                    .send_json(serde_json::json!({"device_token": session.device_token}))
                    .map_err(|_| "未能撤销设备授权，请恢复网络后重试；凭据仍保留".to_string())?;
            } else {
            let _ = ureq::post(&format!(
                "{}{}{}",
                session.base_url, session.api_path, "/auth/logout"
            ))
            .timeout(std::time::Duration::from_secs(5))
            .send_json(serde_json::json!({ "refresh_token": session.refresh_token }));
            }
        }
        clear_if_current(&session);
    }
    Ok(serde_json::json!({ "authenticated": false, "projects": [] }))
}

pub fn scheduling_nodes(url: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(url)?; get_with_refresh(&mut session, "/scheduling/nodes")
}
pub fn scheduling(url: &str, id: &str) -> Result<serde_json::Value, String> {
    let mut session = configured_session(url)?; get_with_refresh(&mut session, &format!("/jobs/{id}/scheduling"))
}

pub fn project_collaboration(url: &str, project_id: &str, action: &str, body: serde_json::Value) -> Result<serde_json::Value, String> {
    if action == "devices-get" { let mut session = configured_session(url)?; return get_with_refresh(&mut session, "/auth/devices"); }
    if action == "devices-revoke" {
        let id = uuid::Uuid::parse_str(body["id"].as_str().ok_or("缺少设备编号")?).map_err(|_| "设备编号无效")?;
        let mut session = configured_session(url)?;
        return post_with_refresh(&mut session, &format!("/auth/devices/{id}/revoke"), serde_json::json!({}));
    }
    if action.starts_with("account-") { return account_request(url, action, body); }
    if action == "project-create" { let mut session = configured_session(url)?; return post_with_refresh(&mut session, "/projects", body); }
    if action == "audit-get" { let id = uuid::Uuid::parse_str(project_id).map_err(|_| "工作区编号无效")?; let mut session = configured_session(url)?; return get_with_refresh(&mut session, &format!("/audit-logs?project_id={id}&limit=100")); }
    let id = uuid::Uuid::parse_str(project_id).map_err(|_| "项目编号无效")?;
    let mut session = configured_session(url)?;
    let path = match action {
        "nodes-get" | "nodes-set" => format!("/projects/{id}/nodes"),
        _ => return Err("无效操作".into()),
    };
    if action.ends_with("get") {
        get_with_refresh(&mut session, &path)
    } else {
        let payload = body;
        post_with_refresh(&mut session, &path, payload)
    }
}

pub fn storage(url: &str, action: &str, body: serde_json::Value) -> Result<serde_json::Value, String> {
    let id = |field: &str| -> Result<uuid::Uuid, String> {
        uuid::Uuid::parse_str(body[field].as_str().ok_or("缺少目录或项目编号")?)
            .map_err(|_| "目录或项目编号无效".into())
    };
    let (method, path, payload) = match action {
        "roots" => ("GET", "/storage/roots".into(), serde_json::Value::Null),
        "create" => ("POST", "/storage/roots".into(), serde_json::json!({"name":body["name"]})),
        "enable" => ("PATCH", format!("/storage/roots/{}", id("root_id")?), serde_json::json!({"enabled":body["enabled"]})),
        "bind" => ("PUT", format!("/storage/roots/{}/bindings/{}", id("root_id")?, id("node_id")?), serde_json::json!({"local_key":body["local_key"]})),
        "grant" | "revoke" => (if action == "grant" {"PUT"} else {"DELETE"}, format!("/storage/roots/{}/grants/{}", id("root_id")?, id("project_id")?), serde_json::json!({})),
        "list" => ("GET", format!("/projects/{}/storage", id("project_id")?), serde_json::Value::Null),
        "browse" => ("POST", format!("/projects/{}/storage/{}/browse", id("project_id")?, id("root_id")?), serde_json::json!({"path":body["path"],"cursor":body["cursor"],"page_size":100})),
        "result" => ("GET", format!("/projects/{}/storage/browse/{}", id("project_id")?, id("request_id")?), serde_json::Value::Null),
        "versions" => ("GET", format!("/projects/{}/data-versions", id("project_id")?), serde_json::Value::Null),
        "protection" => ("GET", format!("/projects/{}/protection", id("project_id")?), serde_json::Value::Null),
        "files" => ("GET", format!("/projects/{}/managed-files", id("project_id")?), serde_json::Value::Null),
        "file-create" => ("POST", format!("/projects/{}/managed-files", id("project_id")?), serde_json::json!({"name":body["name"]})),
        "file-versions" => ("GET", format!("/projects/{}/managed-files/{}/versions", id("project_id")?, id("file_id")?), serde_json::Value::Null),
        "preview" => ("POST", format!("/projects/{}/managed-files/{}/preview", id("project_id")?, id("file_id")?), body["change"].clone()),
        "approve" => ("POST", format!("/projects/{}/changes/{}/approve", id("project_id")?, id("change_id")?), serde_json::json!({"digest":body["digest"]})),
        "commit" => ("POST", format!("/projects/{}/changes/{}/commit", id("project_id")?, id("change_id")?), serde_json::json!({"approval_token":body["approval_token"]})),
        "backups" => ("GET", format!("/projects/{}/backups", id("project_id")?), serde_json::Value::Null),
        "restore" => ("POST", format!("/projects/{}/backups/{}/restore", id("project_id")?, id("backup_id")?), serde_json::json!({"side":body["side"]})),
        "agent-enable" => {
            let project = id("project_id")?.to_string();
            let mut session = configured_session(url)?;
            let value = post_with_refresh(&mut session, &format!("/projects/{project}/agent-grant"), body["grant"].clone())?;
            let stored = serde_json::json!({"platform":url, "project":project, "token":value["token"], "endpoint":format!("{}{}", session.base_url, value["endpoint"].as_str().ok_or("缺少工具地址")?)});
            keyring::Entry::new("BioDSH.ProtectedAgent", "desktop-agent-capability").map_err(|_| "无法打开安全存储")?.set_password(&stored.to_string()).map_err(|_| "无法保存对话授权")?;
            return Ok(serde_json::json!({"enabled":true,"expires_in":value["expires_in"]}));
        },
        _ => return Err("无效的共享目录操作".into()),
    };
    let mut session = configured_session(url)?;
    let timeout = if matches!(action, "commit" | "restore") { 300 } else { 15 };
    for attempt in 0..2 {
        let request = ureq::request(method, &format!("{}{}{}", session.base_url, session.api_path, path))
            .set("Authorization", &format!("Bearer {}", session.access_token))
            .timeout(std::time::Duration::from_secs(timeout));
        let response = if method == "GET" {request.call()} else {request.send_json(payload.clone())};
        match response {
            Ok(response) => return parse(response),
            Err(ureq::Error::Status(401, _)) if attempt == 0 => refresh(&mut session)?,
            Err(ureq::Error::Status(code, _)) => return Err(match code {
                403 | 404 => "目录不可用或没有访问权限".into(),
                409 => "目录状态已变化，请刷新".into(),
                429 => "查询过于频繁，请稍后重试".into(),
                503 => "服务不可用或独立备份尚未配置，请检查保护状态".into(),
                _ => format!("共享目录请求失败：HTTP {code}"),
            }),
            Err(_) => return Err("无法连接平台，请检查网络".into()),
        }
    }
    Err("登录已过期，请重新登录".into())
}


fn account_error(error: ureq::Error) -> String {
    match error {
        ureq::Error::Status(code, response) => {
            let value: serde_json::Value = response.into_json().unwrap_or_default();
            if let Some(detail) = value["detail"].as_str() {
                return match detail {
                    "The system must keep an active administrator" => "必须保留至少一名有效管理员".into(),
                    "Username already exists" => "用户名已存在，请更换".into(),
                    "Administrator role required" => "此操作需要系统管理员权限".into(),
                    _ if detail.len() < 300 => detail.to_string(),
                    _ => format!("账号操作失败：HTTP {code}"),
                };
            }
            match code {
                401 => "登录已失效或人员状态待核验，请重新登录或联系管理员".into(),
                403 => "没有执行此操作的权限，请联系管理员".into(),
                422 => "请检查输入；密码需为 12 至 256 个字符，且不能全为空白".into(),
                _ => format!("账号操作失败：HTTP {code}"),
            }
        },
        _ => "无法连接平台，请检查网络后重试".into(),
    }
}

fn account_request(url: &str, action: &str, body: serde_json::Value) -> Result<serde_json::Value, String> {
    if action == "account-clear-invitation" { clear_invitation(); return Ok(serde_json::json!({})); }
    let uid = || -> Result<uuid::Uuid, String> {
        uuid::Uuid::parse_str(body["user_id"].as_str().ok_or("缺少用户编号")?).map_err(|_| "用户编号无效".into())
    };
    if action == "account-reset" {
        let (base, api) = discover(url)?;
        return ureq::post(&format!("{base}{api}/auth/password/reset"))
            .timeout(std::time::Duration::from_secs(20)).send_json(body)
            .map_err(account_error).and_then(parse);
    }
    let (method, path, payload) = match action {
        "account-list" => {
            let mut query = reqwest::Url::parse("https://localhost/admin/accounts").unwrap();
            query.query_pairs_mut().append_pair("q", body["q"].as_str().unwrap_or(""))
                .append_pair("offset", &body["offset"].as_u64().unwrap_or(0).to_string());
            ("GET", format!("{}?{}", query.path(), query.query().unwrap_or("")), serde_json::Value::Null)
        },
        "account-create" => ("POST", "/admin/users".into(), body.clone()),
        "account-update" => ("PATCH", format!("/admin/users/{}", uid()?), body["changes"].clone()),
        "account-revoke" => ("POST", format!("/admin/users/{}/revoke", uid()?), serde_json::json!({})),
        "account-recovery" => ("POST", format!("/admin/accounts/{}/recovery", uid()?), serde_json::json!({})),
        "account-sync" => ("POST", format!("/admin/accounts/{}/sync", uid()?), serde_json::json!({})),
        "account-directory" => ("GET", "/admin/directory/status".into(), serde_json::Value::Null),
        "account-password" => ("POST", "/auth/password/change".into(), body.clone()),
        _ => return Err("无效账号操作".into()),
    };
    let mut session = configured_session(url)?;
    for attempt in 0..2 {
        let request = ureq::request(method, &format!("{}{}{}", session.base_url, session.api_path, path))
            .set("Authorization", &format!("Bearer {}", session.access_token))
            .timeout(std::time::Duration::from_secs(40));
        let response = if method == "GET" { request.call() } else { request.send_json(payload.clone()) };
        match response {
            Ok(value) => {
                if action == "account-password" { clear_if_current(&session); }
                return if value.status() == 204 { Ok(serde_json::Value::Null) } else { parse(value) };
            },
            Err(ureq::Error::Status(401, _)) if attempt == 0 => refresh(&mut session)?,
            Err(error) => return Err(account_error(error)),
        }
    }
    Err("请重新登录".into())
}


fn platform_status_message(code: u16) -> String {
    match code {
        400 => "邀请无效、已过期或已使用，请联系负责人获取新链接".into(),
        401 => "登录已失效，请重新登录".into(),
        403 => "没有此项目的操作权限，请联系项目负责人".into(),
        404 => "项目或资源不可用，可能已被移除权限，请刷新项目列表".into(),
        409 => "状态已变化或操作违反保护规则（需保留管理员/项目负责人），请刷新后重试".into(),
        422 => "提交的信息不完整或格式不正确，请检查输入".into(),
        _ => format!("平台连接失败：HTTP {code}"),
    }
}


#[cfg(test)]
mod device_tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    #[test]
    fn legacy_session_remains_readable() {
        let session: StoredSession = serde_json::from_str(r#"{"baseUrl":"https://example.test","apiPath":"/api/v1","accessToken":"old-access","refreshToken":"old-refresh"}"#).unwrap();
        assert!(session.device_token.is_empty());
        assert_eq!(session.refresh_token, "old-refresh");
    }

    #[test]
    fn stale_request_cannot_replace_new_device_identity() {
        let old: StoredSession = serde_json::from_str(r#"{"baseUrl":"https://example.test","apiPath":"/api/v1","accessToken":"old","refreshToken":"refresh"}"#).unwrap();
        let mut current = old.clone();
        current.refresh_token.clear();
        current.device_token = "new-device-secret".into();
        assert!(!same_session(&old, &current));
        let mut renewed = current.clone();
        renewed.access_token = "new-short-lived-access".into();
        assert!(same_session(&current, &renewed));
    }

    fn failed_exchange(code: u16) -> (String, StoredSession) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(std::time::Duration::from_secs(5))).unwrap();
            let mut buffer = [0; 4096];
            let _ = stream.read(&mut buffer).unwrap();
            write!(stream, "HTTP/1.1 {code} Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        let mut session = StoredSession { base_url: format!("http://{address}"), api_path: "/api/v1".into(), access_token: "old-access".into(), refresh_token: String::new(), device_token: "keep-device-secret".into(), device_id: "test".into() };
        let error = refresh(&mut session).unwrap_err();
        server.join().unwrap();
        (error, session)
    }

    #[test]
    fn temporary_failure_does_not_expire_or_replace_credentials() {
        let (error, session) = failed_exchange(503);
        assert!(!error.contains("登录已过期"));
        assert_eq!(session.device_token, "keep-device-secret");
        assert_eq!(session.access_token, "old-access");
    }

    #[test]
    fn revoked_device_requires_authorization() {
        let (error, _) = failed_exchange(401);
        assert!(error.contains("登录已过期"));
    }
}
