use serde_json::{json,Value};
use std::{io::{BufRead,BufReader,Write},process::{Child,Command,Stdio},sync::Mutex};
use tauri::{AppHandle,Manager};
use tauri_plugin_dialog::{DialogExt,MessageDialogButtons};
struct Service { child:Child,url:String,control:String }
static SERVICE:Mutex<Option<Service>>=Mutex::new(None);

pub fn start(app:&AppHandle,root:&std::path::Path)->Result<String,String>{
    // Shared research is available to authenticated accounts; SSH access is still checked.
    crate::hub_auth::require(root,"")?;
    let mut slot=SERVICE.lock().map_err(|_|"科研服务繁忙")?;
    if let Some(s)=slot.as_mut(){if s.child.try_wait().map_err(|_|"无法检查科研服务")?.is_none(){return Ok(s.url.clone());}}
    let mut config=research_connection(root,&crate::hub_auth::base())?;
    // Team-shared research uses this profile's SSH credentials only for the fixed 8100 proxy.
    let control=uuid::Uuid::new_v4().simple().to_string();
    config["control"]=json!(control);config["directory"]=json!(crate::paths::resource(app,"education"));
    let mut command=Command::new(crate::paths::node_binary(app));
    command.arg(crate::paths::resource(app,"ssh").join("education.cjs")).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x0800_0000);}
    let mut child=command.spawn().map_err(|_|"科研服务启动失败，请检查安装是否完整")?;
    if writeln!(child.stdin.as_mut().unwrap(),"{}",config).is_err(){let _=child.kill();return Err("科研服务启动失败".into());}
    let stdout=child.stdout.take().unwrap();let(tx,rx)=std::sync::mpsc::channel();
    std::thread::spawn(move||{let mut line=String::new();let _=BufReader::new(stdout).read_line(&mut line);let _=tx.send(line);});
    let result=(||{let line=rx.recv_timeout(std::time::Duration::from_secs(25)).map_err(|_|"科研服务连接超时，请重试")?;
        let value:Value=serde_json::from_str(&line).map_err(|_|"科研服务启动失败")?;
        value["url"].as_str().map(str::to_owned).ok_or_else(||value["error"].as_str().unwrap_or("科研服务连接失败").to_string())})();
    match result {Ok(url)=>{*slot=Some(Service{child,url:url.clone(),control});Ok(url)},Err(e)=>{let _=child.kill();let _=child.wait();Err(e)}}
}
pub fn delete(app:&AppHandle,root:&std::path::Path,path:&str)->Result<Value,String>{
    crate::hub_auth::require(root,"")?;
    let parts:Vec<_>=path.split('/').collect();
    if parts.len()!=6 || parts[1]!="api" || parts[2]!="review" || !matches!(parts[3],"ppt"|"rebuttal") || parts[4]!="jobs" || parts[5].is_empty() || parts[5].len()>80 || !parts[5].chars().all(|c|c.is_ascii_alphanumeric()||c=='_'||c=='-'){return Err("删除目标无效".into());}
    if !app.dialog().message("删除此科研任务及其生成文件？此操作不可撤销。").title("确认删除").buttons(MessageDialogButtons::OkCancel).blocking_show(){return Err("已取消删除".into());}
    let slot=SERVICE.lock().map_err(|_|"科研服务繁忙")?;let s=slot.as_ref().ok_or("请先打开教育科研")?;
    let mut url=reqwest::Url::parse(&s.url).map_err(|_|"科研服务地址无效")?;url.set_path("/control/delete");url.query_pairs_mut().append_pair("path",path);
    reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(std::time::Duration::from_secs(30)).build().map_err(|_|"删除请求失败")?.post(url).header("X-OMaaX-Control",&s.control).send().map_err(|_|"删除请求失败，请重试")?.json().map_err(|_|"删除结果读取失败".into())
}
pub fn stop(){if let Ok(mut slot)=SERVICE.lock(){if let Some(mut s)=slot.take(){let _=s.child.kill();let _=s.child.wait();}}}

fn research_connection(root:&std::path::Path,shared:&std::path::Path)->Result<Value,String>{
    let (directory,id)=research_profile(root,shared)?;
    crate::ssh_hosts::connection(&directory,&id)
}
fn research_profile(root:&std::path::Path,shared:&std::path::Path)->Result<(std::path::PathBuf,String),String>{
    // Only the fixed research proxy may reuse the existing machine service profile.
    // Agent selection is independent of access to the shared research service.
    for directory in [root,shared] {
        if let Some(host)=crate::ssh_hosts::list(directory)?.into_iter().find(|h|h.host=="192.168.0.175" && h.host_key.is_some()) {
            return Ok((directory.to_path_buf(),host.id));
        }
    }
    Err("科研服务尚未配置，请联系管理员完成服务连接配置".into())
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn shared_research_does_not_require_account_binding_or_agent_selection(){
        let temp=std::env::temp_dir().join(format!("omaax-research-test-{}",uuid::Uuid::new_v4()));let account=temp.join("account");let shared=temp.join("shared");
        std::fs::create_dir_all(shared.join("ssh")).unwrap();
        let mut host=json!({"id":"shared","name":"research","host":"192.168.0.175","port":26518,"username":"service","auth":"password","selected":false,"fingerprint":"known","hostKey":"trusted"});
        std::fs::write(shared.join("ssh/hosts.json"),json!([host]).to_string()).unwrap();
        assert_eq!(research_profile(&account,&shared).unwrap(),(shared.clone(),"shared".into()));
        assert!(!account.exists());
        host["hostKey"]=Value::Null;
        std::fs::write(shared.join("ssh/hosts.json"),json!([host]).to_string()).unwrap();
        assert!(research_profile(&account,&shared).is_err());
        std::fs::remove_dir_all(temp).unwrap();
    }
}
