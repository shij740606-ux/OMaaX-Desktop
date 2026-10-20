//! Loopback transport credentials are private to the native process and the bundled SDK.
use std::{io::{BufRead, BufReader, Write}, process::{Child, Command, Stdio}, sync::Mutex, time::Duration};
use tauri::AppHandle;
use crate::paths::AppPaths;

struct Bridge { child: Child, url: String, key: String }
impl Drop for Bridge { fn drop(&mut self) { let _=self.child.kill();let _=self.child.wait(); } }
static BRIDGE: Mutex<Option<Bridge>>=Mutex::new(None);

pub fn connection(app:&AppHandle,paths:&AppPaths)->Result<(String,String),String>{
    crate::hub_auth::require(&paths.root,"")?;
    let mut slot=BRIDGE.lock().map_err(|_|"对话连接忙，请重试")?;
    if let Some(b)=slot.as_mut(){if matches!(b.child.try_wait(),Ok(None)){return Ok((b.url.clone(),b.key.clone()));}}
    *slot=None;
    let key=uuid::Uuid::new_v4().to_string();
    let mut cmd=Command::new(crate::paths::node_binary(app));
    cmd.arg(crate::paths::resource(app,"scripts/company-llm.mjs")).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).env_remove("NODE_OPTIONS");
    #[cfg(windows)] {use std::os::windows::process::CommandExt;cmd.creation_flags(0x08000000);}
    let mut child=cmd.spawn().map_err(|_|"无法启动对话连接，请重新打开软件")?;
    let config=serde_json::json!({"key":key,"executable":std::env::current_exe().map_err(|_|"无法启动对话连接")?,"root":paths.root,"accountHome":crate::hub_auth::base()});
    if writeln!(child.stdin.as_mut().unwrap(),"{config}").is_err(){let _=child.kill();let _=child.wait();return Err("无法初始化对话连接".into());}
    let stdout=child.stdout.take().unwrap();let (tx,rx)=std::sync::mpsc::channel();
    std::thread::spawn(move||{let mut line=String::new();let _=BufReader::new(stdout).read_line(&mut line);let _=tx.send(line);});
    let port=rx.recv_timeout(Duration::from_secs(15)).ok().and_then(|s|serde_json::from_str::<serde_json::Value>(&s).ok()).and_then(|v|v["port"].as_u64()).filter(|p|*p>0&&*p<=65535);
    let Some(port)=port else{let _=child.kill();let _=child.wait();return Err("对话连接启动失败，请重试".into());};
    let url=format!("http://127.0.0.1:{port}/v1");
    *slot=Some(Bridge{child,url:url.clone(),key:key.clone()});Ok((url,key))
}
pub fn stop(){if let Ok(mut slot)=BRIDGE.lock(){*slot=None;}}

pub fn modes()->Result<serde_json::Value,String>{
    let (url,key)={
        let slot=BRIDGE.lock().map_err(|_|"对话连接忙，请重试")?;
        let b=slot.as_ref().ok_or("请等待对话服务启动")?;
        (b.url.clone(),b.key.clone())
    };
    ureq::get(&format!("{url}/modes")).set("Authorization",&format!("Bearer {key}"))
        .timeout(Duration::from_secs(35)).call().map_err(|_|"暂时无法获取推理模式，请稍后重试")?
        .into_json().map_err(|_|"推理模式响应无效".into())
}
