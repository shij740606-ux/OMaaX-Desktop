use keyring::Entry;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, io::{BufRead, Read, Write}, path::{Path, PathBuf}, process::{Command, Stdio}, sync::Mutex};

static WRITE_LOCK: Mutex<()> = Mutex::new(());
static AUDIT_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Host {
    pub id: String, pub name: String, pub host: String, pub port: u16, pub username: String,
    #[serde(default)] pub worker_node_id: Option<String>,
    pub auth: String, pub selected: bool, pub fingerprint: Option<String>, pub host_key: Option<String>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Draft {
    pub id: Option<String>, pub name: String, pub host: String, pub port: u16, pub username: String,
    pub auth: String, pub password: Option<String>, pub private_key: Option<String>, pub passphrase: Option<String>,
}
#[derive(Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Secret { password: String, private_key: String, passphrase: String }
#[derive(Serialize, Deserialize)]
struct SecretParts { revision: String, count: usize }

fn secret_entry(id: &str) -> Result<Entry, String> { Entry::new("BioDSH SSH", id).map_err(|_| "系统安全存储不可用".into()) }
fn secret(id: &str) -> Result<Secret, String> {
    let value = secret_entry(id)?.get_password().map_err(|_| "服务器凭据未保存，请编辑服务器重新填写".to_string())?;
    if let Ok(parts) = serde_json::from_str::<SecretParts>(&value) {
        if parts.count > 512 { return Err("服务器凭据无效".into()); }
        let mut payload = String::new();
        for index in 0..parts.count {
            payload.push_str(&secret_entry(&format!("{id}-{}-{index}", parts.revision))?.get_password().map_err(|_| "服务器凭据不完整".to_string())?);
        }
        return serde_json::from_str(&payload).map_err(|_| "服务器凭据无效".into());
    }
    serde_json::from_str(&value).map_err(|_| "服务器凭据无效".into())
}
fn delete_parts(id: &str, record: &str) {
    if let Ok(parts) = serde_json::from_str::<SecretParts>(record) {
        for index in 0..parts.count.min(512) {
            if let Ok(entry) = secret_entry(&format!("{id}-{}-{index}", parts.revision)) { let _ = entry.delete_credential(); }
        }
    }
}
fn save_secret(id: &str, secret: &Secret) -> Result<(), String> {
    // Windows Credential Manager has a per-entry size limit; split large private keys without writing plaintext files.
    let payload = serde_json::to_string(secret).map_err(|e| e.to_string())?;
    if payload.len() > 262144 { return Err("SSH 凭据过大".into()); }
    let old = secret_entry(id)?.get_password().unwrap_or_default();
    let mut parts = SecretParts { revision: uuid::Uuid::new_v4().simple().to_string(), count: 0 };
    let mut remaining = payload.as_str();
    while !remaining.is_empty() {
        let mut end = remaining.len().min(900); while !remaining.is_char_boundary(end) { end -= 1; }
        let item = secret_entry(&format!("{id}-{}-{}", parts.revision, parts.count))?;
        if item.set_password(&remaining[..end]).is_err() { delete_parts(id, &serde_json::to_string(&parts).unwrap_or_default()); return Err("保存 SSH 凭据失败".into()); }
        parts.count += 1; remaining = &remaining[end..];
    }
    let record = serde_json::to_string(&parts).map_err(|e| e.to_string())?;
    if secret_entry(id)?.set_password(&record).is_err() { delete_parts(id, &record); return Err("保存 SSH 凭据失败".into()); }
    let verified=self::secret(id).is_ok_and(|loaded| loaded.password==secret.password && loaded.private_key==secret.private_key && loaded.passphrase==secret.passphrase);
    if !verified {
        if old.is_empty(){let _=secret_entry(id)?.delete_credential();}else{let _=secret_entry(id)?.set_password(&old);}
        delete_parts(id,&record);return Err("凭据保存后无法读取，原有凭据已保留，请检查系统安全存储".into());
    }
    delete_parts(id, &old); Ok(())
}
pub fn list(root: &Path) -> Result<Vec<Host>, String> {
    let file = root.join("ssh/hosts.json");
    if !file.exists() { return Ok(Vec::new()); }
    serde_json::from_str(&fs::read_to_string(file).map_err(|e| e.to_string())?).map_err(|_| "服务器列表文件损坏，请勿覆盖原文件".into())
}
fn persist(root: &Path, hosts: &[Host]) -> Result<(), String> {
    let dir = root.join("ssh"); fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let temp = dir.join("hosts.tmp");
    fs::write(&temp, serde_json::to_vec_pretty(hosts).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    fs::rename(temp, dir.join("hosts.json")).map_err(|e| e.to_string())
}
fn validate(draft: &Draft) -> Result<(), String> {
    if draft.name.trim().is_empty() || draft.name.len() > 120 || draft.port == 0 || draft.username.trim().is_empty() || draft.username.len() > 128 {
        return Err("请填写名字、用户名和有效端口".into());
    }
    if draft.host.is_empty() || draft.host.len() > 253 || !draft.host.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':' | '_')) {
        return Err("请填写有效 IP 或主机名，不包含协议或路径".into());
    }
    if !matches!(draft.auth.as_str(), "password" | "key") { return Err("认证方式无效".into()); }
    Ok(())
}
pub fn save(root: &Path, draft: Draft) -> Result<Vec<Host>, String> {
    validate(&draft)?;
    let _guard = WRITE_LOCK.lock().map_err(|_| "保存锁不可用")?;
    let mut hosts = list(root)?;
    let existing = draft.id.as_ref().and_then(|id| hosts.iter().find(|h| &h.id == id)).cloned();
    if draft.id.is_some() && existing.is_none() { return Err("服务器不存在".into()); }
    let id = existing.as_ref().map(|h| h.id.clone()).unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let mut credentials = if existing.is_some() { secret(&id).unwrap_or_default() } else { Secret::default() };
    if let Some(value) = draft.password.filter(|v| !v.is_empty()) { credentials.password = value; }
    if let Some(value) = draft.private_key.filter(|v| !v.trim().is_empty()) { credentials.private_key = value; }
    if let Some(value) = draft.passphrase { credentials.passphrase = value; }
    if (draft.auth == "password" && credentials.password.is_empty()) || (draft.auth == "key" && credentials.private_key.is_empty()) { return Err("请填写所选认证方式的密码或私钥".into()); }
    let same_endpoint = existing.as_ref().is_some_and(|h| h.host == draft.host.trim() && h.port == draft.port);
    let host = Host { worker_node_id: existing.as_ref().filter(|_| same_endpoint).and_then(|h| h.worker_node_id.clone()), id: id.clone(), name: draft.name.trim().into(), host: draft.host.trim().into(), port: draft.port,
        username: draft.username.trim().into(), selected: existing.as_ref().is_some_and(|h| h.selected && h.username == draft.username.trim() && h.auth == draft.auth) && same_endpoint, auth: draft.auth,
        fingerprint: existing.as_ref().filter(|_| same_endpoint).and_then(|h| h.fingerprint.clone()),
        host_key: existing.as_ref().filter(|_| same_endpoint).and_then(|h| h.host_key.clone()) };
    save_secret(&id, &credentials)?;
    hosts.retain(|h| h.id != id); hosts.push(host); persist(root, &hosts)?; Ok(hosts)
}
pub fn remove(root: &Path, id: &str) -> Result<Vec<Host>, String> {
    let _guard = WRITE_LOCK.lock().map_err(|_| "保存锁不可用")?;
    let mut hosts = list(root)?; if !hosts.iter().any(|h|h.id==id) {return Err("服务器不存在".into());} hosts.retain(|h| h.id != id); persist(root, &hosts)?;
    let entry = secret_entry(id)?;
    if let Ok(record) = entry.get_password() { delete_parts(id, &record); }
    let _ = entry.delete_credential(); Ok(hosts)
}
pub fn select(root: &Path, id: &str, selected: bool) -> Result<Vec<Host>, String> {
    let _guard = WRITE_LOCK.lock().map_err(|_| "保存锁不可用")?;
    let mut hosts = list(root)?; let host = hosts.iter_mut().find(|h| h.id == id).ok_or("服务器不存在")?;
    if selected && host.host_key.is_none() { return Err("请先测试连接并确认主机指纹".into()); }
    host.selected = selected; persist(root, &hosts)?; Ok(hosts)
}
pub fn trust(root: &Path, id: &str, host_key: &str, fingerprint: &str) -> Result<Vec<Host>, String> {
    let _guard = WRITE_LOCK.lock().map_err(|_| "保存锁不可用")?;
    if host_key.len() < 20 || host_key.len() > 16384 || !fingerprint.starts_with("SHA256:") { return Err("主机指纹无效".into()); }
    let mut hosts = list(root)?; let host = hosts.iter_mut().find(|h| h.id == id).ok_or("服务器不存在")?;
    host.host_key = Some(host_key.into()); host.fingerprint = Some(fingerprint.into()); host.selected = false;
    persist(root, &hosts)?; Ok(hosts)
}
pub fn connection(root: &Path, id: &str) -> Result<Value, String> {
    let host = list(root)?.into_iter().find(|h| h.id == id).ok_or("服务器不存在")?;
    if host.host_key.is_none() { return Err("请先确认服务器主机指纹".into()); }
    let credentials = secret(id)?;
    Ok(json!({"host":host.host,"port":host.port,"username":host.username,"hostKey":host.host_key,
        "password":if host.auth=="password" {Some(credentials.password)}else{None},
        "privateKey":if host.auth=="key"{Some(credentials.private_key)}else{None},"passphrase":credentials.passphrase}))
}
pub fn call(root: &Path, node: &Path, script: &Path, id: &str, operation: &str, command: &str, timeout: u64, agent: bool) -> Result<Value, String> {
    call_with_context(root,node,script,id,operation,command,timeout,agent,&Value::Null)
}
fn call_with_context(root: &Path, node: &Path, script: &Path, id: &str, operation: &str, command: &str, timeout: u64, agent: bool, context:&Value) -> Result<Value, String> {
    let request_id=uuid::Uuid::new_v4().to_string();
    let started=std::time::Instant::now();
    let mut secrets=Vec::new();
    audit_command(root,json!({"requestId":request_id,"event":"requested","serverId":id,"operation":operation,"actor":if agent{"model"}else{"application"},"sessionId":context["_sessionId"],"commandSha256":digest(command),"commandBytes":command.len(),"timeoutSeconds":timeout}))?;
    let result=(|| {
    let host = list(root)?.into_iter().find(|h| h.id == id).ok_or("服务器不存在")?;
    if agent && !host.selected { return Err("该服务器未被用户选择，禁止连接".into()); }
    if !matches!(operation,"probe"|"test"|"execute") {return Err("服务器操作无效".into());}
    if operation != "probe" && host.host_key.is_none() { return Err("请在服务器管理页面确认主机指纹".into()); }
    validate_command(command)?;
    let credentials = if operation == "probe" { Secret::default() } else { secret(id)? };
    secrets=vec![credentials.password.clone(),credentials.private_key.clone(),credentials.passphrase.clone()];
    audit_command(root,json!({"requestId":request_id,"event":"executing","serverId":id,"operation":operation,"description":audit_text(context["description"].as_str().unwrap_or(""),&secrets,1024),"command":audit_text(command,&secrets,65536)}))?;
    let request = json!({"operation": operation, "host": host.host, "port": host.port, "username": host.username,
        "hostKey": host.host_key, "password": if host.auth == "password" { Some(credentials.password) } else { None },
        "privateKey": if host.auth == "key" { Some(credentials.private_key) } else { None },
        "passphrase": credentials.passphrase, "command": command, "timeoutSeconds": timeout});
    run_request(node, script, request)
    })();
    let outcome=match &result {
        Ok(value)=>json!({"requestId":request_id,"event":"finished","serverId":id,"operation":operation,"elapsedMs":started.elapsed().as_millis(),"exitCode":value["exitCode"],"stdout":audit_text(value["stdout"].as_str().unwrap_or(""),&secrets,4096),"stderr":audit_text(value["stderr"].as_str().unwrap_or(""),&secrets,4096),"stdoutSha256":digest(value["stdout"].as_str().unwrap_or("")),"stderrSha256":digest(value["stderr"].as_str().unwrap_or(""))}),
        Err(error)=>json!({"requestId":request_id,"event":"failed","serverId":id,"operation":operation,"elapsedMs":started.elapsed().as_millis(),"error":audit_text(error,&secrets,4096)})
    };
    if let Err(error)=audit_command(root,outcome) {
        return match result {Ok(mut value)=>{value["auditWarning"]=json!(error);Ok(value)},Err(original)=>Err(format!("{original}；{error}"))};
    }
    result
}
fn digest(value:&str)->String {format!("{:x}",Sha256::digest(value.as_bytes()))}
fn audit_command(root:&Path,mut value:Value)->Result<(),String>{
    let _guard=AUDIT_LOCK.lock().map_err(|_|"服务器操作日志不可用")?;
    let dir=root.join("logs");fs::create_dir_all(&dir).map_err(|_|"无法保存服务器操作日志")?;
    value["time"]=json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis());
    let mut file=fs::OpenOptions::new().create(true).append(true).open(dir.join("server-commands.jsonl")).map_err(|_|"无法保存服务器操作日志")?;
    writeln!(file,"{value}").map_err(|_|"无法保存服务器操作日志".into())
}
fn audit_text(value:&str,secrets:&[String],limit:usize)->String{
    let mut value=value.to_string();
    for secret in secrets.iter().filter(|s|!s.is_empty()){value=value.replace(secret,"[已隐藏凭据]");}
    if value.contains("PRIVATE KEY-----"){return "[已隐藏私钥内容]".into();}
    // Arbitrary scripts can contain credentials; omit sensitive lines instead of guessing their shell syntax.
    let text=value.lines().map(|line|{
        let lower=line.to_ascii_lowercase();
        if ["password","passwd","passphrase","api_key","apikey","api-key","token","secret","authorization","bearer ","sk-","akia"].iter().any(|marker|lower.contains(marker)) {
            "[已隐藏含凭据的行]"
        }else{line}
    }).collect::<Vec<_>>().join("\n");
    if text.len()<=limit{return text;}
    let mut end=limit;while !text.is_char_boundary(end){end-=1;}
    format!("{}\n[日志摘要已截断]",&text[..end])
}
fn validate_command(command: &str) -> Result<(), String> {
    // Large bundled workflow catalogs travel on SSH stdin, not as a shell argument.
    if command.len() > 6 * 1024 * 1024 || command.contains('\0') { return Err("执行内容超过 6 MB 或包含无效字符".into()); }
    Ok(())
}
fn environment_command(action: &str) -> Result<String, String> {
    if !matches!(action, "inspect" | "repair" | "status") { return Err("环境操作无效".into()); }
    Ok(format!("{}\nomaax_repair_action={}\n{}", include_str!("../../resources/ssh/user_runtime.sh"), action, include_str!("../../resources/ssh/environment_repair.sh")))
}
fn environment_options(args: &Value) -> Result<String, String> {
    let python=args["pythonVersion"].as_str().unwrap_or("3.12");
    let channel=args["channel"].as_str().unwrap_or("conda-forge");
    if python.is_empty() || channel.is_empty() || python.contains('\0') || channel.contains('\0') {return Err("Python 版本或软件源无效".into());}
    Ok(format!("export OMAAX_BOOTSTRAP_PYTHON={} OMAAX_BOOTSTRAP_CHANNEL={}\n",shell_quote(python),shell_quote(channel)))
}
fn shell_quote(value:&str)->String {format!("'{}'",value.replace('\'',"'\"'\"'"))}
fn environment_request(args:&Value)->Result<String,String>{
    if let Some(command)=args.get("command") {
        let command=command.as_str().ok_or("环境修复命令必须是文本")?;
        if command.trim().is_empty(){return Err("环境修复命令不能为空".into());}
        validate_command(command)?;
        return Ok(command.into());
    }
    Ok(environment_options(args)?+&environment_command(args["action"].as_str().unwrap_or("inspect"))?)
}
fn repair_environment(root: &Path, node: &Path, script: &Path, args: &Value) -> Result<Value, String> {
    let id=args["serverId"].as_str().ok_or("缺少服务器编号")?;
    if !list(root)?.iter().any(|host| host.id==id && host.selected) { return Err("该服务器未被用户选择，禁止连接".into()); }
    let command=environment_request(args)?;
    let result=call_with_context(root,node,script,id,"execute",&command,args["timeoutSeconds"].as_u64().unwrap_or(900),true,args)?;
    if result["exitCode"].as_i64()!=Some(0) {
        return Err(format!("SSH 环境修复未完成，原有数据保留。请根据日志继续诊断：{} {}",result["stdout"].as_str().unwrap_or(""),result["stderr"].as_str().unwrap_or("")));
    }
    Ok(result)
}
fn execute_tool(root:&Path,node:&Path,script:&Path,args:&Value)->Result<Value,String>{
    let id=args["serverId"].as_str().ok_or("缺少服务器编号")?;
    let command=args["command"].as_str().filter(|s|!s.trim().is_empty()).ok_or("缺少执行命令")?;
    call_with_context(root,node,script,id,"execute",command,args["timeoutSeconds"].as_u64().unwrap_or(900),true,args)
}

