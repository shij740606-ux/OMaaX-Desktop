use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use sha2::Digest;
use std::{path::Path, io::Write, process::{Command, Stdio}};

const SERVER_WORKSPACE: &str = include_str!("../../resources/ssh/server_workspace.py");
const WORKSPACE_CONTEXT: &str = include_str!("../../resources/ssh/workspace_context.py");
const SERVER_FILE_OPS: &str = include_str!("../../resources/ssh/server_file_ops.py");
const SERVER_DELETE: &str = include_str!("../../resources/ssh/server_delete.py");
const SERVER_SETUP: &str = include_str!("../../resources/ssh/server_setup.py");
const RUNNER: &str = include_str!("../../resources/ssh/personal_runner.py");
const WORKFLOW_ENGINE: &str = include_str!("../../resources/ssh/workflow_engine.py");
const WORKFLOW_CATALOG: &str = include_str!("../../resources/workflows/catalog.json");
const WORKFLOW_ADAPTER: &str = include_str!("../../resources/ssh/workflow_adapter.py");
const WORKFLOW_ENVIRONMENTS: &str = include_str!("../../resources/ssh/workflow_environments.py");
const WORKFLOW_DEPENDENCIES: &str = include_str!("../../resources/ssh/workflow-dependencies.json");
const WORKFLOW_BUNDLE: &[u8] = include_bytes!("../../resources/workflows/kernel.zip");

fn runner_source() -> String {
    let engine_hash=format!("{:x}",sha2::Sha256::digest(format!("{WORKFLOW_ENGINE}\n{RUNNER}\n{WORKFLOW_ADAPTER}\n{WORKFLOW_ENVIRONMENTS}\n{WORKFLOW_DEPENDENCIES}\n{SERVER_WORKSPACE}\n{WORKSPACE_CONTEXT}\n{SERVER_FILE_OPS}").as_bytes()));
    let mut prefix=String::from("import sys,types,base64,json\n");
    for (name,source) in [("workspace_context",WORKSPACE_CONTEXT),("server_file_ops",SERVER_FILE_OPS),("server_setup",SERVER_SETUP)] {prefix.push_str(&format!("m=types.ModuleType('{name}')\nexec(compile(base64.b64decode('{}'),'<{name}>','exec'),m.__dict__)\nsys.modules['{name}']=m\n",STANDARD.encode(source)));}
    // Deletion imports storage, so load it after server_workspace has been registered.
    let delete_module=format!("d=types.ModuleType('server_delete')\nexec(compile(base64.b64decode('{}'),'<server_delete>','exec'),d.__dict__)\nsys.modules['server_delete']=d\n",STANDARD.encode(SERVER_DELETE));
    format!("{prefix}import sys,types,base64,json\nt=types.ModuleType('server_workspace')\nexec(compile(base64.b64decode('{}'),'<server_workspace>','exec'),t.__dict__)\nsys.modules['server_workspace']=t\n{delete_module}e=types.ModuleType('workflow_environments')\nexec(compile(base64.b64decode('{}'),'<workflow_environments>','exec'),e.__dict__)\ne.DEPENDENCIES=json.loads(base64.b64decode('{}'))\nsys.modules['workflow_environments']=e\nw=types.ModuleType('workflow_engine')\nexec(compile(base64.b64decode('{}'),'<workflow_engine>','exec'),w.__dict__)\nw.CATALOG=json.loads(base64.b64decode('{}'))\nw.ADAPTER_SOURCE=base64.b64decode('{}').decode()\nw.ENGINE_HASH='{}'\nsys.modules['workflow_engine']=w\n{}",STANDARD.encode(SERVER_WORKSPACE),STANDARD.encode(WORKFLOW_ENVIRONMENTS),STANDARD.encode(WORKFLOW_DEPENDENCIES),STANDARD.encode(WORKFLOW_ENGINE),STANDARD.encode(WORKFLOW_CATALOG),STANDARD.encode(WORKFLOW_ADAPTER),engine_hash,RUNNER)
}

struct FrozenRunner { hash:String, encoded:String }
static FROZEN:std::sync::OnceLock<Result<FrozenRunner,String>>=std::sync::OnceLock::new();
fn frozen_runner(node:&Path)->Result<&'static FrozenRunner,String>{
    FROZEN.get_or_init(||{
        let source=runner_source();let hash=format!("{:x}",sha2::Sha256::digest(source.as_bytes()));
        let mut cmd=Command::new(node);cmd.arg("-e").arg("let b='';process.stdin.on('data',c=>b+=c);process.stdin.on('end',()=>process.stdout.write(require('node:zlib').gzipSync(Buffer.from(b)).toString('base64')))").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)] {use std::os::windows::process::CommandExt;cmd.creation_flags(0x0800_0000);}
        let mut child=cmd.spawn().map_err(|e|e.to_string())?;let mut input=child.stdin.take().ok_or("压缩管道不可用")?;
        input.write_all(source.as_bytes()).map_err(|e|e.to_string())?;drop(input);
        let output=child.wait_with_output().map_err(|e|e.to_string())?;
        if !output.status.success(){return Err("执行组件压缩失败".into());}
        Ok(FrozenRunner{hash,encoded:String::from_utf8(output.stdout).map_err(|e|e.to_string())?})
    }).as_ref().map_err(Clone::clone)
}
fn runner_command(hash:&str,payload:&str)->String{
    format!("if ! command -v python3 >/dev/null 2>&1; then echo OMAAX_RUNNER_MISSING >&2; exit 78; fi\npython3 - <<'OMAAX_REQUEST'\nimport base64,hashlib,json,pathlib,platform,runpy,sys\nm=pathlib.Path('/etc/machine-id')\ni=(m.read_text().strip() if m.exists() else '')+':'+platform.node()\nk=hashlib.sha256(i.encode()).hexdigest()[:16]\nold=pathlib.Path.home()/'.local/share/biodsh-personal'/k\nr=old if (old/'state.sqlite').is_file() else pathlib.Path.home()/'omaax'/'.servers'/k\np=r/'runner-{hash}.py'\nif any(x.is_symlink() for x in [p,*p.parents]):raise ValueError('Invalid runner path')\nif not p.is_file():\n print('OMAAX_RUNNER_MISSING',file=sys.stderr);sys.exit(78)\nif hashlib.sha256(p.read_bytes()).hexdigest()!='{hash}':raise ValueError('执行组件校验失败，请重新配置服务器')\ng=runpy.run_path(str(p),run_name='biodsh_runner')\ntry: print(json.dumps({{'result':g['dispatch'](json.loads(base64.b64decode('{payload}')))}}))\nexcept Exception as e: print(json.dumps({{'error':str(e)}}))\nOMAAX_REQUEST")
}

