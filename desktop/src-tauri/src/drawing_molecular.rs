use super::*;
use std::{process::{Command,Stdio},io::Read};
use base64::Engine;

pub(super) fn run(app:&AppHandle,paths:&crate::paths::AppPaths,body:&Value)->Result<Value,String>{
 let protein=text(body,"protein",4_000_000)?;
 let ligand=text(body,"ligand",1_000_000)?;
 let payload=serde_json::to_vec(&json!({"protein":protein,"ligand":ligand,"compress":!body["server"].as_str().unwrap_or("").is_empty()})).map_err(|_|"结构内容无效")?;
 let script=include_str!("../../resources/ssh/molecular_interactions.py");
 let server=body["server"].as_str().unwrap_or("");
 let python=body["python"].as_str().unwrap_or("").trim();
 if python.len()>8192 || python.contains('\0') {return Err("环境路径无效".into());}
 let start=std::time::Instant::now();
 let result=(||{
   let stdout=if !server.is_empty(){
     let code=format!("import sys,io,base64\nsys.stdin=io.StringIO(base64.b64decode('{}').decode('utf-8'))\n{}",base64::engine::general_purpose::STANDARD.encode(&payload),script);
     let executable=if python.is_empty(){"python3"}else{python};
     let command=format!("{} - <<'OMAAX_MOLECULAR_INPUT'\n{}\nOMAAX_MOLECULAR_INPUT",shell_quote(executable),code);
     let value=crate::ssh_hosts::call(&paths.root,&crate::paths::node_binary(app),&crate::paths::resource(app,"ssh").join("client.cjs"),server,"execute",&command,900,false)?;
     if let Some(error)=value["error"].as_str(){return Err(error.to_string());}
     let output=value["stdout"].as_str().unwrap_or("").to_string();
     if output.trim().is_empty(){return Err("服务器未返回结果。请检查所选 Python 环境已安装 prolif==2.1.0。".into());}
     output
   }else{
     let mut command=if python.is_empty(){
       let mut c=Command::new(crate::paths::uv_binary(app));
       c.args(["run","--no-project","--python","3.12","--with","prolif==2.1.0","--with","rdkit==2025.9.6","python"]);c
     }else{Command::new(python)};
     command.args(["-c",script]).env("PYTHONIOENCODING","utf-8").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
     #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x0800_0000);}
     let mut child=command.spawn().map_err(|_|"无法启动分析环境，请检查 Python 路径或软件安装")?;
     let mut input=child.stdin.take().ok_or("无法传入结构")?;
     let writer=std::thread::spawn(move||input.write_all(&payload));
     let out=child.stdout.take().ok_or("无法读取分析输出")?;
     let err=child.stderr.take().ok_or("无法读取分析日志")?;
     let reader=std::thread::spawn(move||{let mut data=String::new();let _=out.take(4_000_001).read_to_string(&mut data);data});
     let errors=std::thread::spawn(move||{let mut data=String::new();let _=err.take(500_000).read_to_string(&mut data);data});
     loop{
       if child.try_wait().map_err(|_|"无法读取分析状态")?.is_some(){break;}
       if start.elapsed()>Duration::from_secs(900){let _=child.kill();let _=child.wait();return Err("计算等待超时，请检查环境进程后再重试，避免重复提交".into());}
       std::thread::sleep(Duration::from_millis(100));
     }
     let _=writer.join();let output=reader.join().map_err(|_|"分析输出读取失败")?;
     let errors=errors.join().unwrap_or_default();
     if output.is_empty(){return Err(format!("分析环境未能运行：{}",errors.chars().rev().take(1200).collect::<String>().chars().rev().collect::<String>()));}
     if output.len()>4_000_000{return Err("结果过大，请减少姿势数".into());}output
   };
   let value=decode_result(&stdout)?;
   if let Some(error)=value["error"].as_str(){return Err(format!("相互作用分析未完成：{error}"));}
   if value["format"]!="omaax-interactions"{return Err("分析结果格式无效".into());}
   Ok(value)
 })();
 // Log operation and outcome, never model credentials or raw molecular content.
 let audit=json!({"time":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs(),"action":"molecular_interactions","server":server,"success":result.is_ok(),"seconds":start.elapsed().as_secs()});
 let _=fs::create_dir_all(&paths.logs);
 if let Ok(mut file)=fs::OpenOptions::new().create(true).append(true).open(paths.logs.join("drawing-audit.jsonl")){let _=writeln!(file,"{audit}");}
 result
}
fn shell_quote(s:&str)->String{format!("'{}'",s.replace('\'',"'\\''"))}
fn decode_result(output:&str)->Result<Value,String>{
 let mut value:Value=serde_json::from_str(output.trim()).map_err(|_|"分析结果不完整，请检查环境后重试")?;
 if let Some(encoded)=value["compressed"].as_str(){
  let bytes=base64::engine::general_purpose::STANDARD.decode(encoded).map_err(|_|"结果传输不完整")?;
  let mut data=String::new();flate2::read::GzDecoder::new(bytes.as_slice()).take(4_000_001).read_to_string(&mut data).map_err(|_|"结果解压失败")?;
  if data.len()>4_000_000{return Err("结果过大，请减少姿势数".into());}
  value=serde_json::from_str(&data).map_err(|_|"结果内容无效")?;
 }
 Ok(value)
}
#[cfg(test)]mod tests{
 use super::*;
 #[test]fn remote_executable_is_one_shell_argument(){assert_eq!(shell_quote("/home/a b/python"),"'/home/a b/python'");assert_eq!(shell_quote("x'; touch /tmp/a; '"),"'x'\\''; touch /tmp/a; '\\'''");}
 #[test]fn compressed_results_roundtrip_and_reject_corruption(){let mut encoder=flate2::write::GzEncoder::new(Vec::new(),flate2::Compression::default());encoder.write_all(br#"{"format":"omaax-interactions"}"#).unwrap();let payload=json!({"compressed":base64::engine::general_purpose::STANDARD.encode(encoder.finish().unwrap())});assert_eq!(decode_result(&payload.to_string()).unwrap()["format"],"omaax-interactions");assert!(decode_result(r#"{"compressed":"bad"}"#).is_err());}
}