pub fn install_environment(root: &Path, node: &Path, script: &Path, id: &str) -> Result<Value,String> {
    // Reuse detached user-level setup; credentials and system package installation are never requested.
    crate::personal_compute::request(root,node,script,id,"setup_start",json!({"retry":true}),false)
}
pub(crate) fn run_request(node: &Path, script: &Path, request: Value) -> Result<Value,String> {
    if script.file_name().and_then(|n|n.to_str())==Some("client.cjs") {
        return crate::ssh_pool::request(node,script,request);
    }
    let mut process = Command::new(node); process.arg(script).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)] { use std::os::windows::process::CommandExt; process.creation_flags(0x0800_0000); }
    let mut child = process.spawn().map_err(|_| "SSH 运行时不可用，请重新构建或安装完整桌面程序".to_string())?;
    let mut input = child.stdin.take().ok_or("SSH 输入管道不可用")?;
    let bytes=serde_json::to_vec(&request).map_err(|e|e.to_string())?;
    let writer=std::thread::spawn(move||input.write_all(&bytes));
    let mut stdout=child.stdout.take().ok_or("SSH 输出管道不可用")?;
    let reader=std::thread::spawn(move||{let mut bytes=Vec::new();stdout.by_ref().take(8*1048576).read_to_end(&mut bytes).map(|_|bytes)});
    let timeout=request["timeoutSeconds"].as_u64().unwrap_or(30);
    let started=std::time::Instant::now();
    loop {
        if child.try_wait().map_err(|e|e.to_string())?.is_some(){break;}
        if timeout!=0 && started.elapsed().as_secs()>=timeout.max(5).saturating_add(15){let _=child.kill();let _=child.wait();let _=writer.join();let _=reader.join();return Err("SSH 操作超时，已释放本地连接。远端任务可能已提交，请刷新核对，勿重复提交".into());}
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    writer.join().map_err(|_|"SSH 输入线程中断")?.map_err(|e|e.to_string())?;
    let output=reader.join().map_err(|_|"SSH 输出线程中断")?.map_err(|e|e.to_string())?;
    let value: Value = serde_json::from_slice(&output).map_err(|_| "SSH 运行时返回无效响应".to_string())?;
    if let Some(error) = value.get("error").and_then(Value::as_str) { return Err(error.into()); }
    Ok(value)
}

