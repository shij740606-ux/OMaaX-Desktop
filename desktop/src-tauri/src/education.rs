use serde_json::{json,Value};
use std::{io::{BufRead,BufReader,Write},process::{Child,Command,Stdio},sync::Mutex};
use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt,MessageDialogButtons};
struct Service { child:Child,url:String,control:String }
static SERVICE:Mutex<Option<Service>>=Mutex::new(None);

pub fn start(app:&AppHandle,paths:&crate::paths::AppPaths)->Result<String,String>{

    let mut slot=SERVICE.lock().map_err(|_|"科研服务繁忙")?;
    if let Some(s)=slot.as_mut(){if s.child.try_wait().map_err(|_|"无法检查科研服务")?.is_none(){return Ok(s.url.clone());}}
    let root=&paths.root;
    let mut config=json!({"service":crate::paths::resource(app,"education-service"),
        "state":root.join("education"),"uv":crate::paths::uv_binary(app),
        "llm":direct_model(paths,&crate::hub_auth::base()),
        "tectonic":crate::paths::resource(app,"bin").join(if cfg!(windows){"tectonic.exe"}else{"tectonic"})});
    let control=uuid::Uuid::new_v4().simple().to_string();
    config["control"]=json!(control);config["directory"]=json!(crate::paths::resource(app,"education"));
    let mut command=Command::new(crate::paths::node_binary(app));
    command.arg(crate::paths::resource(app,"ssh").join("education.cjs")).env_remove("NODE_OPTIONS").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x0800_0000);}
    let mut child=command.spawn().map_err(|_|"科研服务启动失败，请检查安装是否完整")?;
    if writeln!(child.stdin.as_mut().unwrap(),"{}",config).is_err(){let _=child.kill();return Err("科研服务启动失败".into());}
    let stdout=child.stdout.take().unwrap();let(tx,rx)=std::sync::mpsc::channel();
    std::thread::spawn(move||{let mut line=String::new();let _=BufReader::new(stdout).read_line(&mut line);let _=tx.send(line);});
    let result=(||{let line=rx.recv_timeout(std::time::Duration::from_secs(600)).map_err(|_|"本地教育科研准备超时，请检查网络后重试")?;
        let value:Value=serde_json::from_str(&line).map_err(|_|"科研服务启动失败")?;
        value["url"].as_str().map(str::to_owned).ok_or_else(||value["error"].as_str().unwrap_or("科研服务连接失败").to_string())})();
    match result {Ok(url)=>{*slot=Some(Service{child,url:url.clone(),control});Ok(url)},Err(e)=>{let _=child.kill();let _=child.wait();Err(e)}}
}
pub fn delete(app:&AppHandle,root:&std::path::Path,path:&str)->Result<Value,String>{
    let _ = root;
    let parts:Vec<_>=path.split('/').collect();
    if parts.len()!=6 || parts[1]!="api" || parts[2]!="review" || !matches!(parts[3],"ppt"|"rebuttal") || parts[4]!="jobs" || parts[5].is_empty() || parts[5].len()>80 || !parts[5].chars().all(|c|c.is_ascii_alphanumeric()||c=='_'||c=='-'){return Err("删除目标无效".into());}
    if !app.dialog().message("删除此科研任务及其生成文件？此操作不可撤销。").title("确认删除").buttons(MessageDialogButtons::OkCancel).blocking_show(){return Err("已取消删除".into());}
    let slot=SERVICE.lock().map_err(|_|"科研服务繁忙")?;let s=slot.as_ref().ok_or("请先打开教育科研")?;
    let mut url=reqwest::Url::parse(&s.url).map_err(|_|"科研服务地址无效")?;url.set_path("/control/delete");url.query_pairs_mut().append_pair("path",path);
    reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(std::time::Duration::from_secs(30)).build().map_err(|_|"删除请求失败")?.post(url).header("X-OMaaX-Control",&s.control).send().map_err(|_|"删除请求失败，请重试")?.json().map_err(|_|"删除结果读取失败".into())
}
pub fn stop(){if let Ok(mut slot)=SERVICE.lock(){if let Some(mut s)=slot.take(){let _=s.child.kill();let _=s.child.wait();}}}

fn direct_model(paths:&crate::paths::AppPaths,shared:&std::path::Path)->Value {
    // Reuse the existing direct provider credential, never the company gateway token.
    let settings=crate::settings::AppSettings::load(paths);
    let mut legacy=paths.clone();legacy.dsh_home=shared.join("dsh-home");
    let key=if !settings.offline_api_key.trim().is_empty(){settings.offline_api_key}
        else {crate::settings::read_credential_value(paths).or_else(||crate::settings::read_credential_value(&legacy)).unwrap_or_default()};
    json!({"API_KEY":key,"BASE_URL":if settings.offline_base_url.is_empty(){"https://api.deepseek.com/v1".to_string()}else{settings.offline_base_url},
        "MODEL":if settings.offline_model.is_empty(){"deepseek-chat".to_string()}else{settings.offline_model}})
}
