use crate::paths::AppPaths;
use serde::{Deserialize, Serialize};
use std::fs;

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub use_china_mirror: bool,
    pub workspace: String,
    pub onboarded: bool,
    pub theme: String,
    #[serde(default = "default_language")]
    pub language: String,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub offline_base_url: String,
    #[serde(default)]
    pub offline_model: String,
    #[serde(default)]
    pub offline_api_key: String,
    #[serde(default)]
    pub remote_dsh_url: String,
    #[serde(default)]
    pub platform_url: String,
    #[serde(default)]
    pub current_project_id: String,
    #[serde(default)]
    pub ragflow_url: String,
    #[serde(default = "default_ragflow_mode")]
    pub ragflow_mode: String,
    /// 图像生成（OpenAI 兼容 images 接口）：智谱 CogView / OpenAI / SiliconFlow / 通义万相
    #[serde(default)]
    pub image_base_url: String,
    #[serde(default)]
    pub image_api_key: String,
    #[serde(default)]
    pub image_model: String,
    /// 外接 MCP 服务（写进 dsh 的 cordis.patch.yml）
    #[serde(default)]
    pub mcp_servers: Vec<McpServer>,
    /// 示范项目是否已复制到 ~/BioDSH/demos 并注册为工作区
    #[serde(default)]
    pub demos_seeded: bool,
}

#[derive(Clone, Serialize, Deserialize, Default, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct McpServer {
    pub name: String,
    #[serde(default = "default_transport")]
    pub transport: String, // stdio | streamable-http
    #[serde(default)]
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub env: std::collections::BTreeMap<String, String>,
    #[serde(default)]
    pub enabled: Option<bool>,
}
fn default_transport() -> String { "stdio".into() }

pub fn validate_mcp(list: &[McpServer]) -> Result<(), String> {
    let mut names = std::collections::HashSet::new();
    for m in list {
        if m.name.is_empty() || m.name.len()>32 || !m.name.chars().all(|c| c.is_ascii_alphanumeric() || c=='_' || c=='-')
            || m.name.contains("__") || m.name.starts_with("biodsh") || m.name.starts_with("omaax") || !names.insert(&m.name) {
            return Err("工具名称需唯一，使用 1–32 位字母数字、单下划线或连字符，不能使用系统保留名称".into());
        }
        if m.transport=="streamable-http" {
            let url=reqwest::Url::parse(&m.url).map_err(|_|"工具服务地址无效")?;
            if !matches!(url.scheme(),"http"|"https") || url.host_str().is_none() || !url.username().is_empty() || url.password().is_some() {
                return Err("工具服务需使用 HTTP 或 HTTPS 地址，不能包含账号密码".into());
            }
        } else if m.transport!="stdio" || m.command.trim().is_empty() {
            return Err("请选择工具连接方式并填写启动程序".into());
        }
        if m.env.keys().any(|k| k.is_empty() || !k.chars().all(|c|c.is_ascii_alphanumeric()||c=='_')) {
            return Err("环境变量名称只能包含字母数字和下划线".into());
        }
    }
    Ok(())
}

fn default_language() -> String { "system".into() }
fn default_mode() -> String { "online".into() }
fn default_ragflow_mode() -> String { "local".into() }