pub fn metrics(root: &Path, node: &Path, script: &Path, id: &str, agent: bool) -> Result<Value, String> {
    if agent && !list(root)?.iter().any(|host| host.id == id && host.selected) {
        return Err("该服务器未被用户选择，禁止连接".into());
    }
    // Compile the read-only probe into the application; never accept agent-supplied shell text here.
    let source = include_str!("../../resources/ssh/metrics.py");
    let bootstrap=include_str!("../../resources/ssh/user_runtime.sh");
    let command = format!("{bootstrap}\nomaax_find_python || exit 1\n\"$OMAAX_PYTHON\" - <<'BIODSH_METRICS'\n{}\nBIODSH_METRICS", source);
    let result = call(root, node, script, id, "execute", &command, 20, false)?;
    if result["exitCode"].as_i64() != Some(0) { return Err("资源查询失败，请检查服务器 Python 3 环境".into()); }
    serde_json::from_str(result["stdout"].as_str().unwrap_or("")).map_err(|_| "服务器返回的资源数据无效".into())
}

pub fn files(root:&Path,node:&Path,script:&Path,id:&str,body:Value)->Result<Value,String>{
    let object=body.as_object().ok_or("目录查询参数不正确")?;
    if object.keys().any(|k| !["path","offset","limit","showHidden"].contains(&k.as_str())){return Err("目录查询参数不正确".into());}
    let mut request=connection(root,id)?;
    for (key,value) in object {request[key]=value.clone();}
    request["operation"]=json!("files");request["timeoutSeconds"]=json!(30);
    run_request(node,script,request)
}

