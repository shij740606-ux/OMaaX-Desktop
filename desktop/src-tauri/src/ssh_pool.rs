use serde_json::Value;
use std::{collections::HashMap, io::{BufRead, BufReader, Write}, path::Path, process::{Child, ChildStdin, Command, Stdio}, sync::{Arc, Mutex, OnceLock, mpsc}, time::Duration};

struct Worker { child:Child, input:ChildStdin, replies:mpsc::Receiver<String>, _job:crate::child_job::ChildJob }
impl Drop for Worker {fn drop(&mut self){let _=self.child.kill();let _=self.child.wait();}}
type Slot=Arc<Mutex<Option<Worker>>>;
static WORKERS:OnceLock<Mutex<HashMap<String,Slot>>>=OnceLock::new();

fn spawn(node:&Path,script:&Path)->Result<Worker,String>{
    let mut cmd=Command::new(node);cmd.arg(script).arg("--persistent").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)] {use std::os::windows::process::CommandExt;cmd.creation_flags(0x08000000);}
    let mut child=cmd.spawn().map_err(|e|e.to_string())?;
    let job=match crate::child_job::ChildJob::attach(&child){Ok(job)=>job,Err(e)=>{let _=child.kill();let _=child.wait();return Err(e.to_string());}};
    let input=child.stdin.take().ok_or("SSH 输入管道不可用")?;
    let output=child.stdout.take().ok_or("SSH 输出管道不可用")?;
    let (tx,replies)=mpsc::channel();
    std::thread::spawn(move||{for line in BufReader::new(output).lines(){match line{Ok(s) if s.len()<=8*1048576=>{if tx.send(s).is_err(){break;}},_=>break}}});
    Ok(Worker{child,input,replies,_job:job})
}

pub fn request(node:&Path,script:&Path,request:Value)->Result<Value,String>{
    // One in-flight operation per endpoint prevents polling and agents flooding sshd.
    let key=serde_json::json!([node,script,request["host"],request["port"],request["username"]]).to_string();
    let slot={let mut workers=WORKERS.get_or_init(Default::default).lock().map_err(|_|"SSH 连接管理不可用")?;
        workers.entry(key).or_insert_with(||Arc::new(Mutex::new(None))).clone()};
    let mut slot=slot.lock().map_err(|_|"SSH 连接状态不可用")?;
    if slot.as_mut().is_some_and(|w|w.child.try_wait().ok().flatten().is_some()){*slot=None;}
    if slot.is_none(){*slot=Some(spawn(node,script)?);}
    let worker=slot.as_mut().unwrap();
    let mut data=serde_json::to_vec(&request).map_err(|e|e.to_string())?;data.push(b'\n');
    if let Err(e)=worker.input.write_all(&data){*slot=None;return Err(format!("SSH 请求发送中断，请核对任务记录后继续：{e}"));}
    let timeout=request["timeoutSeconds"].as_u64().unwrap_or(30);
    let reply=if timeout==0 {worker.replies.recv().map_err(|_|())}else{worker.replies.recv_timeout(Duration::from_secs(timeout.max(5).saturating_add(15))).map_err(|_|())};
    let reply=match reply{Ok(v)=>v,Err(_)=>{*slot=None;return Err("SSH 响应中断，远端任务可能已提交；请先核对任务状态，不要重复提交".into());}};
    let value:Value=match serde_json::from_str(&reply){Ok(v)=>v,Err(_)=>{*slot=None;return Err("SSH 响应无效，请核对任务状态".into());}};
    if let Some(error)=value["error"].as_str(){return Err(error.to_string());}
    Ok(value)
}

#[cfg(all(test,windows))]
mod tests {
    use super::*;
    #[test]
    fn concurrent_requests_share_worker_and_crash_recreates_it(){
        let manifest=std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let node=manifest.join("../resources/node/win32-x64/node.exe");
        let dir=manifest.join("target/ssh-pool-tests").join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir_all(&dir).unwrap();let script=dir.join("worker.cjs");
        std::fs::write(&script,"require('readline').createInterface({input:process.stdin}).on('line',s=>{const r=JSON.parse(s);if(r.crash)process.exit(1);console.log(JSON.stringify({pid:process.pid,id:r.id}));});").unwrap();
        let handles:Vec<_>=(0..20).map(|id|{let n=node.clone();let s=script.clone();std::thread::spawn(move||{let v=request(&n,&s,serde_json::json!({"host":"test","username":"test","id":id})).unwrap();assert_eq!(v["id"],id);v["pid"].clone()})}).collect();
        let pids:Vec<_>=handles.into_iter().map(|h|h.join().unwrap()).collect();assert!(pids.iter().all(|p|p==&pids[0]));
        assert!(request(&node,&script,serde_json::json!({"host":"test","username":"test","crash":true})).is_err());
        let next=request(&node,&script,serde_json::json!({"host":"test","username":"test","id":21})).unwrap();assert_ne!(next["pid"],pids[0]);
    }
}