pub fn tool_catalog(body:&Value)->Result<Value,String> {
    let catalog:Value=serde_json::from_str(WORKFLOW_CATALOG).map_err(|e|e.to_string())?;
    let rows=catalog["workflows"].as_array().ok_or("流程目录无效")?;
    if let Some(id)=body["workflow"].as_str() {
        let flow=rows.iter().find(|f|f["id"]==id).ok_or("流程不存在")?;
        if let Some(step)=body["step"].as_str() {
            return flow["nodes"].as_array().into_iter().flatten().chain(flow["extraNodes"].as_array().into_iter().flatten()).find(|s|s["id"]==step).cloned().ok_or("步骤不存在".into());
        }
        return Ok(json!({"version":catalog["version"],"id":flow["id"],"name":flow["name"],"defaultSteps":flow["defaultSteps"],"steps":flow["nodes"].as_array().into_iter().flatten().chain(flow["extraNodes"].as_array().into_iter().flatten()).map(|s|json!({"id":s["id"],"name":s["name"],"software":s["software"],"available":s["entryPresent"],"kind":s["kind"]})).collect::<Vec<_>>() }));
    }
    Ok(json!({"version":catalog["version"],"validationMode":"execution_only","workflows":rows.iter().map(|f|json!({"id":f["id"],"name":f["name"],"description":f["description"],"defaultSteps":f["defaultSteps"]})).collect::<Vec<_>>() }))
}