pub fn run_mcp() {
    let root = PathBuf::from(std::env::var_os("BIODSH_HOME").unwrap_or_default());
    let node = PathBuf::from(std::env::var_os("BIODSH_SSH_NODE").unwrap_or_default());
    let script = PathBuf::from(std::env::var_os("BIODSH_SSH_SCRIPT").unwrap_or_default());
    for line in std::io::stdin().lock().lines().map_while(Result::ok) {
        if line.len() > 8*1024*1024 { break; }
        let Ok(request) = serde_json::from_str::<Value>(&line) else { continue };
        let Some(id) = request.get("id") else { continue };
        if request["method"] == "tools/call" {
            let authorization=crate::hub_auth::require(&root,"");
            if let Ok(user)=authorization.as_ref(){crate::hub_auth::audit(&root,user,request["params"]["name"].as_str().unwrap_or("tool"));}
            if let Err(error)=authorization {
                println!("{}",json!({"jsonrpc":"2.0","id":id,"error":{"code":-32001,"message":error}})); let _=std::io::stdout().flush(); continue;
            }
        }
        let result: Result<Value, String> = match request["method"].as_str().unwrap_or("") {
            "initialize" => Ok(json!({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "OMaaX SSH", "version": "1.0.0"}})),
            "ping" => Ok(json!({})),
            "tools/list" => Ok(mcp_tools()),
            "tools/call" => {
                let params = &request["params"];
                let value = match params["name"].as_str().unwrap_or("") {
                    "server_environment" => repair_environment(&root,&node,&script,&params["arguments"]),
                    "workflow_catalog" => crate::personal_compute::tool_catalog(&params["arguments"]),
                    "protected_paths" | "server_delete" | "server_write" | "server_copy" | "server_files" | "workspace_files" | "workspace_config" | "workspace_restore" | "workspace_index" | "workspace_script" | "workspace_analysis" | "workflow_prepare" | "workflow_prepare_status" | "workflow_readiness" | "workflow_environment" | "workflow_inspect" | "workflow_plan" | "workflow_plans" | "workflow_result" | "workflow_resume" | "workflow_confirm" => {
                        let mut args=params["arguments"].clone();
                        let server=args["serverId"].as_str().unwrap_or("").to_string();
                        let session=args["_sessionId"].as_str().map(String::from);
                        if let Some(object)=args.as_object_mut(){
                            object.remove("serverId");
                            if let Some(sid)=object.remove("_sessionId").and_then(|v|v.as_str().map(String::from)) {
                                match crate::personal_compute::session_context(&root.join("dsh-home"),Some(&sid)) {
                                    Ok(context)=>{object.insert("context".into(),context);},Err(error)=>{object.insert("context".into(),json!({"error":error}));}
                                }
                            }
                        }
                        let result=crate::personal_compute::request(&root,&node,&script,&server,params["name"].as_str().unwrap(),args,true);
                        if result.is_ok(){if let Some(sid)=session {let _=crate::personal_compute::remember_server(&root.join("dsh-home"),&sid,&server);}}
                        result
                    },
                    "compute_status" | "task_list" | "task_logs" | "cancel_task" | "server_audit" => {
                        let action=match params["name"].as_str().unwrap_or(""){"compute_status"=>"status","task_list"=>"jobs","task_logs"=>"logs","server_audit"=>"audit",_=>"cancel"};
                        crate::personal_compute::request(&root,&node,&script,params["arguments"]["serverId"].as_str().unwrap_or(""),action,params["arguments"].clone(),true)
                    },
                    "list_servers" => list(&root).map(|hosts| json!(hosts.into_iter().filter(|h| h.selected).collect::<Vec<_>>())),
                    "get_resources" => metrics(&root, &node, &script, params["arguments"]["serverId"].as_str().unwrap_or(""), true),
                    "execute" => execute_tool(&root,&node,&script,&params["arguments"]),
                    _ => Err("Unknown tool".into()),
                };
                Ok(match value { Ok(value) => json!({"content":[{"type":"text","text":value.to_string()}]}), Err(error) => json!({"isError":true,"content":[{"type":"text","text":error}]}) })
            },
            _ => Err("Unknown method".into()),
        };
        let response = match result { Ok(value) => json!({"jsonrpc":"2.0","id":id,"result":value}), Err(error) => json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":error}}) };
        println!("{}", response); let _ = std::io::stdout().flush();
    }
}