impl AppSettings {
    pub fn load(p: &AppPaths) -> AppSettings {
        let f = p.root.join("settings.json");
        let mut s = AppSettings { use_china_mirror: true, workspace: p.workspace.to_string_lossy().into(), onboarded: false, theme: "system".into(), language: "system".into(), mode: "online".into(), offline_base_url: String::new(), offline_model: String::new(), offline_api_key: String::new(), remote_dsh_url: String::new(), platform_url: String::new(), current_project_id: String::new(), ragflow_url: "http://127.0.0.1:9380".into(), ragflow_mode: default_ragflow_mode(), image_base_url: String::new(), image_api_key: String::new(), image_model: String::new(), mcp_servers: Vec::new(), demos_seeded: false };
        let mut had_legacy_platform_token = false;
        if let Ok(txt) = fs::read_to_string(&f) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&txt) {
                had_legacy_platform_token = v.get("platformToken").is_some();
                if let Some(b) = v.get("useChinaMirror").and_then(|x| x.as_bool()) { s.use_china_mirror = b; }
                if let Some(w) = v.get("workspace").and_then(|x| x.as_str()) { if !w.is_empty() { s.workspace = w.into(); } }
                if let Some(b) = v.get("onboarded").and_then(|x| x.as_bool()) { s.onboarded = b; }
                if let Some(t) = v.get("theme").and_then(|x| x.as_str()) { s.theme = t.into(); }
                if let Some(l) = v.get("language").and_then(|x| x.as_str()) { s.language = l.into(); }
                if let Some(m) = v.get("mode").and_then(|x| x.as_str()) { s.mode = m.into(); }
                for (k, field) in [("offlineBaseUrl", 0), ("offlineModel", 1), ("offlineApiKey", 2), ("remoteDshUrl", 3)] {
                    if let Some(val) = v.get(k).and_then(|x| x.as_str()) {
                        match field { 0 => s.offline_base_url = val.into(), 1 => s.offline_model = val.into(), 2 => s.offline_api_key = val.into(), _ => s.remote_dsh_url = val.into() }
                    }
                }
                for (k, field) in [("platformUrl", 0), ("currentProjectId", 1)] {
                    if let Some(val) = v.get(k).and_then(|x| x.as_str()) {
                        match field { 0 => s.platform_url = val.into(), _ => s.current_project_id = val.into() }
                    }
                }
                if let Some(val) = v.get("ragflowUrl").and_then(|x| x.as_str()) { s.ragflow_url = val.into(); }
                if let Some(val) = v.get("ragflowMode").and_then(|x| x.as_str()) { s.ragflow_mode = val.into(); }
                for (k, field) in [("imageBaseUrl", 0), ("imageModel", 1), ("imageApiKey", 2)] {
                    if let Some(val) = v.get(k).and_then(|x| x.as_str()) {
                        match field { 0 => s.image_base_url = val.into(), 1 => s.image_model = val.into(), _ => s.image_api_key = val.into() }
                    }
                }
                if let Some(arr) = v.get("mcpServers") { if let Ok(list) = serde_json::from_value::<Vec<McpServer>>(arr.clone()) { s.mcp_servers = list; } }
                if let Some(b) = v.get("demosSeeded").and_then(|x| x.as_bool()) { s.demos_seeded = b; }
            }
        }
        if had_legacy_platform_token { s.save(p); }
        s
    }
    pub fn save(&self, p: &AppPaths) {
        let _ = fs::write(p.root.join("settings.json"), serde_json::to_string_pretty(self).unwrap_or_default());
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialStatus {
    pub has_key: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub masked: Option<String>,
}

const CRED_KEY: &str = "DEEPSEEK_API_KEY";

/// 解析凭据文件里的 key：支持 dsh ≥0.1.1 的 `version: 1 / refs:` 布局，也认早期的扁平布局。
fn parse_key(txt: &str) -> Option<String> {
    let mut in_refs = false;
    for line in txt.lines() {
        if line.starts_with("refs:") { in_refs = true; continue; }
        if !line.starts_with(' ') && !line.starts_with('\t') { in_refs = false; }
        let t = line.trim();
        if let Some(rest) = t.strip_prefix(&format!("{CRED_KEY}:")) {
            if in_refs || !txt.contains("version:") {
                let k = rest.trim().trim_matches(|c| c == '"' || c == '\'');
                if !k.is_empty() { return Some(k.to_string()); }
            }
        }
    }
    None
}

fn render(key: Option<&str>) -> String {
    match key { Some(k) => format!("version: 1\n\nrefs:\n  {CRED_KEY}: {k}\n"), None => "version: 1\n\nrefs: {}\n".to_string() }
}

pub fn read_credential_value(p: &AppPaths) -> Option<String> {
    fs::read_to_string(p.dsh_home.join(".credentials.yaml")).ok().and_then(|t| parse_key(&t))
}

pub fn read_credential(p: &AppPaths) -> CredentialStatus {
    match read_credential_value(p) {
        Some(k) => CredentialStatus { has_key: true, masked: Some(if k.len() > 8 { format!("{}••••{}", &k[..5], &k[k.len() - 4..]) } else { "••••".into() }) },
        None => CredentialStatus { has_key: false, masked: None },
    }
}

pub fn write_credential(p: &AppPaths, key: &str) {
    let f = p.dsh_home.join(".credentials.yaml");
    let key = key.trim();
    let _ = fs::write(&f, render(if key.is_empty() { None } else { Some(key) }));
    #[cfg(unix)]
    { use std::os::unix::fs::PermissionsExt; let _ = fs::set_permissions(&f, fs::Permissions::from_mode(0o600)); }
}

/// 启动 dsh 前调用：把早期扁平格式的凭据文件迁移到版本化布局（dsh 0.1.1 起扁平格式会让整个进程启动失败）。
pub fn migrate_credentials(p: &AppPaths) {
    let f = p.dsh_home.join(".credentials.yaml");
    let Ok(txt) = fs::read_to_string(&f) else { return };
    if txt.contains("version:") || txt.trim().is_empty() { return; }
    if let Some(k) = parse_key(&txt) { let _ = fs::write(&f, render(Some(&k))); }
}

#[cfg(test)]
mod mcp_validation_tests {
    use super::*;
    #[test]
    fn saved_profile_configuration_roundtrips_without_changing_existing_values() {
        let Ok(list)=std::env::var("OMAAX_UPGRADE_SETTINGS_TEST") else {return;};
        let files:Vec<String>=serde_json::from_str(&fs::read_to_string(list).unwrap()).unwrap();
        assert!(!files.is_empty());
        for file in files {
            let bytes=fs::read(&file).unwrap();
            let original:serde_json::Value=serde_json::from_slice(&bytes).unwrap();
            let settings:AppSettings=serde_json::from_slice(&bytes).expect("Existing configuration must load without fallback defaults");
            let encoded=serde_json::to_value(settings).unwrap();
            for (key,value) in original.as_object().unwrap() {
                if let Some(saved)=encoded.get(key) {assert_eq!(saved,value,"Saved configuration field changed: {key}");}
            }
            assert_eq!(fs::read(file).unwrap(),bytes);
        }
    }
    #[test]
    fn reserved_duplicate_and_injected_names_are_rejected() {
        let good=McpServer {name:"papers".into(),transport:"streamable-http".into(),url:"http://127.0.0.1:3000/mcp".into(),..Default::default()};
        assert!(validate_mcp(&[good.clone()]).is_ok());
        assert!(validate_mcp(&[good.clone(),good.clone()]).is_err());
        for name in ["biodsh_ssh","omaax_image","papers__delete","bad\nname",""] {
            let mut bad=good.clone();bad.name=name.into();assert!(validate_mcp(&[bad]).is_err());
        }
        for url in ["file:///tmp/test","https://user:pass@example.com/mcp","invalid"] {
            let mut bad=good.clone();bad.url=url.into();assert!(validate_mcp(&[bad]).is_err());
        }
        let mut local=McpServer{name:"local".into(),transport:"stdio".into(),command:"node".into(),args:vec!["C:/a b/server.js".into()],..Default::default()};
        assert!(validate_mcp(&[local.clone()]).is_ok());
        local.env.insert("BAD\nKEY".into(),"x".into());assert!(validate_mcp(&[local]).is_err());
    }
}
