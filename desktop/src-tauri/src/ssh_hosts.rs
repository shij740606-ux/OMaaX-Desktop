use keyring::Entry;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs, io::{BufRead, Read, Write}, path::{Path, PathBuf}, process::{Command, Stdio}, sync::Mutex};

static WRITE_LOCK: Mutex<()> = Mutex::new(());

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
    let host = list(root)?.into_iter().find(|h| h.id == id).ok_or("服务器不存在")?;
    if agent && !host.selected { return Err("该服务器未被用户选择，禁止连接".into()); }
    // Enforce before reading credentials or starting any network process. Prompt rules are not a boundary.
    if agent { return Err("数据保护：智能体通用 SSH 命令已禁用。请使用资源查询或平台隔离计算任务；不能回退到其他终端执行。".into()); }
    if operation != "probe" && host.host_key.is_none() { return Err("请在服务器管理页面确认主机指纹".into()); }
    validate_command(command)?;
    let credentials = if operation == "probe" { Secret::default() } else { secret(id)? };
    let request = json!({"operation": operation, "host": host.host, "port": host.port, "username": host.username,
        "hostKey": host.host_key, "password": if host.auth == "password" { Some(credentials.password) } else { None },
        "privateKey": if host.auth == "key" { Some(credentials.private_key) } else { None },
        "passphrase": credentials.passphrase, "command": command, "timeoutSeconds": timeout.clamp(5, 900)});
    run_request(node, script, request)
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
    if !matches!(python,"3.10"|"3.11"|"3.12") || channel.is_empty() || channel.len()>512
        || !channel.chars().all(|c|c.is_ascii_alphanumeric() || "-_./:".contains(c))
        || !(channel=="conda-forge" || channel.starts_with("https://")) {
        return Err("请使用支持的 Python 版本和 HTTPS Conda 软件源".into());
    }
    Ok(format!("export OMAAX_BOOTSTRAP_PYTHON='{python}' OMAAX_BOOTSTRAP_CHANNEL='{channel}'\n"))
}
fn repair_environment(root: &Path, node: &Path, script: &Path, args: &Value) -> Result<Value, String> {
    let id=args["serverId"].as_str().ok_or("缺少服务器编号")?;
    if !list(root)?.iter().any(|host| host.id==id && host.selected) { return Err("该服务器未被用户选择，禁止连接".into()); }
    let command=environment_options(args)?+&environment_command(args["action"].as_str().unwrap_or("inspect"))?;
    let result=call(root,node,script,id,"execute",&command,900,false)?;
    if result["exitCode"].as_i64()!=Some(0) {
        return Err(format!("SSH 环境修复未完成，原有数据保留。请根据日志继续诊断：{} {}",result["stdout"].as_str().unwrap_or(""),result["stderr"].as_str().unwrap_or("")));
    }
    Ok(result)
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
    let deadline=std::time::Instant::now()+std::time::Duration::from_secs(request["timeoutSeconds"].as_u64().unwrap_or(30).clamp(5,if request["kind"]=="transfer"{43200}else{900})+15);
    loop {
        if child.try_wait().map_err(|e|e.to_string())?.is_some(){break;}
        if std::time::Instant::now()>=deadline{let _=child.kill();let _=child.wait();let _=writer.join();let _=reader.join();return Err("SSH 操作超时，已释放本地连接。远端任务可能已提交，请刷新核对，勿重复提交".into());}
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
        if line.len() > 2*1024*1024 { break; }
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
            "tools/list" => Ok(json!({"tools": [
                {"name":"server_environment","description":"Inspect or repair the user-owned runtime directly over SSH, without loading any remote Python or Conda first. DSH chooses pythonVersion and channel to match server compatibility and network. Use inspect for OS, architecture and bootstrap dependencies; use repair when Python/Conda cannot start (including GLIBC errors). Repair installs an independent user runtime, preserves existing Conda/data and returns a persistent log. Use status to retrieve logs after errors or disconnection. Then use workflow_prepare recipes for scientific packages/channels/scripts, server_files for inputs and workflow_plan for declared parameters. No sudo or Podman required. If repair fails, explain the actual log and missing network/system prerequisites; do not claim success.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"action":{"enum":["inspect","repair","status"]},"pythonVersion":{"enum":["3.10","3.11","3.12"]},"channel":{"type":"string","description":"conda-forge or an HTTPS Conda channel mirror"}},"required":["serverId","action"],"additionalProperties":false}},
                {"name":"workflow_catalog","description":"List fixed workflows or obtain declared parameters for one workflow/step. Source software and step order are fixed; scientific parameter choices belong to the user. No biological approval checkpoints. Query workflow and step for detailed editable parameters.","inputSchema":{"type":"object","properties":{"workflow":{"enum":["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"]},"step":{"type":"string"}},"additionalProperties":false}},
                {"name":"workflow_inspect","description":"Read only file names, sizes and text-table headers in a user-provided absolute input folder or registered source. Other account-readable reference paths can be inspected with server_files. Ask the user for sample groups or missing parameters; do not infer biological design.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"source":{"type":"string"},"input":{"type":"string"}},"required":["serverId","input"],"additionalProperties":false}},
                {"name":"workflow_readiness","description":"Inspect required software and missing dependencies for selected steps before preparing or repairing the environment.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"},"steps":{"type":"array","items":{"type":"string"}},"parameters":{"type":"object"},"configs":{"type":"object"}},"required":["serverId","workflow"],"additionalProperties":false}},
                {"name":"workflow_prepare","description":"Prepare or repair selected step environments. Model controls per-step recipes: packages (Conda specs), channels and script (shell executed in a new user-owned Conda environment; preserve input files and use server_delete for all intentional deletions). Omit recipes to use declared dependencies. Poll workflow_prepare_status; after failure revise recipes and retry. Existing environments are preserved until validation succeeds.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"},"steps":{"type":"array","items":{"type":"string"}},"parameters":{"type":"object"},"configs":{"type":"object"},"recipes":{"type":"object","additionalProperties":{"type":"object","properties":{"packages":{"type":"array","items":{"type":"string"}},"channels":{"type":"array","items":{"type":"string"}},"script":{"type":"string"}},"additionalProperties":false}}},"required":["serverId","workflow","steps"],"additionalProperties":false}},
                {"name":"workflow_prepare_status","description":"Read environment installation progress and logs; diagnose failures, revise recipe and retry without asking user to configure software.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"}},"required":["serverId","workflow"],"additionalProperties":false}},
                {"name":"workflow_environment","description":"Register an existing immutable local image for a workflow or individual step; readiness must still pass.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"type":"string"},"image":{"type":"string"},"step":{"type":"string"}},"required":["serverId","workflow","image"],"additionalProperties":false}},
                {"name":"workflow_plan","description":"Prepare any of the fixed pipelines. First collect the user-provided server, absolute data folder (or optional registered source and relative folder), selected stages and parameter choices. Parameters must follow workflow_catalog. Following a real user execution request, the native app validates and automatically submits this plan. Do not call this tool for plan-only discussions or explicit do-not-run requests; use workflow_catalog and workflow_inspect instead. No arbitrary command, image, script, environment or Nextflow config is allowed. Do not impose extra biological audit gates or independent-replicate minimums. The source software handles its own numerical requirements.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"enum":["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"]},"source":{"type":"string"},"input":{"type":"string","description":"Absolute path on the server (recommended), or relative data folder within source; dot means the chosen source folder."},"steps":{"type":"array","items":{"type":"string"}},"prefix":{"type":"string"},"parameters":{"type":"object","description":"Objects keyed by selected stage ID; only the CLI-declared typed fields."},"preferences":{"type":"object","properties":{"library_strandness":{"enum":["","non_stranded","forward","reverse"]}},"additionalProperties":false},"configs":{"type":"object","description":"Objects keyed by selected omics stage ID; use catalog defaults and user choices, paths may be absolute account-readable paths, relative to chosen input or @stage:artifact."},"cpu":{"type":"integer"},"memoryMb":{"type":"integer"},"timeoutSeconds":{"type":"integer"}},"required":["serverId","workflow","input"],"additionalProperties":false}},
                {"name":"workflow_plans","description":"List saved fixed-workflow plans and actual execution states on a selected server. Execution requests are submitted automatically after validation. Read the actual task state and outputs; exit zero alone does not prove successful results.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"],"additionalProperties":false}},
                {"name":"workflow_result","description":"Read the actual executed steps, user parameters and result files of a fixed pipeline. Optionally provide file from the returned file list to read up to 64 KiB of actual text results for interpretation. No arbitrary paths.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"},"file":{"type":"string"}},"required":["serverId","id"],"additionalProperties":false}},
                {"name":"server_delete","description":"Delete only files explicitly requested by the user at account-permitted absolute paths. Moves to recoverable trash. Every deletion, including one small file, returns a pending preview. Explain exact paths, file/directory count, recursive scope and byte size in chat; wait for the user to confirm the deletion card. Never claim a pending request was executed; model cannot approve. No shell deletion or credential/control files.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"recursive":{"type":"boolean"}},"required":["serverId","path"],"additionalProperties":false}},
                {"name":"server_write","description":"Write a new text or structured config file at any absolute server path writable by the SSH account. Directory creation is automatic. Existing files are not overwritten: a pending request is returned for explicit desktop user approval, with backup before replacement. No deletion or arbitrary host commands. Project/session filing is automatic.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"content":{}},"required":["serverId","path","content"],"additionalProperties":false}},
                {"name":"server_copy","description":"Copy an account-readable file, including binary analysis results or generated indexes, to any writable absolute server path. Does not delete or modify source. Existing destination files require explicit desktop user approval and backup. Project/session provenance is automatic.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"source":{"type":"string"},"path":{"type":"string"}},"required":["serverId","source","path"],"additionalProperties":false}},
                {"name":"server_files","description":"Read account-permitted server paths supplied by the user, without modifying originals. List directories, read text up to 1 MB, or stat. Data and reference files may be anywhere readable by this SSH account. Credential and private control files are excluded.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"operation":{"enum":["list","read","stat"]},"offset":{"type":"integer"}},"required":["serverId","path"],"additionalProperties":false}},
                {"name":"workspace_files","description":"Get info about the OMaaX managed workspace, list/read/stat files, mkdir or delete an entry. Paths are relative to workspace. Generated configs, references, files and results belong here. Delete moves to recoverable trash; external paths and active task inputs/outputs cannot be deleted.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"operation":{"enum":["info","contexts","external","list","read","stat","mkdir","move","rename","search","delete","trash"]},"path":{"type":"string"},"offset":{"type":"integer"},"destination":{"type":"string"},"query":{"type":"string"}},"required":["serverId","operation"],"additionalProperties":false}},
                {"name":"workspace_config","description":"Create a new sample/config table inside configs/ or files/. Never overwrite originals. YAML/JSON content is a structured object/list; CSV/TSV content is rows with columns. Use user-supplied groups; fill file paths from server_files. Returned absolute path can be passed to every fixed workflow.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"format":{"enum":["yaml","json","csv","tsv"]},"content":{},"columns":{"type":"array","items":{"type":"string"}}},"required":["serverId","path","format","content"],"additionalProperties":false}},
                {"name":"workspace_restore","description":"Restore a deleted application-workspace entry by trashId. Never overwrite another file.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"trashId":{"type":"string"}},"required":["serverId","trashId"],"additionalProperties":false}},
                {"name":"workspace_index","description":"Build a reference index as a logged task, preserving FASTA/GTF inputs and writing a separate result. Uses fixed index recipes and already prepared approved workflow environment; no host commands. Returns job and generated index path. Check task_list/task_logs for completion before using the index.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"kind":{"enum":["hisat2","salmon","star","bowtie2","bwa"]},"fasta":{"type":"string"},"gtf":{"type":"string"},"cpu":{"type":"integer"},"memoryMb":{"type":"integer"},"timeoutSeconds":{"type":"integer"}},"required":["serverId","kind","fasta"],"additionalProperties":false}},
                {"name":"workspace_script","description":"Save a server plugin/tool analysis script as a new files/*.py, .R or .sh in the application workspace. Execution is only via workspace_analysis, never direct SSH. Final results must stay under /output. Temporary dependencies and caches may use the private task runtime; each task starts fresh. Fixed workflows continue to use their fixed source and workflow_plan.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"path":{"type":"string"},"content":{"type":"string"}},"required":["serverId","path","content"],"additionalProperties":false}},
                {"name":"workspace_analysis","description":"Run analysis directly in the prepared user-owned Conda environment without sudo, Podman or mandatory isolation. Pass scripts and data paths in inputs, and use argv arguments for input paths. Never delete, move or overwrite original inputs: save all results under /output (mapped to this task directory). For dependencies use workflow_prepare and inspect its logs; retry after correcting errors. All intentional deletions must use server_delete, display paths, scope and byte size in chat, then wait for the trusted interface display receipt (not a second approval click); never forge receipts or perform deletion through a script or command. Inspect task_list/task_logs and resume completed workflow steps when available.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"workflow":{"enum":["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"]},"step":{"type":"string"},"inputs":{"type":"array","items":{"type":"string"}},"argv":{"type":"array","items":{"type":"string"}},"cpu":{"type":"integer"},"memoryMb":{"type":"integer"},"timeoutSeconds":{"type":"integer"}},"required":["serverId","inputs","argv"],"additionalProperties":false}},
                {"name":"compute_status","description":"Inspect this SSH user's Conda readiness, registered environments and input data sources.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"]}},
                {"name":"task_list","description":"List this SSH account's tasks, including tasks continuing after desktop disconnect.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"]}},
                {"name":"task_logs","description":"Read task logs. Defaults to the latest 64 KiB; returned segments and nextOffset allow reading complete archived logs without rerunning the task.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"},"segment":{"type":"string"},"offset":{"type":"integer","minimum":0}},"required":["serverId","id"]}},
                {"name":"cancel_task","description":"Request cancellation of this account's task; preserves original data and partial outputs.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"},"id":{"type":"string"}},"required":["serverId","id"]}},
                {"name":"list_servers","description":"List only SSH servers explicitly selected by the user. Never request or expose credentials.","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},
                {"name":"get_resources","description":"Read CPU, memory, disk and GPU metrics on a selected host using a fixed read-only probe. For environment installation and repair use workflow_prepare with packages, channels and script; it runs through SSH with the selected ordinary account. A broken site Conda is skipped and a private installer is prepared automatically. Use server_files to inspect inputs and workflow_plan for the fixed analysis.","inputSchema":{"type":"object","properties":{"serverId":{"type":"string"}},"required":["serverId"],"additionalProperties":false}}
            ]})),
            "tools/call" => {
                let params = &request["params"];
                let value = match params["name"].as_str().unwrap_or("") {
                    "server_environment" => repair_environment(&root,&node,&script,&params["arguments"]),
                    "workflow_catalog" => crate::personal_compute::tool_catalog(&params["arguments"]),
                    "server_delete" | "server_write" | "server_copy" | "server_files" | "workspace_files" | "workspace_config" | "workspace_restore" | "workspace_index" | "workspace_script" | "workspace_analysis" | "workflow_prepare" | "workflow_prepare_status" | "workflow_readiness" | "workflow_environment" | "workflow_inspect" | "workflow_plan" | "workflow_plans" | "workflow_result" => {
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
                    "compute_status" | "task_list" | "task_logs" | "cancel_task" => {
                        let action=match params["name"].as_str().unwrap_or(""){"compute_status"=>"status","task_list"=>"jobs","task_logs"=>"logs",_=>"cancel"};
                        crate::personal_compute::request(&root,&node,&script,params["arguments"]["serverId"].as_str().unwrap_or(""),action,params["arguments"].clone(),true)
                    },
                    "list_servers" => list(&root).map(|hosts| json!(hosts.into_iter().filter(|h| h.selected).collect::<Vec<_>>())),
                    "get_resources" => metrics(&root, &node, &script, params["arguments"]["serverId"].as_str().unwrap_or(""), true),
                    "execute" => call(&root, &node, &script, params["arguments"]["serverId"].as_str().unwrap_or(""), "execute", params["arguments"]["command"].as_str().unwrap_or(""), params["arguments"]["timeoutSeconds"].as_u64().unwrap_or(60), true),
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
    fn selected_host_still_cannot_run_arbitrary_agent_commands() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        persist(&root, &[Host { worker_node_id:None,id:"test".into(),name:"test".into(),host:"127.0.0.1".into(),port:22,username:"test".into(),auth:"password".into(),selected:true,fingerprint:None,host_key:None }]).unwrap();
        // There are no credentials or executable. The policy must reject before either is accessed.
        let error = call(&root, Path::new("missing"), Path::new("missing"), "test", "execute", "python3 -c 'pass'", 5, true).unwrap_err();
        assert!(error.contains("通用 SSH 命令已禁用"));
        fs::remove_dir_all(root).unwrap();
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
        assert!(environment_options(&json!({"channel":"https://x/' ; rm -rf x"})).is_err());
        assert!(environment_options(&json!({"pythonVersion":"3.10","channel":"https://mirror.example/conda-forge"})).unwrap().contains("3.10"));
    }
    #[test]
    fn bootstrap_checks_selection_before_credentials() {
        let temp=std::env::temp_dir().join(format!("ssh-repair-{}",uuid::Uuid::new_v4()));
        assert!(repair_environment(&temp,Path::new("missing"),Path::new("missing"),&json!({"serverId":"unknown","action":"repair"})).unwrap_err().contains("未被用户选择"));
    }
}