fn mcp_tools()->Value {
    json!({"tools": [
                {"name":"execute","description":"Run a command or custom script directly on a selected server with its saved account. No remote Python or Conda is required to start this tool. Use it for environment installation/repair, input checks, parameter preparation and custom tasks. Read protected_paths and never modify registered original-data paths, including through scripts; use server_write/server_copy/server_delete for software-checked file operations. Formal analysis must use the selected built-in workflow through workflow_plan. Commands and outcomes are audited. Inspect exit code and outputs before reporting success; after connection loss check whether work already ran before retrying. timeoutSeconds 0 waits without an execution deadline; otherwise choose the required wait in seconds.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"command":{"type":"string"},"description":{"type":"string","description":"Short purpose, without credentials"},"timeoutSeconds":{"type":"integer","minimum":0}},"required":["serverId","command"],"additionalProperties":false}},
                {"name":"server_environment","description":"Inspect or repair server software directly through SSH, even when Python or Conda is broken. The model may provide any command for diagnosis, installation, package configuration or a custom repair script; command takes precedence over the built-in actions. Without command, inspect/repair/status provide the optional bundled runtime helper, with configurable Python and channel. Registered protected data must not be modified. Commands and outcomes are audited. Formal analysis continues through workflow_plan. Check real exit codes/logs and explain unresolved prerequisites. timeoutSeconds 0 waits without an execution deadline.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"action":{"enum":["inspect","repair","status","execute"]},"command":{"type":"string"},"description":{"type":"string"},"pythonVersion":{"type":"string"},"channel":{"type":"string"},"timeoutSeconds":{"type":"integer","minimum":0}},"required":["serverId"],"additionalProperties":false}},
                {"name":"workflow_catalog","description":"List fixed workflows or obtain declared parameters for one workflow/step. Source software and step order are fixed; scientific parameter choices belong to the user. No biological approval checkpoints. Query workflow and step for detailed editable parameters.","inputSchema":{"type":"object","properties":{"workflow":{"enum":["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"]},"step":{"type":"string"}},"additionalProperties":false}},
                {"name":"workflow_inspect","description":"Read only file names, sizes and text-table headers in a user-provided absolute input folder or registered source. Other account-readable reference paths can be inspected with server_files. Ask the user for sample groups or missing parameters; do not infer biological design.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"source":{"type":"string"},"input":{"type":"string"}},"required":["serverId","input"],"additionalProperties":false}},
                {"name":"workflow_readiness","description":"Inspect required software and missing dependencies for selected steps before preparing or repairing the environment.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"},"steps":{"type":"array","items":{"type":"string"}},"parameters":{"type":"object"},"configs":{"type":"object"}},"required":["serverId","workflow"],"additionalProperties":false}},
                {"name":"workflow_prepare","description":"Prepare or repair selected step environments. Model controls per-step recipes: packages (Conda specs), channels and script (shell executed in a new user-owned Conda environment; preserve input files and use server_delete for all intentional deletions). Omit recipes to use declared dependencies. Poll workflow_prepare_status; after failure revise recipes and retry. Existing environments are preserved until validation succeeds.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"},"steps":{"type":"array","items":{"type":"string"}},"parameters":{"type":"object"},"configs":{"type":"object"},"recipes":{"type":"object","additionalProperties":{"type":"object","properties":{"packages":{"type":"array","items":{"type":"string"}},"channels":{"type":"array","items":{"type":"string"}},"script":{"type":"string"}},"additionalProperties":false}}},"required":["serverId","workflow","steps"],"additionalProperties":false}},
                {"name":"workflow_prepare_status","description":"Read environment installation progress and logs; diagnose failures, revise recipe and retry without asking user to configure software.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"}},"required":["serverId","workflow"],"additionalProperties":false}},
                {"name":"workflow_environment","description":"Register an existing immutable local image for a workflow or individual step; readiness must still pass.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"},"image":{"type":"string"},"step":{"type":"string"}},"required":["serverId","workflow","image"],"additionalProperties":false}},
                {"name":"workflow_plan","description":"Prepare any of the fixed pipelines. First collect the user-provided server, absolute data folder (or optional registered source and relative folder), selected stages and parameter choices. Parameters must follow workflow_catalog. Following a real user execution request, the native app validates and automatically submits this plan. Do not call this tool for plan-only discussions or explicit do-not-run requests; use workflow_catalog and workflow_inspect instead. No arbitrary command, image, script, environment or Nextflow config is allowed. Do not impose extra biological audit gates or independent-replicate minimums. The source software handles its own numerical requirements.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"enum":["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"]},"source":{"type":"string"},"input":{"type":"string","description":"Absolute path on the server (recommended), or relative data folder within source; dot means the chosen source folder."},"steps":{"type":"array","items":{"type":"string"}},"prefix":{"type":"string"},"parameters":{"type":"object","description":"Objects keyed by selected stage ID; only the CLI-declared typed fields."},"preferences":{"type":"object","properties":{"library_strandness":{"enum":["","non_stranded","forward","reverse"]}},"additionalProperties":false},"configs":{"type":"object","description":"Objects keyed by selected omics stage ID; use catalog defaults and user choices, paths may be absolute account-readable paths, relative to chosen input or @stage:artifact."},"cpu":{"type":"integer"},"memoryMb":{"type":"integer"},"timeoutSeconds":{"type":"integer"}},"required":["serverId","workflow","input"],"additionalProperties":false}},
                {"name":"workflow_plans","description":"List saved fixed-workflow plans and actual execution states on a selected server. Execution requests are submitted automatically after validation. Read the actual task state and outputs; exit zero alone does not prove successful results.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"],"additionalProperties":false}},
                {"name":"workflow_confirm","description":"Submit a previously prepared fixed-workflow plan after the user has requested execution in this conversation. The application checks that existing intent automatically; no additional confirmation click. Use the exact id and digest returned by workflow_plan/workflow_plans.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"},"digest":{"type":"string"}},"required":["serverId","id","digest"],"additionalProperties":false}},
                {"name":"workflow_resume","description":"Continue a previously interrupted fixed-workflow plan after the user requested execution. The application checks recorded conversation intent automatically and preserves completed outputs; no additional confirmation click. Use the exact id and digest returned by workflow_plans.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"},"digest":{"type":"string"}},"required":["serverId","id","digest"],"additionalProperties":false}},
                {"name":"workflow_result","description":"Read the actual executed steps, user parameters and result files of a fixed pipeline. Optionally provide file from the returned file list to read up to 64 KiB of actual text results for interpretation. No arbitrary paths.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"},"file":{"type":"string"}},"required":["serverId","id"],"additionalProperties":false}},
                {"name":"protected_paths","description":"Read the user-managed original-data protection list. Paths and their contents cannot be modified, overwritten, moved or deleted. Reading, copying out and links outside are allowed. Only the desktop user can add or remove protection entries.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"],"additionalProperties":false}},
                {"name":"server_delete","description":"Delete account-writable files or directories outside registered original-data paths, moving them into recoverable trash immediately with an audit record. Protected paths and their ancestors cannot be deleted. No extra approval is required.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"recursive":{"type":"boolean"}},"required":["serverId","path"],"additionalProperties":false}},
                {"name":"server_write","description":"Write a new text or structured config file at any absolute server path writable by the SSH account. Directory creation is automatic. Registered original-data paths cannot be modified. Outside protected paths, existing files are backed up and replaced without another approval. No deletion or arbitrary host commands. Project/session filing is automatic.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"content":{}},"required":["serverId","path","content"],"additionalProperties":false}},
                {"name":"server_copy","description":"Copy an account-readable file or directory, including binary results or indexes, to any writable absolute server path. Nested links are preserved. Does not delete or modify the source. Registered protected destinations cannot be written; other existing destinations are backed up and replaced without another approval. Operations are audited and project/session filing is automatic.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"source":{"type":"string"},"path":{"type":"string"}},"required":["serverId","source","path"],"additionalProperties":false}},
                {"name":"server_files","description":"Read account-accessible server files: list directories, stat or read text in chunks. Reading never changes the source.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"operation":{"enum":["list","read","stat"]},"offset":{"type":"integer"}},"required":["serverId","path"],"additionalProperties":false}},
                {"name":"workspace_files","description":"Browse and organize the application workspace with list/read/stat, mkdir, move, rename, search, delete and trash. Relative paths refer to the workspace. Registered original-data paths remain protected; deletions are recoverable and audited.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"operation":{"enum":["info","contexts","external","list","read","stat","mkdir","move","rename","search","delete","trash"]},"path":{"type":"string"},"offset":{"type":"integer"},"destination":{"type":"string"},"query":{"type":"string"}},"required":["serverId","operation"],"additionalProperties":false}},
                {"name":"workspace_config","description":"Write a sample/config table at a workspace-relative or absolute server path outside registered original-data protection. YAML/JSON uses objects/lists, CSV/TSV uses rows with columns. Existing files are backed up before replacement. Returned absolute paths work with fixed workflows.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"format":{"enum":["yaml","json","csv","tsv"]},"content":{},"columns":{"type":"array","items":{"type":"string"}}},"required":["serverId","path","format","content"],"additionalProperties":false}},
                {"name":"workspace_restore","description":"Restore a deleted application-workspace entry by trashId. Never overwrite another file.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"trashId":{"type":"string"}},"required":["serverId","trashId"],"additionalProperties":false}},
                {"name":"workspace_index","description":"Build a reference index as a logged task, preserving FASTA/GTF inputs and writing a separate result. Uses fixed index recipes and already prepared approved workflow environment; no host commands. Returns job and generated index path. Check task_list/task_logs for completion before using the index.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"kind":{"enum":["hisat2","salmon","star","bowtie2","bwa"]},"fasta":{"type":"string"},"gtf":{"type":"string"},"cpu":{"type":"integer"},"memoryMb":{"type":"integer"},"timeoutSeconds":{"type":"integer"}},"required":["serverId","kind","fasta"],"additionalProperties":false}},
                {"name":"workspace_script","description":"Save or update a custom server script at a workspace-relative or absolute path outside registered original-data protection. Existing files are backed up before replacement. Execute directly with execute or as a logged durable task with workspace_analysis. Formal analyses continue through the selected fixed source and workflow_plan.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"content":{"type":"string"}},"required":["serverId","path","content"],"additionalProperties":false}},
                {"name":"workspace_analysis","description":"Run a custom script as a durable, logged task using a prepared Conda environment or the host tools. No Conda environment is required for image host:direct; when image is omitted the host is used if no prepared environment exists. Pass input paths and argv. Write results to /output for automatic project filing; other account-writable locations are also available. Never modify registered protected data. For software installation or repair use server_environment or execute. Formal analyses continue through workflow_plan. Inspect task_list/task_logs for actual completion.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"enum":["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"]},"step":{"type":"string"},"inputs":{"type":"array","items":{"type":"string"}},"argv":{"type":"array","items":{"type":"string"}},"cpu":{"type":"integer"},"memoryMb":{"type":"integer"},"timeoutSeconds":{"type":"integer"},"image":{"type":"string","description":"Optional prepared environment identifier; host:direct uses host tools without Conda."}},"required":["serverId","inputs","argv"],"additionalProperties":false}},
                {"name":"compute_status","description":"Inspect this SSH user's Conda readiness, registered environments and input data sources.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"]}},
                {"name":"task_list","description":"List this SSH account's tasks, including tasks continuing after desktop disconnect.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"]}},
                {"name":"task_logs","description":"Read task logs. Defaults to the latest 64 KiB; returned segments and nextOffset allow reading complete archived logs without rerunning the task.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"},"segment":{"type":"string"},"offset":{"type":"integer","minimum":0}},"required":["serverId","id"]}},
                {"name":"server_audit","description":"Read the server file-operation and task audit history. Direct server commands also have a local account log in logs/server-commands.jsonl with conversation ID, command/result summaries and hashes.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"],"additionalProperties":false}},
                {"name":"cancel_task","description":"Request cancellation of this account's task; preserves original data and partial outputs.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"}},"required":["serverId","id"]}},
                {"name":"list_servers","description":"List only SSH servers explicitly selected by the user. Never request or expose credentials.","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},
                {"name":"get_resources","description":"Read CPU, memory, disk and GPU metrics on the selected server. For environment installation/repair use server_environment or execute, even if remote Python or Conda cannot start. Use workflow_prepare for workflow-specific recipes and workflow_plan for formal analysis.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"],"additionalProperties":false}}
    ]})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frozen_workflow_payload_can_exceed_old_command_limit() {
        assert!(validate_command(&"x".repeat(500000)).is_ok());
        assert!(validate_command(&"x".repeat(6 * 1024 * 1024 + 1)).is_err());
        assert!(validate_command("bad\0payload").is_err());
    }
    #[test]
    fn file_browser_rejects_commands_before_reading_credentials(){
        let error=files(Path::new("missing"),Path::new("missing"),Path::new("missing"),"none",json!({"command":"rm -rf anything"})).unwrap_err();
        assert!(error.contains("目录查询参数不正确"));
    }
    #[cfg(windows)]
    #[test]
    fn new_profile_credentials_trust_selection_and_edit_survive_roundtrips(){
        let root=std::env::temp_dir().join(format!("omaax-onboarding-{}",uuid::Uuid::new_v4()));
        let mut rows=save(&root,Draft{id:None,name:"fresh server".into(),host:"127.0.0.1".into(),port:22,username:"test".into(),auth:"password".into(),password:Some("test-private-password".into()),private_key:None,passphrase:None}).unwrap();
        let id=rows.remove(0).id;
        assert!(select(&root,&id,true).is_err());assert_eq!(secret(&id).unwrap().password,"test-private-password");
        trust(&root,&id,&"a".repeat(40),"SHA256:test").unwrap();select(&root,&id,true).unwrap();
        save(&root,Draft{id:Some(id.clone()),name:"renamed".into(),host:"127.0.0.1".into(),port:22,username:"test".into(),auth:"password".into(),password:None,private_key:None,passphrase:None}).unwrap();
        assert!(list(&root).unwrap()[0].selected);assert_eq!(secret(&id).unwrap().password,"test-private-password");
        assert!(!fs::read_to_string(root.join("ssh/hosts.json")).unwrap().contains("test-private-password"));
        remove(&root,&id).unwrap();assert!(secret(&id).is_err());fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn agent_cannot_connect_to_an_unselected_host() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        persist(&root, &[Host { worker_node_id:None,id:"test".into(),name:"test".into(),host:"127.0.0.1".into(),port:22,username:"test".into(),auth:"password".into(),selected:false,fingerprint:None,host_key:None }]).unwrap();
        assert!(call(&root, Path::new("missing"), Path::new("missing"), "test", "execute", "whoami", 5, true).unwrap_err().contains("未被用户选择"));
        assert!(select(&root,"test",true).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn selected_host_requires_verified_identity_before_execution() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        persist(&root, &[Host { worker_node_id:None,id:"test".into(),name:"test".into(),host:"127.0.0.1".into(),port:22,username:"test".into(),auth:"password".into(),selected:true,fingerprint:None,host_key:None }]).unwrap();
        // Opening model execution must not remove server identity verification.
        let error = call(&root, Path::new("missing"), Path::new("missing"), "test", "execute", "python3 -c 'pass'", 5, true).unwrap_err();
        assert!(error.contains("确认主机指纹"));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn command_audit_redacts_credentials_and_records_denied_attempts(){
        let text="echo work\nexport API_KEY='sensitive-key'\necho secret-password\n";
        let safe=audit_text(text,&["secret-password".into()],65536);
        assert!(safe.contains("echo work"));assert!(!safe.contains("sensitive-key"));assert!(!safe.contains("secret-password"));
        assert_eq!(audit_text("-----BEGIN PRIVATE KEY-----\nkey-data\n-----END PRIVATE KEY-----",&[],65536),"[已隐藏私钥内容]");
        assert!(audit_text(&"字".repeat(20),&[],7).contains("已截断"));
        let root=std::env::temp_dir().join(format!("ssh-audit-{}",uuid::Uuid::new_v4()));
        assert!(execute_tool(&root,Path::new("missing"),Path::new("missing"),&json!({"serverId":"unknown","command":"echo denied","_sessionId":"real-session"})).is_err());
        let records=fs::read_to_string(root.join("logs/server-commands.jsonl")).unwrap();
        let events:Vec<Value>=records.lines().map(|line|serde_json::from_str(line).unwrap()).collect();
        assert_eq!(events[0]["sessionId"],"real-session");assert_eq!(events[0]["commandSha256"],digest("echo denied"));assert_eq!(events[1]["event"],"failed");
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(windows)]
    #[test]
    fn native_model_execute_and_custom_environment_repair_reach_transport(){
        let root=std::env::temp_dir().join(format!("ssh-autonomy-{}",uuid::Uuid::new_v4()));
        let mut hosts=save(&root,Draft{id:None,name:"transport test".into(),host:"127.0.0.1".into(),port:22,username:"test".into(),auth:"password".into(),password:Some("audit-private-password".into()),private_key:None,passphrase:None}).unwrap();
        let id=hosts.remove(0).id;
        trust(&root,&id,&"a".repeat(40),"SHA256:test").unwrap();select(&root,&id,true).unwrap();
        let script=root.join("transport.cjs");
        fs::write(&script,"let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{const r=JSON.parse(s);process.stdout.write(JSON.stringify({exitCode:0,stdout:r.command,stderr:r.password,timeoutSeconds:r.timeoutSeconds}));});").unwrap();
        let node=Path::new(env!("CARGO_MANIFEST_DIR")).join("../resources/node/win32-x64/node.exe");
        let args=json!({"serverId":id,"command":"printf custom-command","timeoutSeconds":3601,"_sessionId":"test-session"});
        let result=execute_tool(&root,&node,&script,&args).unwrap();
        assert_eq!(result["stdout"],args["command"]);assert_eq!(result["timeoutSeconds"],3601);
        let mut repair=args;repair["command"]=json!("printf custom-repair");repair["action"]=json!("execute");repair["timeoutSeconds"]=json!(0);
        let result=repair_environment(&root,&node,&script,&repair).unwrap();
        assert_eq!(result["stdout"],"printf custom-repair");assert_eq!(result["timeoutSeconds"],0);
        let log=fs::read_to_string(root.join("logs/server-commands.jsonl")).unwrap();
        assert!(log.contains("printf custom-repair"));assert!(log.contains("test-session"));assert!(!log.contains("audit-private-password"));
        let schema=mcp_tools();
        for tool in ["execute","server_environment","workflow_confirm","workflow_resume"] {assert!(schema["tools"].as_array().unwrap().iter().any(|item|item["name"]==tool));}
        remove(&root,&id).unwrap();fs::remove_dir_all(root).unwrap();
    }
    #[test]
    #[ignore = "Explicit opt-in: native model command entry on the selected 175 server using isolated audit files"]
    fn live_175_native_model_command(){
        let hosts_file=PathBuf::from(std::env::var("OMAAX_LIVE_SSH_HOSTS").expect("host metadata JSON"));
        let root=PathBuf::from(std::env::var("OMAAX_LIVE_SSH_TEST_ROOT").expect("isolated audit root"));
        assert!(root.file_name().unwrap().to_string_lossy().starts_with("ssh-autonomy-"));
        let hosts:Vec<Host>=serde_json::from_slice(&fs::read(hosts_file).unwrap()).unwrap();
        let host=hosts.into_iter().find(|h|h.host=="192.168.0.175"&&h.selected&&h.host_key.is_some()).expect("selected trusted 175");
        persist(&root,&[host.clone()]).unwrap();
        let request:Value=serde_json::from_slice(&fs::read(std::env::var("OMAAX_LIVE_SSH_REQUEST").expect("reviewed test request")).unwrap()).unwrap();
        let mut args=request["arguments"].clone();args["serverId"]=json!(host.id);
        let resources=Path::new(env!("CARGO_MANIFEST_DIR")).join("../resources");
        let node=resources.join("node/win32-x64/node.exe");let script=resources.join("ssh/client.cjs");
        let result=match request["name"].as_str().unwrap(){"execute"=>execute_tool(&root,&node,&script,&args),"server_environment"=>repair_environment(&root,&node,&script,&args),_=>panic!("Unexpected test route")};
        let output=match result{Ok(result)=>json!({"result":result}),Err(error)=>json!({"error":error})};
        fs::write(root.join("response.json"),serde_json::to_vec_pretty(&output).unwrap()).unwrap();
        assert!(output.get("error").is_none(),"native command failed; inspect isolated response.json");
        assert_eq!(output["result"]["exitCode"],0);
    }
    #[cfg(windows)]
    #[test]
    fn large_credentials_roundtrip_in_system_store() {
        let id = uuid::Uuid::new_v4().to_string();
        let value = Secret { password: "测试密码".repeat(400), private_key: "private-key-test-data".repeat(400), passphrase:"test".into() };
        save_secret(&id, &value).unwrap();
        let loaded = secret(&id).unwrap();
        let item = secret_entry(&id).unwrap();
        delete_parts(&id, &item.get_password().unwrap()); let _ = item.delete_credential();
        assert_eq!(loaded.password, value.password); assert_eq!(loaded.private_key, value.private_key);
    }
}

pub fn bind(root: &Path, id: &str, node_id: Option<String>) -> Result<Vec<Host>, String> {
    let _guard = WRITE_LOCK.lock().map_err(|_| "保存锁不可用")?;
    let mut hosts = list(root)?;
    let host = hosts.iter_mut().find(|h| h.id == id).ok_or("服务器不存在")?;
    host.worker_node_id = node_id; persist(root, &hosts)?; Ok(hosts)
}


#[cfg(test)]
mod environment_repair_tests {
    use super::*;
    #[test]
    fn bootstrap_does_not_load_workflow_runner() {
        let command=environment_command("repair").unwrap();
        assert!(command.contains("omaax_install_runtime analysis"));
        assert!(!command.contains("OMAAX_REQUEST"));
        assert!(command.contains("repair-XXXXXXXX.log"));
        assert!(environment_command("repair; rm -rf x").is_err());
        assert!(environment_options(&json!({"channel":"https://x/' ; echo sample"})).unwrap().contains("'\"'\"'"));
        assert!(environment_options(&json!({"pythonVersion":"3.10","channel":"https://mirror.example/conda-forge"})).unwrap().contains("3.10"));
        assert!(environment_options(&json!({"pythonVersion":"3.13","channel":"bioconda"})).unwrap().contains("'3.13'"));
        assert_eq!(environment_request(&json!({"action":"execute","command":"/bin/sh -c 'printf repair'"})).unwrap(),"/bin/sh -c 'printf repair'");
        assert!(environment_request(&json!({"action":"execute"})).is_err());
    }
    #[test]
    fn bootstrap_checks_selection_before_credentials() {
        let temp=std::env::temp_dir().join(format!("ssh-repair-{}",uuid::Uuid::new_v4()));
        assert!(repair_environment(&temp,Path::new("missing"),Path::new("missing"),&json!({"serverId":"unknown","action":"repair"})).unwrap_err().contains("未被用户选择"));
    }
}
