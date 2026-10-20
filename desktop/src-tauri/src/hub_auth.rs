//! Central authentication; local profiles never adopt legacy data.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{path::{Path, PathBuf}, sync::{OnceLock, Mutex}, time::Duration};

static SESSION_WRITE: Mutex<()> = Mutex::new(());
static CHECKED: Mutex<Option<(String,User,std::time::Instant)>> = Mutex::new(None);
static BASE: OnceLock<PathBuf> = OnceLock::new();
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct User { pub id: String, pub username: String, pub role: String, pub authorizations: Vec<String> }
#[derive(Serialize, Deserialize)]
struct Session { hub: String, token: String, user: User, #[serde(default)] restart_nonce: Option<String> }

#[derive(Serialize, Deserialize)]
pub struct RememberedLogin { pub username: String, pub password: String }
fn remembered_entry() -> Result<keyring::Entry,String> {
    keyring::Entry::new("OMaaX.RememberedLogin", &digest(&format!("{}:{}",base().display(),configured_hub())))
        .map_err(|_|"系统安全存储不可用".into())
}
pub fn remembered_login() -> Result<Option<RememberedLogin>,String> {
    match remembered_entry()?.get_password() {
        Ok(value)=>serde_json::from_str(&value).map(Some).map_err(|_|"保存的登录信息无效，请重新输入".into()),
        Err(keyring::Error::NoEntry)=>Ok(None),
        Err(_)=>Err("无法读取保存的登录信息，请重新输入".into())
    }
}
pub fn forget_login() -> Result<(),String> {
    match remembered_entry()?.delete_credential() {Ok(())|Err(keyring::Error::NoEntry)=>Ok(()),Err(_)=>Err("无法清除保存的登录信息，请重试".into())}
}
pub fn remember_login(username:&str,password:&str,remember:bool)->Result<(),String>{
    if !remember {return forget_login();}
    let value=serde_json::to_string(&RememberedLogin{username:username.trim().into(),password:password.into()}).map_err(|_|"无法保存登录信息")?;
    let entry=remembered_entry()?;
    entry.set_password(&value).map_err(|_|"无法安全保存账号和密码")?;
    if entry.get_password().ok().as_deref()!=Some(&value){return Err("保存的登录信息校验失败".into());} Ok(())
}
// Only the internal restart immediately after password verification may resume.
// The nonce lives in OS secure storage, is consumed once, and is never sent to the renderer.
pub fn arm_login_restart()->Result<(),String>{
    let mut session=stored()?;let nonce=uuid::Uuid::new_v4().to_string();session.restart_nonce=Some(nonce.clone());
    entry()?.set_password(&serde_json::to_string(&session).map_err(|_|"无法完成登录")?).map_err(|_|"无法完成登录")?;
    std::env::set_var("OMAAX_LOGIN_RESTART",nonce);Ok(())
}
pub fn begin_desktop_session()->Result<(),String>{
    if local_access(){return Ok(());}
    let nonce=std::env::var("OMAAX_LOGIN_RESTART").ok();std::env::remove_var("OMAAX_LOGIN_RESTART");
    if let Ok(mut session)=stored(){
        if nonce.is_some() && session.restart_nonce==nonce {
            session.restart_nonce=None;
            entry()?.set_password(&serde_json::to_string(&session).map_err(|_|"无法完成登录")?).map_err(|_|"无法完成登录")?;
            return Ok(());
        }
    }
    let dir=base().join("accounts");std::fs::create_dir_all(&dir).map_err(|_|"无法初始化登录状态")?;
    std::fs::write(dir.join("logged-out"),b"password required on startup").map_err(|_|"无法锁定登录状态")?;
    *CHECKED.lock().unwrap()=None;Ok(())
}

pub fn initialize(base: PathBuf) { let _ = BASE.set(base); }
pub fn base() -> PathBuf {
    BASE.get().cloned().or_else(|| std::env::var_os("OMAAX_ACCOUNT_HOME").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("USERPROFILE").or_else(||std::env::var_os("HOME")).unwrap_or_default()).join("BioDSH"))
}
fn digest(s: &str) -> String { format!("{:x}", Sha256::digest(s.as_bytes())) }
fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new("OMaaX.HubSession", &digest(&base().to_string_lossy())).map_err(|_|"系统安全存储不可用".into())
}
fn stored() -> Result<Session, String> {
    if base().join("accounts/logged-out").exists(){return Err("UNAUTHORIZED: 已退出，请重新登录".into());}
    let text = entry()?.get_password().map_err(|_|"UNAUTHORIZED: 请登录".to_string())?;
    serde_json::from_str(&text).map_err(|_|"UNAUTHORIZED: 登录记录无效".into())
}
pub fn normalize(hub: &str) -> Result<String, String> {
    let url = reqwest::Url::parse(hub.trim()).map_err(|_|"服务地址无效")?;
    if !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some() || url.path() != "/" {
        return Err("请填写服务地址，不包含路径、账号或查询参数".into());
    }
    let local = matches!(url.host_str(), Some("localhost"|"127.0.0.1"|"[::1]"));
    let private=url.host_str().and_then(|h|h.parse::<std::net::Ipv4Addr>().ok()).is_some_and(|ip|ip.is_private());
    if url.scheme() != "https" && !(url.scheme() == "http" && (local || private)) { return Err("公网服务地址需要使用 HTTPS；HTTP 仅限局域网或本机".into()); }
    Ok(url.as_str().trim_end_matches('/').to_string())
}
pub fn configured_hub() -> String {
    for file in [base().join("accounts/hub.json")] {
        if let Ok(text) = std::fs::read_to_string(file) {
            if let Ok(value) = serde_json::from_str::<Value>(&text) {
                if let Some(url) = value["platformUrl"].as_str() {
                    if matches!(url.trim_end_matches('/'),"http://192.168.0.175:8090"|"https://biodsh.biomino.fun") {return "https://cloud.haiguji.com".into();}
                    return url.to_string();
                }
            }
        }
    }
    "https://cloud.haiguji.com".into()
}
fn request(hub: &str, route: &str, token: Option<&str>, body: Option<Value>) -> Result<Value, String> {
    let hub = normalize(hub)?;
    // Never forward credentials to a redirected origin.
    let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(12))
        .no_proxy().redirect(reqwest::redirect::Policy::none()).build().map_err(|_|"登录服务连接失败，请重试")?;
    let url = format!("{hub}/api/auth/{route}");
    let mut req = if body.is_some() { client.post(url) } else { client.get(url) };
    if let Some(token) = token { req = req.bearer_auth(token); }
    if let Some(body) = body { req = req.json(&body); }
    let response = req.send().map_err(|_|"无法连接登录服务，请检查地址和网络后重试")?;
    let status = response.status();
    if status.as_u16() == 401 { return Err(if route=="login" {"UNAUTHORIZED: 用户名或密码错误"} else {"UNAUTHORIZED: 登录已过期，请重新登录"}.into()); }
    if status.as_u16() == 403 { return Err("FORBIDDEN: 当前账号无此权限".into()); }
    if !status.is_success() { return Err(format!("登录服务暂不可用（{}），请稍后重试或联系管理员", status.as_u16())); }
    let value: Value = response.json().map_err(|_|"登录服务返回异常，请重试或联系管理员")?;
    if value["ok"] != true { return Err("登录未成功，请检查账号信息或联系管理员".into()); }
    Ok(value)
}
fn enabled(hub: &str) -> Result<(), String> {
    if request(hub,"status",None,None)?["auth_enabled"] != true { return Err("登录服务尚未开通，请联系管理员".into()); } Ok(())
}
fn parse_user(value: &Value) -> Result<User, String> {
    let mut user: User = serde_json::from_value(value.clone()).map_err(|_|"账号信息不完整")?;
    user.id = uuid::Uuid::parse_str(&user.id).map_err(|_|"账号标识无效")?.to_string();
    if !matches!(user.role.as_str(),"user"|"admin"|"researcher"|"guest") || user.username.trim().is_empty() { return Err("登录信息无效，请重新登录".into()); } Ok(user)
}
pub fn profile(base: &Path, hub: &str, user: &User) -> Result<PathBuf, String> {
    let hub = normalize(hub)?;
    let id = uuid::Uuid::parse_str(&user.id).map_err(|_|"用户标识无效")?;
    Ok(base.join("accounts/profiles").join(digest(&hub)).join(id.to_string()))
}
pub fn local_access() -> bool { cfg!(feature = "local-access") }
fn local_user() -> User { User { id: "local".into(), username: "本机使用".into(), role: "local".into(), authorizations: vec!["qa".into(), "bioflow".into(), "docgen".into()] } }
pub fn startup_root() -> PathBuf {
    if local_access() { return base(); }
    stored().and_then(|s| profile(&base(), &s.hub, &s.user)).unwrap_or_else(|_|base().join("accounts/locked"))
}
// Desktop analysis and shared research are included with every authenticated account.
// Cloud knowledge alone needs an explicit qa entitlement (or the administrator role).
pub fn has(user: &User, unit: &str) -> bool { matches!(unit,""|"bioflow"|"docgen") || user.role == "admin" || user.authorizations.iter().any(|v|v==unit) }
pub fn require(root: &Path, unit: &str) -> Result<User, String> {
    if local_access() {
        if root != base() { return Err("当前数据目录不匹配，请重新打开软件".into()); }
        let user=local_user();
        return if has(&user,unit) { Ok(user) } else { Err("该功能暂不可用".into()) };
    }
    let session = stored()?;
    if normalize(&configured_hub())? != session.hub || profile(&base(),&session.hub,&session.user)? != root { return Err("UNAUTHORIZED: 账号已切换，请重新打开软件".into()); }
    let fingerprint=digest(&format!("{}:{}",session.hub,session.token));
    if let Some((key,user,at))=CHECKED.lock().unwrap().as_ref() {
        if key==&fingerprint && at.elapsed()<Duration::from_secs(5) {if has(user,unit){return Ok(user.clone());} return Err("FORBIDDEN: 当前账号没有该功能授权".into());}
    }
    enabled(&session.hub)?;
    let user = parse_user(&request(&session.hub,"me",Some(&session.token),None)?["user"])?;
    if user.id != session.user.id { return Err("UNAUTHORIZED: 登录身份已变化".into()); }
    *CHECKED.lock().unwrap()=Some((fingerprint,user.clone(),std::time::Instant::now()));
    if !has(&user,unit) { return Err("FORBIDDEN: 当前账号没有该功能授权".into()); } Ok(user)
}
pub fn status(root: &Path) -> Value {
    match require(root,"") { Ok(user)=>json!({"authenticated":true,"localAccess":local_access(),"hub":configured_hub(),"user":user}), Err(error)=>json!({"authenticated":false,"hub":configured_hub(),"error":error}) }
}
// Tokens stay in the native process; cloud QA requires its own entitlement.
pub fn cloud_session(root: &Path) -> Result<(String,String),String> {
    let user=require(root,"")?;
    if user.role!="admin" && !user.authorizations.iter().any(|v|v=="qa") {return Err("FORBIDDEN: 当前账号没有知识库问答权限".into());}
    let s=stored()?;
    if profile(&base(),&s.hub,&s.user)?!=root || user.id!=s.user.id {return Err("UNAUTHORIZED: 账号已切换".into());}
    Ok((s.hub,s.token))
}
// Native child pipe only: never exposed as an IPC command or renderer response.
pub fn company_session_cli() {
    let root=PathBuf::from(std::env::var_os("BIODSH_HOME").unwrap_or_default());
    let result=(||->Result<Value,String>{
        require(&root,"")?;
        let s=stored()?;
        if profile(&base(),&s.hub,&s.user)?!=root{return Err("account changed".into());}
        Ok(json!({"hub":s.hub,"token":s.token}))
    })();
    match result{Ok(v)=>println!("{v}"),Err(_)=>std::process::exit(1)}
}
pub fn login(hub: &str, username: &str, password: &str) -> Result<(), String> {
    let hub = normalize(hub)?;
    if username.trim().is_empty() || password.is_empty() { return Err("请输入用户名和密码".into()); }
    enabled(&hub)?;
    let reply = request(&hub,"login",None,Some(json!({"username":username.trim(),"password":password})))?;
    let token = reply["token"].as_str().filter(|s|!s.is_empty()).ok_or("登录未完成，请重试")?;
    let user = parse_user(&reply["user"])?;
    let checked = parse_user(&request(&hub,"me",Some(token),None)?["user"])?;
    if user.id != checked.id { return Err("登录信息不一致，请重新登录".into()); }
    let session = Session { hub:hub.clone(),token:token.into(),user:checked,restart_nonce:None };
    let _guard=SESSION_WRITE.lock().map_err(|_|"登录存储忙，请重试")?;
    let dir = base().join("accounts"); std::fs::create_dir_all(&dir).map_err(|_|"无法保存服务地址，请重试")?;
    std::fs::write(dir.join("hub.json"),json!({"platformUrl":hub}).to_string()).map_err(|_|"无法保存服务地址，请重试")?;
    let value=serde_json::to_string(&session).map_err(|_|"无法保存登录")?;
    entry()?.set_password(&value).map_err(|_|"无法保存登录凭据到系统安全存储".to_string())?;
    if entry()?.get_password().ok().as_deref()!=Some(value.as_str()){return Err("登录凭据保存后校验失败，请重试".into());}
    let marker=dir.join("logged-out");if marker.exists(){std::fs::remove_file(marker).map_err(|_|"无法完成登录状态切换")?;}
    *CHECKED.lock().unwrap()=None;Ok(())
}
pub fn logout() -> Result<(), String> {
    let _guard=SESSION_WRITE.lock().map_err(|_|"登录存储忙，请重试")?;
    let session = stored().ok();
    let dir=base().join("accounts");std::fs::create_dir_all(&dir).map_err(|_|"无法保存退出状态")?;
    // Persist revocation first: stale OS credential reads or a crash cannot restore a logged-out session.
    std::fs::write(dir.join("logged-out"),b"logged out").map_err(|_|"无法保存退出状态")?;
    *CHECKED.lock().unwrap()=None;
    entry()?.set_password("{}").map_err(|_|"已锁定账号，但系统无法清理旧凭据")?;
    match entry()?.delete_credential() { Ok(()) | Err(keyring::Error::NoEntry) => (), Err(_)=>return Err("无法清除系统登录凭据".into()) }
    if let Some(s)=session { let _=request(&s.hub,"logout",Some(&s.token),Some(json!({}))); } Ok(())
}
// Explicit allowlist: a newly added command cannot accidentally become public.
pub fn command_unit(command: &str) -> Result<Option<&'static str>, String> {
    match command {
        "cloud_qa" => Ok(Some("qa")),
        "hub_status"|"hub_login"|"hub_logout"|"hub_remembered_login"|"hub_forget_login"|"window_control" => Ok(None),
        "open_path"|"open_external"|"education_start"|"education_delete"|"hub_admin"|"app_info"|"settings_get"|"settings_set"|"credential_get"|"credential_set"|"env_status"|"dsh_status"|"skills_catalog"|"skills_statuses"|"client_log"|"check_updates"|"dsh_stop"|"dsh_bounds" => Ok(Some("")),
        "dsh_start"|"dsh_restart"|"dsh_reload"|"dsh_rpc"|"dsh_open_session"|"dsh_new_session"|"dsh_set_context"|"assistant_ask"|"deepseek_balance"|"session_export"|"session_delete"|"ratings_get"|"ratings_set" => Ok(Some("")),
        "personal_compute"|"ssh_hosts_list"|"ssh_hosts_save"|"ssh_hosts_remove"|"ssh_hosts_select"|"ssh_hosts_trust"|"ssh_hosts_test"|"ssh_hosts_metrics"|"ssh_hosts_files"|"env_install"|"env_install_extra"|"skills_readme"|"skills_install"|"skills_uninstall"|"workspace_files"|"read_workspace_image"|"refdata_list"|"refdata_install"|"refdata_remove"|"db_catalog"|"embed_query"|"demos_seed"|"git_status"|"git_init"|"git_commit"|"pick_folder" => Ok(Some("")),
        _ => Err("FORBIDDEN: 此功能暂不可用，请联系管理员".into())
    }
}
pub fn check_model() {
    let root = PathBuf::from(std::env::var_os("BIODSH_HOME").unwrap_or_default());
    match require(&root,"") { Ok(user)=>println!("{}",json!({"ok":true,"userId":user.id})), Err(_)=>{ println!("{}",json!({"ok":false}));std::process::exit(1); } }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[cfg(feature="local-access")]
    fn local_release_uses_legacy_without_login_but_rejects_other_profile_roots() {
        let root=base();assert_eq!(startup_root(),root);
        assert!(require(&root,"bioflow").is_ok());assert!(require(&root,"qa").is_ok());
        assert!(require(&root.join("accounts/profiles/other"),"qa").is_err());
        assert_eq!(status(&root)["localAccess"],true);
        assert!(command_unit("arbitrary_shell").is_err());
    }
    fn user() -> User { User{id:"761f059b-1111-4111-8111-123456789abc".into(),username:"a".into(),role:"user".into(),authorizations:vec!["qa".into()] } }
    #[test]
    #[cfg(not(feature = "local-access"))]
    fn login_switch_revocation_and_logout_use_isolated_secure_storage() {
        use std::{io::{Read,Write},sync::{Arc,atomic::{AtomicBool,Ordering}}};
        let root=std::env::temp_dir().join(format!("omaax-auth-test-{}",uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();std::fs::write(root.join("settings.json"),"legacy settings").unwrap();std::fs::write(root.join("conversation.txt"),"legacy conversation").unwrap();
        initialize(root.clone());
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();listener.set_nonblocking(true).unwrap();
        let url=format!("http://{}",listener.local_addr().unwrap());let stop=Arc::new(AtomicBool::new(false));let revoked=Arc::new(AtomicBool::new(false));let enabled_flag=Arc::new(AtomicBool::new(true));
        let (done,revoke,on)=(stop.clone(),revoked.clone(),enabled_flag.clone());
        let worker=std::thread::spawn(move||while !done.load(Ordering::SeqCst) {
            let Ok((mut socket,_))=listener.accept() else {std::thread::sleep(Duration::from_millis(5));continue};
            socket.set_nonblocking(false).unwrap();socket.set_read_timeout(Some(Duration::from_secs(3))).unwrap();let mut bytes=Vec::new();let mut buffer=[0u8;4096];
            loop {let n=socket.read(&mut buffer).unwrap();if n==0{break}bytes.extend_from_slice(&buffer[..n]);
                if let Some(i)=bytes.windows(4).position(|s|s==b"\r\n\r\n") {let header=String::from_utf8_lossy(&bytes[..i]).to_lowercase();let len=header.lines().find_map(|l|l.strip_prefix("content-length:").and_then(|v|v.trim().parse::<usize>().ok())).unwrap_or(0);if bytes.len()>=i+4+len{break}}
            }
            let request=String::from_utf8_lossy(&bytes);let route=request.lines().next().unwrap_or("");let mut code=200;
            let is_b=request.contains("token-b")||request.contains("\"username\":\"b\"");let mut u=user();if is_b {u.id="761f059b-1111-4111-8111-123456789abd".into();u.username="b".into();u.authorizations=vec![];}
            let body=if route.contains("/status "){json!({"ok":true,"auth_enabled":on.load(Ordering::SeqCst)})}
            else if route.contains("/login "){json!({"ok":true,"token":if is_b{"token-b"}else{"token-a"},"user":u})}
            else if route.contains("/me "){if revoke.load(Ordering::SeqCst){code=401;json!({"ok":false})}else{json!({"ok":true,"user":u})}}
            else {json!({"ok":true})}.to_string();
            let _=write!(socket,"HTTP/1.1 {code} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
        });
        let result=std::panic::catch_unwind(|| {
            assert!(stored().is_err());assert_eq!(startup_root(),root.join("accounts/locked"));
            login(&url,"a","test-only-password").unwrap();let a=startup_root();assert!(require(&a,"qa").is_ok());assert!(require(&a,"bioflow").is_ok());
            std::fs::create_dir_all(&a).unwrap();std::fs::write(a.join("conversation.txt"),"account a conversation").unwrap();
            remember_login("a","test-only-password",true).unwrap();
            assert_eq!(remembered_login().unwrap().unwrap().username,"a");
            arm_login_restart().unwrap();begin_desktop_session().unwrap();assert!(require(&a,"bioflow").is_ok());
            // The internal handoff is one-use; normal reopen requires a password even when remembered.
            begin_desktop_session().unwrap();assert!(stored().is_err());assert_eq!(startup_root(),root.join("accounts/locked"));
            assert_eq!(remembered_login().unwrap().unwrap().password,"test-only-password");
            assert_eq!(std::fs::read_to_string(a.join("conversation.txt")).unwrap(),"account a conversation");
            std::env::set_var("OMAAX_LOGIN_RESTART","forged");begin_desktop_session().unwrap();assert!(stored().is_err());
            forget_login().unwrap();assert!(remembered_login().unwrap().is_none());
            login(&url,"a","test-only-password").unwrap();assert_eq!(startup_root(),a);
            login(&url,"b","test-only-password").unwrap();let b=startup_root();assert_ne!(a,b);assert!(require(&a,"qa").is_err());assert!(require(&b,"bioflow").is_ok());assert!(!b.join("conversation.txt").exists());assert!(require(&b,command_unit("dsh_start").unwrap().unwrap()).is_ok());assert!(require(&b,"docgen").is_ok());assert!(cloud_session(&b).is_err());
            assert!(!std::fs::read_to_string(root.join("accounts/hub.json")).unwrap().contains("token-"));
            revoked.store(true,Ordering::SeqCst);*CHECKED.lock().unwrap()=None;assert!(require(&b,"bioflow").unwrap_err().contains("UNAUTHORIZED"));revoked.store(false,Ordering::SeqCst);
            enabled_flag.store(false,Ordering::SeqCst);assert!(login(&url,"a","test-only-password").is_err());assert!(require(&b,"").is_err());enabled_flag.store(true,Ordering::SeqCst);
            login(&url,"a","test-only-password").unwrap();assert_eq!(startup_root(),a);assert_eq!(std::fs::read_to_string(a.join("conversation.txt")).unwrap(),"account a conversation");
            let residual=entry().unwrap().get_password().unwrap();
            logout().unwrap();assert!(stored().is_err());assert!(require(&a,"qa").is_err());
            entry().unwrap().set_password(&residual).unwrap();assert!(stored().is_err(),"Persisted logout must reject even an OS-stale credential");
            login(&url,"b","test-only-password").unwrap();assert!(require(&startup_root(),"bioflow").is_ok());logout().unwrap();
            assert_eq!(std::fs::read_to_string(root.join("settings.json")).unwrap(),"legacy settings");assert_eq!(std::fs::read_to_string(root.join("conversation.txt")).unwrap(),"legacy conversation");
        });
        let _=entry().and_then(|e|e.delete_credential().map_err(|e|e.to_string()));stop.store(true,Ordering::SeqCst);worker.join().unwrap();std::fs::remove_dir_all(root).unwrap();result.unwrap();
    }
    fn mock(status:u16, body:&str) -> (String,std::thread::JoinHandle<String>) {
        use std::io::{Read,Write};
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();let url=format!("http://{}",listener.local_addr().unwrap());let body=body.to_string();
        let task=std::thread::spawn(move||{let(mut socket,_)=listener.accept().unwrap();let mut buffer=[0u8;8192];let n=socket.read(&mut buffer).unwrap();let request=String::from_utf8_lossy(&buffer[..n]).to_string();write!(socket,"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len()).unwrap();request});(url,task)
    }
    #[test] fn disabled_and_malformed_hubs_never_allow_anonymous_access() {
        for body in [r#"{"ok":true,"auth_enabled":false}"#,r#"{"ok":true}"#,r#"{"ok":false,"auth_enabled":true}"#,"<html>404</html>"] {
            let(url,t)=mock(200,body);assert!(enabled(&url).is_err());assert!(t.join().unwrap().starts_with("GET /api/auth/status "));
        }
        let(url,t)=mock(200,r#"{"ok":true,"auth_enabled":true}"#);assert!(enabled(&url).is_ok());t.join().unwrap();
    }
    #[test] fn errors_and_bearer_header_are_preserved_without_leaking_token() {
        for (code,label) in [(401,"UNAUTHORIZED"),(403,"FORBIDDEN"),(404,"404"),(500,"500"),(302,"302")] {
            let(url,t)=mock(code,r#"{"ok":false}"#);let error=request(&url,"me",Some("test-private-token"),None).unwrap_err();
            assert!(error.contains(label));assert!(!error.contains("test-private-token"));assert!(t.join().unwrap().to_lowercase().contains("authorization: bearer test-private-token"));
        }
    }
    #[test] fn wrong_password_has_actionable_login_message() {
        let(url,t)=mock(401,r#"{"ok":false}"#);
        assert!(request(&url,"login",None,Some(json!({"username":"test","password":"not-real"}))).unwrap_err().contains("用户名或密码错误"));t.join().unwrap();
        assert_eq!(command_unit("open_path").unwrap(),Some(""));
    }
    #[test] fn profiles_do_not_adopt_legacy_or_other_accounts() {
        let base=Path::new("legacy");let mut b=user();b.id="761f059b-1111-4111-8111-123456789abd".into();
        let a=profile(base,"https://one.test/",&user()).unwrap();
        assert_ne!(a,base);assert_ne!(a,profile(base,"https://one.test",&b).unwrap());assert_ne!(a,profile(base,"https://two.test",&user()).unwrap());
        b.id="../../legacy".into();assert!(profile(base,"https://one.test",&b).is_err());
    }
    #[test] fn https_and_credentials_are_constrained() {
        for s in ["http://remote.test","https://u:p@a.test","https://a.test/path","https://a.test/?token=x","file:///tmp"] {assert!(normalize(s).is_err(),"{s}");}
        assert!(external_url("file:///C:/other-account/settings.json").is_err());assert!(external_url("https://example.com/help").is_ok());
        assert_eq!(normalize("https://A.test/").unwrap(),"https://a.test");assert!(normalize("http://127.0.0.1:8090").is_ok());
    }
    #[test] fn privileges_fail_closed() {
        assert!(has(&user(),"qa"));assert!(has(&user(),"bioflow"));let mut admin=user();admin.role="admin".into();assert!(has(&admin,"bioflow"));
        assert!(command_unit("new_command").is_err());assert!(command_unit("migrate_import").is_err());assert!(command_unit("platform_login").is_err());
        assert_eq!(command_unit("personal_compute").unwrap(),Some(""));assert_eq!(command_unit("hub_login").unwrap(),None);
    }
    #[test] fn cloud_roles_and_qa_scope() {
        let mut ordinary=user();ordinary.authorizations.clear();
        for role in ["user","researcher","guest"] {
            ordinary.role=role.into();
            assert!(has(&ordinary,"bioflow") && has(&ordinary,"docgen"));
            assert!(!has(&ordinary,"qa"));
        }
        ordinary.authorizations=vec!["bioflow".into(),"docgen".into()];assert!(!has(&ordinary,"qa"));
        ordinary.authorizations.push("qa".into());assert!(has(&ordinary,"qa"));
        for command in ["dsh_start","assistant_ask","session_delete"] {assert_eq!(command_unit(command).unwrap(),Some(""));}
        for role in ["user","researcher","guest","admin"] {let mut v=serde_json::to_value(user()).unwrap();v["role"]=json!(role);assert!(parse_user(&v).is_ok());}
        let mut v=serde_json::to_value(user()).unwrap();v["role"]=json!("superuser");assert!(parse_user(&v).is_err());
        assert_eq!(command_unit("cloud_qa").unwrap(),Some("qa"));
    }
    #[test]
    #[ignore = "requires an explicitly provisioned disposable cloud account"]
    #[cfg(not(feature="local-access"))]
    fn live_cloud_secure_login_and_history() {
        let root=PathBuf::from(std::env::var("OMAAX_LIVE_TEST_HOME").expect("isolated test home"));
        assert!(root.file_name().unwrap().to_string_lossy().starts_with("cloud-native-"));
        std::fs::create_dir_all(&root).unwrap();initialize(root.clone());
        std::fs::write(root.join("legacy-dialogue.txt"),"preserved legacy").unwrap();
        let name=std::env::var("OMAAX_LIVE_TEST_USER").unwrap();let password=std::env::var("OMAAX_LIVE_TEST_PASSWORD").unwrap();
        login("https://cloud.haiguji.com",&name,&password).unwrap();let profile=startup_root();
        assert_ne!(profile,root);assert!(!profile.join("legacy-dialogue.txt").exists());
        assert!(require(&profile,"qa").is_ok());assert!(require(&profile,"bioflow").is_ok());
        *CHECKED.lock().unwrap()=None;assert_eq!(startup_root(),profile);assert!(status(&profile)["authenticated"]==true);
        let session=crate::cloud_qa::call(&profile,"create",json!({"title":"OMaaX native history acceptance"})).unwrap();
        let id=session["id"].as_str().unwrap();
        let blocks=json!([{"type":"sources","items":[{"id":"S1","title":"Acceptance evidence","doi":"10.1/test"}]}]);
        crate::cloud_qa::call(&profile,"message",json!({"id":id,"role":"assistant","text":"Saved native acceptance evidence","blocks":blocks})).unwrap();
        let detail=crate::cloud_qa::call(&profile,"detail",json!({"id":id})).unwrap();
        assert_eq!(detail["messages"][0]["blocks"][0]["items"][0]["doi"],"10.1/test");
        logout().unwrap();assert!(crate::cloud_qa::call(&profile,"list",json!({})).is_err());
        login("https://cloud.haiguji.com",&name,&password).unwrap();assert_eq!(startup_root(),profile);
        let detail=crate::cloud_qa::call(&profile,"detail",json!({"id":id})).unwrap();assert_eq!(detail["messages"][0]["blocks"][0]["items"][0]["doi"],"10.1/test");
        logout().unwrap();assert_eq!(std::fs::read_to_string(root.join("legacy-dialogue.txt")).unwrap(),"preserved legacy");
    }
}

/// Only profile-owned local workspaces can be added. Server paths use SSH account rules.


pub fn audit(root:&Path,user:&User,action:&str) {
    use std::io::Write;
    let dir=root.join("logs");if std::fs::create_dir_all(&dir).is_err(){return;}
    if let Ok(mut file)=std::fs::OpenOptions::new().create(true).append(true).open(dir.join("account-access.jsonl")) {
        let event=json!({"time":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs(),"userId":user.id,"action":action,"event":"authorized_request"});
        let _=writeln!(file,"{event}");
    }
}

pub fn external_url(value:&str)->Result<String,String>{
    let url=reqwest::Url::parse(value).map_err(|_|"链接无效")?;
    if !matches!(url.scheme(),"http"|"https") || url.host_str().is_none() {return Err("仅允许打开网页链接".into());} Ok(url.to_string())
}