pub fn session_context(home:&Path,session:Option<&str>)->Result<Value,String> {
    let sid=session.unwrap_or("manual");
    if sid.is_empty()||sid.len()>128||!sid.chars().all(|c|c.is_ascii_alphanumeric()||c=='-'||c=='_'){return Err("对话编号不正确".into());}
    let file=home.join("storages/workspace.json");
    let document:Value=std::fs::read(file).ok().and_then(|b|serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
    let selected=document.pointer("/tables/workspaces").and_then(Value::as_object).and_then(|rows|rows.iter().find(|(_,w)|session.is_some()&&w["sessionIds"].as_array().is_some_and(|ids|ids.iter().any(|id|id==sid))).or_else(||if session.is_none(){rows.iter().next()}else{None}));
    let (project_id,project_name)=selected.map(|(id,w)|(id.as_str(),w["title"].as_str().filter(|s|!s.is_empty()).unwrap_or("未命名项目"))).unwrap_or(("unassigned","未归档项目"));
    let cached=home.join("session-contexts").join(format!("{sid}.json"));
    let previous:Value=std::fs::read(&cached).ok().and_then(|b|serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
    let name=if sid=="manual"{"页面提交".to_string()}else{previous["sessionName"].as_str().map(String::from).unwrap_or_else(||format!("对话 {}",&sid[..sid.len().min(8)]))};
    Ok(json!({"projectId":project_id,"projectName":project_name,"sessionId":sid,"sessionName":name}))
}

pub fn save_session_context(home:&Path,context:&Value)->Result<(),String> {
    let fields=["projectId","projectName","sessionId","sessionName"];
    if !context.as_object().is_some_and(|v|v.len()==fields.len() && fields.iter().all(|k|v.get(*k).and_then(Value::as_str).is_some_and(|s|!s.is_empty()&&s.len()<=2048&&!s.contains(['\0','\n','\r'])))){return Err("项目与对话信息不正确".into());}
    let sid=context["sessionId"].as_str().ok_or("对话编号不正确")?;let actual=session_context(home,Some(sid))?;
    if context["projectId"]!=actual["projectId"]{return Err("项目与对话不匹配".into());}
    let dir=home.join("session-contexts");std::fs::create_dir_all(&dir).map_err(|e|e.to_string())?;
    let temp=dir.join(format!("{sid}-{}.tmp",uuid::Uuid::new_v4()));std::fs::write(&temp,serde_json::to_vec(context).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    std::fs::rename(temp,dir.join(format!("{sid}.json"))).map_err(|e|e.to_string())?;Ok(())
}

pub fn remember_server(home:&Path,session:&str,server:&str)->Result<(),String> {
    session_context(home,Some(session))?;
    let dir=home.join("session-servers");std::fs::create_dir_all(&dir).map_err(|e|e.to_string())?;
    let file=dir.join(format!("{session}.json"));let mut ids:Vec<String>=std::fs::read(&file).ok().and_then(|b|serde_json::from_slice(&b).ok()).unwrap_or_default();
    ids.retain(|id|id!=server);ids.insert(0,server.into());
    let temp=dir.join(format!("{session}-{}.tmp",uuid::Uuid::new_v4()));std::fs::write(&temp,serde_json::to_vec(&ids).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;std::fs::rename(temp,file).map_err(|e|e.to_string())?;Ok(())
}

fn workflow_selection(payload:&Value, catalog:&Value) -> Result<Value,String> {
    let selected=&payload["selection"];
    if selected.is_null(){return Ok(Value::Null);}
    let fields=["workflow","version","serverId","source","input","prefix","steps","parameters","configs","preferences","context","cpu","memoryMb","timeoutSeconds","resultId"];
    let object=selected.as_object().ok_or("流程选择内容不正确")?;
    if object.keys().any(|k|!fields.contains(&k.as_str())) || selected.to_string().len()>512*1024 {return Err("流程选择内容过多或不正确".into());}
    if selected["workflow"]!=payload["workflowId"] || selected["version"]!=payload["version"] {return Err("流程选择与版本不匹配".into());}
    let flow=catalog["workflows"].as_array().and_then(|rows|rows.iter().find(|f|f["id"]==selected["workflow"])).ok_or("流程不存在")?;
    let known=|id:&str|flow["nodes"].as_array().into_iter().flatten().chain(flow["extraNodes"].as_array().into_iter().flatten()).any(|s|s["id"]==id);
    let steps=selected["steps"].as_array().ok_or("请选择流程步骤")?;
    if steps.iter().any(|s|!s.as_str().is_some_and(known)){return Err("流程步骤不存在".into());}
    for key in ["parameters","configs"] {
        let values=selected[key].as_object().ok_or("流程参数不正确")?;
        if values.iter().any(|(id,v)|!known(id)||!v.is_object()){return Err("流程参数对应的步骤不存在".into());}
    }
    for key in ["serverId","source","input","prefix","resultId"] {
        if let Some(v)=object.get(key){if !v.as_str().is_some_and(|s|s.len()<=4096&&!s.contains('\0')){return Err("流程目录或编号不正确".into());}}
    }
    let input=selected["input"].as_str().ok_or("请选择数据子目录")?;
    if input.is_empty()||input.contains('\\')||input.contains(':')||input.split('/').any(|part|part==".."){return Err("请输入服务器绝对路径或已有数据目录的子目录".into());}
    for key in ["cpu","memoryMb","timeoutSeconds"] {
        if let Some(v)=object.get(key){if !v.as_u64().is_some_and(|n|n>0){return Err("计算资源必须为正整数".into());}}
    }
    Ok(selected.clone())
}

pub fn bind_workflow(home:&Path, payload:&Value) -> Result<Value,String> {
    let sid=payload["sessionId"].as_str().ok_or("请选择对话")?;
    if sid.is_empty() || sid.len()>128 || !sid.chars().all(|c|c.is_ascii_alphanumeric()||c=='-'||c=='_'){return Err("对话编号不正确".into());}
    let catalog:Value=serde_json::from_str(WORKFLOW_CATALOG).map_err(|e|e.to_string())?;
    let id=payload["workflowId"].as_str().ok_or("请选择流程")?;
    if !catalog["workflows"].as_array().is_some_and(|rows|rows.iter().any(|r|r["id"]==id)) || payload["version"]!=catalog["version"] {return Err("流程版本不匹配".into());}
    let selection=workflow_selection(payload,&catalog)?;
    if !selection["context"].is_null()&&selection["context"]["sessionId"]!=sid{return Err("流程选择与当前对话不匹配".into());}
    if !selection["context"].is_null(){save_session_context(home,&selection["context"])?;}
    let dir=home.join("workflow-bindings");
    let ready:Value=serde_json::from_slice(&std::fs::read(dir.join("ready.json")).map_err(|_|"流程工具护栏尚未启用，请升级运行环境并重启智能体")?).map_err(|_|"流程护栏状态不正确")?;
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|e|e.to_string())?.as_millis() as u64;
    if ready["schema"]!=1 || !ready["at"].as_u64().is_some_and(|at|at<=now && now-at<=20000){return Err("流程护栏未运行，禁止开始一体化分析流程对话".into());}
    if !selection.is_null() && ready["selectionContext"]!=true {return Err("请更新并重启智能体，以启用流程对话".into());}
    let mut binding=json!({"schema":1,"sessionId":sid,"workflow":id,"version":catalog["version"]});
    if !selection.is_null(){binding["selection"]=selection;}
    let mut file=std::fs::OpenOptions::new().write(true).create_new(true).open(dir.join(format!("{sid}.json"))).map_err(|_|"此对话已绑定流程，请使用新的一体化分析流程对话")?;
    file.write_all(&serde_json::to_vec(&binding).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    Ok(binding)
}

fn permitted(action:&str,agent:bool)->bool {
    match action {
        "server_delete" | "server_write" | "server_copy" | "server_files" | "workspace_files" | "workspace_config" | "workspace_restore" | "workspace_index" | "workspace_script" | "workspace_analysis" | "status" | "jobs" | "logs" | "audit" | "cancel" | "export" | "workflow_inspect" | "workflow_readiness" | "workflow_plan" | "workflow_plans" | "workflow_result" | "workflow_prepare_status" | "workflow_validate" | "workflow_prepare" | "workflow_environment" => true,
        "initialize" | "setup_start" | "setup_status" | "server_environments" | "server_delete_requests" | "server_delete_confirm" | "server_delete_reject" | "server_write_requests" | "server_write_confirm" | "server_write_reject" | "submit" | "configure" | "bootstrap" | "import_prepare" | "import_finish" | "workflow_confirm" | "workflow_bundle_status" => !agent,
        _ => false,
    }
}

fn save_workflow_inbox(root:&Path,host:&str,id:&str,selection:&Value,state:&str)->Result<(),String>{
    let temp=root.join(format!("workflow-inbox-{}.tmp",uuid::Uuid::new_v4()));
    let notice=json!({"host":host,"id":id,"workflow":selection["workflow"],"context":selection["context"],"selection":selection,"state":state});
    std::fs::write(&temp,serde_json::to_vec(&notice).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    std::fs::rename(temp,root.join("workflow-inbox.json")).map_err(|e|e.to_string())
}

pub fn request(root: &Path, node: &Path, script: &Path, host: &str, action: &str, body: Value, agent: bool) -> Result<Value, String> {
    if action=="workflow_catalog" { return serde_json::from_str(WORKFLOW_CATALOG).map_err(|e|e.to_string()); }
    if action=="workflow_inbox" {
        let file=root.join("workflow-inbox.json");
        return if file.exists(){serde_json::from_slice(&std::fs::read(file).map_err(|e|e.to_string())?).map_err(|e|e.to_string())}else{Ok(Value::Null)};
    }
    let record = crate::ssh_hosts::list(root)?.into_iter().find(|h| h.id == host).ok_or("服务器不存在")?;
    if agent && !record.selected { return Err("该服务器未被用户选择".into()); }
    let allowed = permitted(action,agent);
    if !allowed { return Err("不允许的服务器操作".into()); }
    if matches!(action,"workflow_plan"|"workflow_environment"|"workflow_prepare") {
        let status=request(root,node,script,host,"workflow_bundle_status",json!({}),false)?;
        if status["ready"]!=true {
            let payload=json!({"connection":crate::ssh_hosts::connection(root,host)?,"bundle":STANDARD.encode(WORKFLOW_BUNDLE),"destination":status["path"],"sha256":status["sha256"]});
            let mut cmd=Command::new(node);cmd.arg(script.with_file_name("stage-workflows.cjs")).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
            #[cfg(windows)] {use std::os::windows::process::CommandExt;cmd.creation_flags(0x0800_0000);}
            let mut child=cmd.spawn().map_err(|e|e.to_string())?;let mut stdin=child.stdin.take().ok_or("源码传输管道不可用")?;
            stdin.write_all(&serde_json::to_vec(&payload).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;drop(stdin);
            let output=child.wait_with_output().map_err(|e|e.to_string())?;let result:Value=serde_json::from_slice(&output.stdout).map_err(|_|"流程源码传输未完成")?;
            if let Some(error)=result["error"].as_str(){return Err(error.into());}
        }
    }
    let payload = STANDARD.encode(serde_json::to_vec(&json!({"action":action,"body":body,"actor":if agent{"agent"}else{"user"}})).map_err(|e| e.to_string())?);
    // Only bundled code and base64 data enter the shell; no agent-provided shell text.
    let frozen=frozen_runner(node)?;
    let cached=runner_command(&frozen.hash,&payload);
    let mut value=crate::ssh_hosts::call(root,node,script,host,"execute",&cached,if action=="bootstrap"{900}else{120},false)?;
    if value["exitCode"]==78 && value["stderr"].as_str().unwrap_or("").contains("OMAAX_RUNNER_MISSING") {
        // Cache misses happen before dispatch, so this retry cannot duplicate a mutation.
        let install=format!("if ! command -v python3 >/dev/null 2>&1; then sudo -n apt-get install -y python3 >&2 || exit 1; fi\npython3 - <<'OMAAX_INSTALL'\nimport base64,gzip,os\ns=gzip.decompress(base64.b64decode('{}'))\ng={{'__name__':'biodsh_runner'}}\nexec(compile(s,'<omaax>','exec'),g)\nr=g['root']()\np=r/'runner-{}.py'\nif p.is_symlink():raise ValueError('Invalid runner path')\nt=r/('runner-{}.tmp-'+str(os.getpid()))\nwith t.open('xb') as f:f.write(s)\nt.chmod(0o600)\nt.replace(p)\nOMAAX_INSTALL",frozen.encoded,frozen.hash,frozen.hash);
        let installed=crate::ssh_hosts::call(root,node,script,host,"execute",&install,120,false)?;
        if installed["exitCode"]!=0{return Err(format!("服务器基础组件准备未完成：{}",installed["stderr"].as_str().unwrap_or("请检查 Python 3 或系统安装权限")));}
        value=crate::ssh_hosts::call(root,node,script,host,"execute",&cached,if action=="bootstrap"{900}else{120},false)?;
    }
    if value["exitCode"].as_i64() != Some(0) { return Err(format!("用户级执行组件失败：{}",value["stderr"].as_str().unwrap_or("请检查 Python 3 环境"))); }
    let result: Value = serde_json::from_str(value["stdout"].as_str().unwrap_or("")).map_err(|_| "执行组件响应无效")?;
    if let Some(error) = result["error"].as_str() {
        if agent && action=="workflow_plan" && ["先选择并检查此流程的运行环境","尚不能运行","环境登记已失效"].iter().any(|message|error.contains(message)) {
            save_workflow_inbox(root,host,&uuid::Uuid::new_v4().to_string(),&body,"needs_environment")?;
        }
        return Err(error.into());
    }
    if agent && action=="workflow_plan" {
        save_workflow_inbox(root,host,result["result"]["plan"]["id"].as_str().ok_or("计划编号缺失")?,&body,"planned")?;
    }
    Ok(result["result"].clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn interrupted_preparation_reuses_its_target_transfer_and_conversation() {
        let root=std::env::current_dir().unwrap().join("target/server-dispatch-tests").join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir_all(&root).unwrap();
        let transfer=uuid::Uuid::new_v4().to_string();
        transfer_record(&root,&transfer,&json!({"id":transfer,"state":"complete","result":{"source":"import_existing","path":"saved"}})).unwrap();
        let path=root.join("dispatch.json");
        let mut saved=json!({"host":"176","original":{"id":"test","sourceHost":"175","sources":["data"],"context":{"sessionId":"dialogue-a"}},"transfers":{"data":transfer}});
        let prepared=prepare_dispatch(&root,Path::new("missing"),Path::new("missing"),&path,&mut saved).unwrap();
        assert_eq!(prepared["sources"],json!(["import_existing"]));assert_eq!(prepared["mounts"]["data"],"import_existing");assert_eq!(prepared["context"]["sessionId"],"dialogue-a");
        assert_eq!(saved["host"],"176");assert_eq!(prepare_dispatch(&root,Path::new("missing"),Path::new("missing"),&path,&mut saved).unwrap(),prepared);
        let guard=operation_guard("test-dispatch-exclusive").unwrap();assert!(operation_guard("test-dispatch-exclusive").is_err());drop(guard);assert!(operation_guard("test-dispatch-exclusive").is_ok());
    }
    #[test]
    fn runner_requests_use_a_small_versioned_loader() {
        let command=runner_command(&"a".repeat(64),"e30=");
        assert!(command.len()<4096);assert!(command.contains("OMAAX_RUNNER_MISSING"));
        assert!(command.contains("hashlib.sha256(p.read_bytes())"));
        assert!(!command.contains("gzip.decompress"));
        for action in ["server_delete_confirm","server_delete_reject","setup_start","server_environments"]{assert!(!permitted(action,true));assert!(permitted(action,false));}
        assert!(permitted("server_delete",true));
    }
    #[test]
    fn bundled_server_modules_load_together() {
        let Ok(python)=std::env::var("OMAAX_TEST_PYTHON")else{return;};
        let mut child=Command::new(python).arg("-").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
        let source=format!("{}\nassert sys.modules['server_delete'].LIMIT == 10000000000\nassert callable(sys.modules['server_setup'].dispatch)\nprint('bundled server modules loaded')\n",runner_source());
        let mut input=child.stdin.take().unwrap();input.write_all(source.as_bytes()).unwrap();drop(input);
        let output=child.wait_with_output().unwrap();assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stderr));
    }
    #[test]
    fn model_environment_handoff_keeps_exact_selection_and_conversation(){
        let root=std::env::current_dir().unwrap().join("target/workflow-inbox-tests").join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir_all(&root).unwrap();
        let selection=json!({"workflow":"singlecell","context":{"sessionId":"session-test"},"steps":["singlecell_upstream"],"configs":{"singlecell_upstream":{"aligner":"star","skip_cellbender":true}}});
        save_workflow_inbox(&root,"175","draft-test",&selection,"needs_environment").unwrap();
        let notice:Value=serde_json::from_slice(&std::fs::read(root.join("workflow-inbox.json")).unwrap()).unwrap();
        assert_eq!(notice["selection"],selection);assert_eq!(notice["context"]["sessionId"],"session-test");assert_eq!(notice["state"],"needs_environment");
    }
    #[test]
    fn context_and_server_association_use_real_workspace_membership() {
        let home=std::env::current_dir().unwrap().join("target/workflow-chat-tests").join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir_all(home.join("storages")).unwrap();
        std::fs::write(home.join("storages/workspace.json"),json!({"tables":{"workspaces":{"project-a":{"title":"项目甲","sessionIds":["session-a"]},"project-b":{"title":"项目乙","sessionIds":["session-b"]}}}}).to_string()).unwrap();
        let mut context=session_context(&home,Some("session-a")).unwrap();assert_eq!(context["projectId"],"project-a");context["sessionName"]=json!("我的分析");save_session_context(&home,&context).unwrap();
        assert_eq!(session_context(&home,Some("session-a")).unwrap()["sessionName"],"我的分析");
        context["projectId"]=json!("project-b");assert!(save_session_context(&home,&context).is_err());
        assert!(session_context(&home,Some("../other")).is_err());
        remember_server(&home,"session-a","175").unwrap();remember_server(&home,"session-a","176").unwrap();remember_server(&home,"session-a","175").unwrap();
        assert_eq!(std::fs::read_to_string(home.join("session-servers/session-a.json")).unwrap(),"[\"175\",\"176\"]");
        for action in ["server_write_confirm","server_write_reject","server_write_requests"] {assert!(!permitted(action,true));assert!(permitted(action,false));}
    }
    #[test]
    fn agent_can_prepare_workflow_software_but_cannot_bypass_data_protection() {
        for action in ["configure","bootstrap","install_environment","import_prepare","import_finish","execute","delete","submit","workflow_confirm"] { assert!(!permitted(action,true)); }
        for action in ["status","jobs","logs","cancel","workflow_plan","workflow_plans","workflow_prepare","workflow_environment"] { assert!(permitted(action,true)); }
        assert!(permitted("bootstrap",false));
    }
    #[test]
    fn dispatch_rejects_invalid_identity_before_network() {
        let p=Path::new("does-not-exist");
        assert!(schedule(p,p,p,&[],json!({}),true).is_err());
        assert!(schedule(p,p,p,&["host".into()],json!({"id":"../bad"}),false).unwrap_err().contains("编号"));
        assert!(schedule(p,p,p,&["host".into()],json!({"id":"valid"}),true).unwrap_err().contains("确认"));
    }
    #[test]
    fn fixed_dialogue_requires_live_guard_and_catalog_version() {
        let p=Path::new("does-not-exist");
        assert!(bind_workflow(p,&json!({"sessionId":"../bad","workflowId":"rnaseq","version":"20261013.1"})).is_err());
        assert!(bind_workflow(p,&json!({"sessionId":"session","workflowId":"rnaseq","version":"old"})).is_err());
        let catalog:Value=serde_json::from_str(WORKFLOW_CATALOG).unwrap();
        assert!(bind_workflow(p,&json!({"sessionId":"session","workflowId":"rnaseq","version":catalog["version"]})).unwrap_err().contains("护栏"));
    }
    #[test]
    fn nine_workflow_catalog_exposes_declared_parameters_without_scientific_gates() {
        let summary=tool_catalog(&json!({})).unwrap();assert_eq!(summary["workflows"].as_array().unwrap().len(),9);
        for id in ["gene_family","rnaseq","singlecell","spatial","metagenome","atacseq","chipseq","cutandrun","methylseq"] {assert_eq!(tool_catalog(&json!({"workflow":id})).unwrap()["id"],id);}
        assert!(!tool_catalog(&json!({"workflow":"rnaseq","step":"rnaseq_de"})).unwrap()["arguments"].as_array().unwrap().is_empty());
        assert!(tool_catalog(&json!({"workflow":"rnaseq","step":"unknown"})).is_err());
    }
    #[test]
    fn internal_selection_keeps_exact_fields_and_rejects_other_paths() {
        let catalog:Value=serde_json::from_str(WORKFLOW_CATALOG).unwrap();
        let mut payload=json!({"workflowId":"rnaseq","version":catalog["version"],"selection":{"workflow":"rnaseq","version":catalog["version"],"serverId":"selected-server","source":"shared","input":".","prefix":"omaax","steps":["rnaseq_de"],"parameters":{"rnaseq_de":{"padj":0.05}},"configs":{},"cpu":2,"memoryMb":2048,"timeoutSeconds":3600}});
        assert_eq!(workflow_selection(&payload,&catalog).unwrap(),payload["selection"]);
        for invalid in ["../private","C:/other","a\\..\\private"] {payload["selection"]["input"]=json!(invalid);assert!(workflow_selection(&payload,&catalog).is_err());}
        payload["selection"]["input"]=json!("/home/other/data");assert!(workflow_selection(&payload,&catalog).is_ok());
        payload["selection"]["input"]=json!(".");payload["selection"]["workflow"]=json!("gene_family");assert!(workflow_selection(&payload,&catalog).is_err());
        payload["selection"]["workflow"]=json!("rnaseq");payload["selection"]["steps"]=json!(["invented"]);assert!(workflow_selection(&payload,&catalog).is_err());
    }
    #[test]
    fn selection_is_saved_only_with_context_capable_guard_and_cannot_be_replaced() {
        let home=std::env::current_dir().unwrap().join("target/workflow-chat-tests").join(uuid::Uuid::new_v4().to_string());
        let dir=home.join("workflow-bindings");std::fs::create_dir_all(&dir).unwrap();
        let catalog:Value=serde_json::from_str(WORKFLOW_CATALOG).unwrap();
        let payload=json!({"sessionId":"new-session","workflowId":"rnaseq","version":catalog["version"],"selection":{"workflow":"rnaseq","version":catalog["version"],"source":"shared","input":".","steps":["rnaseq_qc"],"parameters":{"rnaseq_qc":{"cut_front":true}},"configs":{}}});
        let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
        std::fs::write(dir.join("ready.json"),json!({"schema":1,"at":now}).to_string()).unwrap();
        assert!(bind_workflow(&home,&payload).unwrap_err().contains("重启"));
        std::fs::write(dir.join("ready.json"),json!({"schema":1,"at":now,"selectionContext":true}).to_string()).unwrap();
        let saved=bind_workflow(&home,&payload).unwrap();assert_eq!(saved["selection"],payload["selection"]);
        let disk:Value=serde_json::from_slice(&std::fs::read(dir.join("new-session.json")).unwrap()).unwrap();assert_eq!(disk,saved);
        assert!(bind_workflow(&home,&payload).is_err());
    }
    #[test]
    #[ignore = "Explicit opt-in: reads the current user's saved SSH hosts and checks connectivity"]
    fn live_saved_ssh_status() {
        let root=std::path::PathBuf::from(std::env::var("BIODSH_LIVE_HOME").expect("BIODSH_LIVE_HOME"));
        let runtime=std::path::PathBuf::from(std::env::var("BIODSH_LIVE_RUNTIME").expect("BIODSH_LIVE_RUNTIME"));
        for host in crate::ssh_hosts::list(&root).unwrap() {
            let result=request(&root,&runtime.join("node/node.exe"),&runtime.join("ssh/client.cjs"),&host.id,"status",json!({}),false);
            println!("{}: {:?}",host.name,result);
        }
    }
}

pub fn schedule(root: &Path, node: &Path, script: &Path, ids: &[String], spec: Value, agent: bool) -> Result<Value,String> {
    if agent { return Err("智能体不能直接提交任意命令；请使用一体化分析流程生成计划，在桌面确认后运行".into()); }
    if ids.is_empty() || ids.len()>32 { return Err("请选择 1–32 台候选服务器".into()); }
    if spec["gpu"].as_u64().unwrap_or(0)>0 {return Err("尚未启用 GPU 隔离执行；不会降级为不受保护的任务".into());}
    let job=spec["id"].as_str().ok_or("提交必须带稳定任务编号")?.to_string();
    if job.is_empty() || job.len()>80 || !job.chars().all(|c|c.is_ascii_alphanumeric()||c=='-'||c=='_'){return Err("任务编号无效".into());}
    let dir=root.join("ssh-dispatch");std::fs::create_dir_all(&dir).map_err(|e|e.to_string())?;
    let record=dir.join(format!("{job}.json"));
    let _dispatch=operation_guard(&format!("dispatch:{}",record.display()))?;
    if record.exists(){
        let mut saved:Value=serde_json::from_slice(&std::fs::read(&record).map_err(|e|e.to_string())?).map_err(|_|"调度记录不完整，请核对远程任务，勿重复提交")?;
        if saved["original"]!=spec {return Err("此任务编号已用于其他提交".into());}
        let prepared=prepare_dispatch(root,node,script,&record,&mut saved)?;
        let id=saved["host"].as_str().ok_or("旧记录缺少目标服务器，请核对后使用新任务编号")?;
        let result=request(root,node,script,id,"submit",prepared,agent)?;
        return Ok(json!({"host":id,"job":result,"excluded":[]}));
    }
    let original=spec.clone();
    let sources:Vec<String>=serde_json::from_value(spec["sources"].clone()).map_err(|_|"输入数据列表无效")?;
    let source_host=spec["sourceHost"].as_str().unwrap_or("").to_string();
    if !sources.is_empty(){
        let source=request(root,node,script,&source_host,"status",json!({}),agent)?;
        if sources.iter().any(|s|source["sources"][s].is_null()){return Err("来源服务器未登记所选数据".into());}
    }
    let mut choices = Vec::new(); let mut rejected = Vec::new();
    for id in ids {
        let evaluated = (|| -> Result<f64,String> {
            let status = request(root,node,script,id,"status",json!({}),agent)?;
            if status["protection"].is_null() { return Err(status["error"].as_str().unwrap_or("隔离环境不可用").into()); }
            if !status["images"].as_array().is_some_and(|images| images.contains(&spec["image"])) { return Err("该账号未批准所选镜像".into()); }
            let metric = crate::ssh_hosts::metrics(root,node,script,id,agent)?;
            let cpu = metric["logicalCores"].as_f64().unwrap_or(0.0);
            let free_cpu = cpu*(1.0-metric["cpuPercent"].as_f64().unwrap_or(100.0)/100.0);
            let free_mem = metric["memoryTotal"].as_f64().unwrap_or(0.0)-metric["memoryUsed"].as_f64().unwrap_or(0.0);
            if cpu < spec["cpu"].as_f64().unwrap_or(f64::MAX) || free_mem < spec["memoryMb"].as_f64().unwrap_or(f64::MAX)*1048576.0 { return Err("CPU 或可用内存不足".into()); }
            let jobs = request(root,node,script,id,"jobs",json!({}),agent)?;
            let pending = jobs.as_array().map(|rows| rows.iter().filter(|r| matches!(r["state"].as_str(),Some("running"|"queued"))).count()).unwrap_or(0);
            let busy_cpu=if free_cpu<spec["cpu"].as_f64().unwrap_or(f64::MAX){100000.0}else{0.0};
            Ok(free_cpu + free_mem/1073741824.0 - pending as f64*1000000.0-busy_cpu)
        })();
        match evaluated { Ok(score) => choices.push((score,id)), Err(reason)=>rejected.push(json!({"host":id,"reason":reason})) }
    }
    choices.sort_by(|a,b| b.0.total_cmp(&a.0));
    let (_,id) = choices.first().ok_or_else(|| format!("无可用候选服务器：{rejected:?}"))?;
    let mut pending=std::fs::OpenOptions::new().write(true).create_new(true).open(&record).map_err(|_|"此任务正在准备，请勿并发提交")?;
    let mut saved=json!({"original":original,"host":id,"transfers":{}});
    pending.write_all(serde_json::to_string(&saved).unwrap().as_bytes()).map_err(|e|e.to_string())?;drop(pending);
    let prepared=prepare_dispatch(root,node,script,&record,&mut saved)?;
    // Never fail over after submission: a timeout may mean the original task is already running.
    let result = request(root,node,script,id,"submit",prepared,agent).map_err(|e|format!("提交到 {id} 的任务 {} 未获确认，请在该服务器刷新核对，禁止自动重复提交：{e}",spec["id"]))?;
    Ok(json!({"host":id,"job":result,"excluded":rejected}))
}

static OPERATIONS:std::sync::Mutex<Vec<String>>=std::sync::Mutex::new(Vec::new());
struct OperationGuard(String);
impl Drop for OperationGuard{fn drop(&mut self){if let Ok(mut ids)=OPERATIONS.lock(){ids.retain(|id|id!=&self.0);}}}
fn operation_guard(key:&str)->Result<OperationGuard,String>{
    let mut ids=OPERATIONS.lock().map_err(|e|e.to_string())?;
    if ids.iter().any(|v|v==key){return Err("同一操作正在进行，请勿重复启动".into());}
    ids.push(key.into());Ok(OperationGuard(key.into()))
}
fn save_dispatch(path:&Path,value:&Value)->Result<(),String>{
    let temp=path.with_extension(format!("{}.tmp",uuid::Uuid::new_v4()));
    std::fs::write(&temp,serde_json::to_vec(value).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    std::fs::rename(temp,path).map_err(|e|e.to_string())
}
fn prepare_dispatch(root:&Path,node:&Path,script:&Path,path:&Path,saved:&mut Value)->Result<Value,String>{
    if !saved["spec"].is_null(){return Ok(saved["spec"].clone());}
    let host=saved["host"].as_str().ok_or("旧的准备记录缺少目标服务器，请先核对任务，勿直接重复提交")?.to_string();
    let mut spec=saved["original"].clone();
    let source_host=spec["sourceHost"].as_str().unwrap_or("").to_string();
    let sources:Vec<String>=serde_json::from_value(spec["sources"].clone()).map_err(|_|"输入数据列表无效")?;
    if source_host!=host&&!sources.is_empty(){
        let mut mapped=serde_json::Map::new();let mut imported=Vec::new();
        for source in sources{
            let id=if let Some(id)=saved["transfers"][&source].as_str(){id.to_string()}else{
                let id=create_transfer(root,node,script,&source_host,Some(&host),json!({"source":source,"path":".","context":spec["context"]}))?;
                saved["transfers"][&source]=json!(id);save_dispatch(path,saved)?;id
            };
            let result=resume_transfer(root,node,script,&id)?;
            let name=result["source"].as_str().ok_or("传输后未登记数据")?.to_string();
            mapped.insert(source,json!(name));imported.push(name);
        }
        spec["sources"]=json!(imported);spec["mounts"]=json!(mapped);
    }
    saved["spec"]=spec.clone();save_dispatch(path,saved)?;Ok(spec)
}
fn transfer_record(root:&Path,id:&str,value:&Value)->Result<(),String>{
    let dir=root.join("transfers");std::fs::create_dir_all(&dir).map_err(|e|e.to_string())?;
    let temp=dir.join(format!("{id}-{}.tmp",uuid::Uuid::new_v4()));
    std::fs::write(&temp,serde_json::to_vec(value).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    std::fs::rename(temp,dir.join(format!("{id}.json"))).map_err(|e|e.to_string())
}
pub fn transfers(root:&Path)->Result<Value,String>{
    let dir=root.join("transfers");if !dir.exists(){return Ok(json!([]));}
    let mut rows=Vec::new();
    for file in std::fs::read_dir(&dir).map_err(|e|e.to_string())?.flatten(){
        if file.path().extension().and_then(|v|v.to_str())!=Some("json")||file.file_name().to_string_lossy().ends_with(".progress.json"){continue;}
        let Ok(mut row)=std::fs::read(file.path()).ok().and_then(|b|serde_json::from_slice::<Value>(&b).ok()).ok_or(())else{continue};
        let id=row["id"].as_str().unwrap_or("").to_string();
        let progress=dir.join(format!("{id}.progress.json"));
        if let Some(value)=std::fs::read(progress).ok().and_then(|b|serde_json::from_slice::<Value>(&b).ok()){row["progress"]=value;}
        if row["state"]=="running"&&!OPERATIONS.lock().map_err(|e|e.to_string())?.contains(&id){row["state"]=json!("interrupted");}
        row.as_object_mut().map(|o|{o.remove("files");o.remove("body");});rows.push(row);
    }
    rows.sort_by(|a,b|b["created"].as_f64().unwrap_or(0.).total_cmp(&a["created"].as_f64().unwrap_or(0.)));rows.truncate(100);Ok(json!(rows))
}
pub fn transfer(root:&Path,node:&Path,script:&Path,source:&str,target:Option<&str>,body:Value)->Result<Value,String>{
    let id=create_transfer(root,node,script,source,target,body)?;resume_transfer(root,node,script,&id)
}
fn create_transfer(root:&Path,node:&Path,script:&Path,source:&str,target:Option<&str>,body:Value)->Result<String,String>{
    let mut files=request(root,node,script,source,"export",body.clone(),false)?;
    files.as_array_mut().ok_or("文件清单无效")?.sort_by_key(|f|f["name"].as_str().unwrap_or("").to_string());
    let bytes=files.as_array().unwrap().iter().try_fold(0u64,|total,file|total.checked_add(file["size"].as_u64().unwrap_or(u64::MAX))).ok_or("传输大小无效")?;
    let id=uuid::Uuid::new_v4().to_string();
    let record=json!({"id":id,"source":source,"target":target,"body":body,"files":files,"bytes":bytes,"state":"preparing","created":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|e|e.to_string())?.as_secs_f64()});
    transfer_record(root,&id,&record)?;Ok(id)
}
pub fn resume_transfer(root:&Path,node:&Path,script:&Path,id:&str)->Result<Value,String>{
    if uuid::Uuid::parse_str(id).is_err(){return Err("传输编号不正确".into());}
    let _guard=operation_guard(id)?;let dir=root.join("transfers");
    let mut record:Value=serde_json::from_slice(&std::fs::read(dir.join(format!("{id}.json"))).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    if record["state"]=="complete"{return Ok(record["result"].clone());}
    let progress=dir.join(format!("{id}.progress.json"));
    if record["state"]=="running" {
        let recent=std::fs::read(&progress).ok().and_then(|b|serde_json::from_slice::<Value>(&b).ok());
        let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|e|e.to_string())?.as_secs_f64();
        if recent.as_ref().is_some_and(|p|p["state"]=="running"&&now-p["updated"].as_f64().unwrap_or(0.)<90.){return Err("上一传输连接可能仍在工作，请稍后刷新；不会同时重试同一传输".into());}
    }
    let work=(||->Result<Value,String>{
        let source=record["source"].as_str().ok_or("来源服务器无效")?;
        let target=record["target"].as_str();
        let mut current=request(root,node,script,source,"export",record["body"].clone(),false)?;
        current.as_array_mut().ok_or("文件清单无效")?.sort_by_key(|f|f["name"].as_str().unwrap_or("").to_string());
        if current!=record["files"]{return Err("来源文件清单或内容时间已变化，请发起新的传输；原文件未改动".into());}
        if record["destination"].is_null(){
            let destination=if let Some(host)=target{
                let prepared=request(root,node,script,host,"import_prepare",json!({"id":id,"bytes":record["bytes"],"context":record["body"]["context"]}),false)?;
                prepared["path"].as_str().ok_or("接收目录无效")?.to_string()
            }else{let path=root.join("server-results").join(id);std::fs::create_dir_all(&path).map_err(|e|e.to_string())?;path.to_string_lossy().into_owned()};
            record["destination"]=json!(destination);
        }
        let source=record["source"].as_str().unwrap();let target=record["target"].as_str();
        let payload=json!({"kind":"transfer","timeoutSeconds":43200,"resume":true,"progressFile":progress,"source":crate::ssh_hosts::connection(root,source)?,"target":target.map(|host|crate::ssh_hosts::connection(root,host)).transpose()?,"destination":record["destination"],"files":record["files"],"local":target.is_none()});
        record["state"]=json!("running");transfer_record(root,id,&record)?;
        let result=crate::ssh_hosts::run_request(node,&script.with_file_name("relay.cjs"),payload)?;
        record["state"]=json!("verifying");transfer_record(root,id,&record)?;
        if let Some(host)=record["target"].as_str(){request(root,node,script,host,"import_finish",json!({"id":id,"files":result["files"]}),false)}
        else{Ok(json!({"path":record["destination"],"files":result["files"].as_array().map(Vec::len)}))}
    })();
    match work{
        Ok(mut result)=>{result["transferId"]=json!(id);record["state"]=json!("complete");record["result"]=result.clone();record["error"]=json!(null);transfer_record(root,id,&record)?;Ok(result)},
        Err(error)=>{record["state"]=json!("failed");record["error"]=json!(error);transfer_record(root,id,&record)?;Err(format!("传输 {id} 未完成：{error}。已保留进度，可在数据传输记录中重试；原文件未改动"))}
    }
}
