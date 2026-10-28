use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{io::{Read, Write}, process::{Command, Stdio}, time::{Duration, Instant}};
use tauri::AppHandle;

pub(super) fn run(app: &AppHandle, paths: &crate::paths::AppPaths, action: &str, body: &Value, owner: &str) -> Result<Value, String> {
    let kind = match action { "figure_pdf_prepare" => "prepare", "figure_pdf_inspect" => "inspect", "figure_pdf_import" => "import", "figure_fonts" => "fonts", _ => return Err("Unsupported figure action".into()) };
    if body["dataUrl"].as_str().is_some_and(|value| value.len() > 67_000_000) { return Err("PDF 超过 50 MB，请先拆分后重试".into()); }
    let account = format!("{:x}", Sha256::digest(owner.as_bytes()));
    let mut input = body.as_object().cloned().ok_or("Invalid figure request")?;
    input.insert("action".into(), json!(kind));
    input.insert("state".into(), json!(paths.root.join("figure-import").join(account)));
    input.insert("uv".into(), json!(crate::paths::uv_binary(app)));
    input.insert("documents".into(), json!(crate::paths::resource(app, "documents")));
    let payload = serde_json::to_vec(&input).map_err(|_| "Invalid figure request")?;
    if payload.len() > 68_000_000 { return Err("PDF request is too large".into()); }
    let mut command = Command::new(crate::paths::node_binary(app));
    command.arg(crate::paths::resource(app, "scripts").join("figure-pdf-runtime.mjs"))
        .env_remove("NODE_OPTIONS").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x0800_0000); }
    let mut child = command.spawn().map_err(|_| "无法启动 PDF 组件，请检查软件安装是否完整")?;
    let mut stdin = child.stdin.take().ok_or("Cannot send PDF request")?;
    let writer = std::thread::spawn(move || stdin.write_all(&payload));
    let stdout = child.stdout.take().ok_or("Cannot read PDF result")?;
    let stderr = child.stderr.take().ok_or("Cannot read PDF error")?;
    let reader = std::thread::spawn(move || { let mut result = Vec::new(); let _ = stdout.take(65_000_001).read_to_end(&mut result); result });
    let errors = std::thread::spawn(move || { let mut result = Vec::new(); let _ = stderr.take(16_000).read_to_end(&mut result); result });
    let start = Instant::now();
    let timeout = if kind == "fonts" { 30 } else { 1000 };
    loop {
        if child.try_wait().map_err(|_| "PDF 组件状态读取失败")?.is_some() { break; }
        if start.elapsed() > Duration::from_secs(timeout) {
            #[cfg(windows)] { use std::os::windows::process::CommandExt; let _ = Command::new("taskkill.exe").args(["/PID", &child.id().to_string(), "/T", "/F"]).creation_flags(0x0800_0000).stdout(Stdio::null()).stderr(Stdio::null()).status(); }
            let _ = child.kill(); let _ = child.wait();
            return Err("PDF 操作等待超时，请减少页数或稍后重试".into());
        }
        std::thread::sleep(Duration::from_millis(80));
    }
    let _ = writer.join(); let _ = errors.join();
    let output = reader.join().map_err(|_| "PDF 结果读取失败")?;
    if output.len() > 65_000_000 { return Err("PDF 结果过大，请减少页数后重试".into()); }
    let result: Value = serde_json::from_slice(&output).map_err(|_| "PDF 组件返回内容无效，请重试")?;
    if let Some(error) = result["error"].as_str() { return Err(error.to_string()); }
    if crate::hub_auth::require(&paths.root, "")?.id != owner { return Err("UNAUTHORIZED: Account changed".into()); }
    Ok(result)
}
