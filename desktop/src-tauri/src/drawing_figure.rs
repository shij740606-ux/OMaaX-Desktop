//! Native services for OMaaX's independently implemented figure composition editor.
use quick_xml::{events::{BytesStart, Event}, Reader, Writer, XmlVersion};
use serde_json::{json, Value};
use std::{collections::HashMap, fs, io::{Read, Write}, path::Path,
    sync::{Arc, LazyLock, Mutex, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

const MAX_SVG: usize = 64_000_000;
const MAX_RASTER_URL: usize = 86_000_000;
const CANCELLED: &str = "FIGURE_CANCELLED";
struct Request { owner: String, flag: Arc<AtomicBool>, started: bool, created: Instant }
static REQUESTS: LazyLock<Mutex<HashMap<String, Request>>> = LazyLock::new(|| Mutex::new(HashMap::new()));
struct RequestGuard { key: String, flag: Arc<AtomicBool> }
impl Drop for RequestGuard {
    fn drop(&mut self) {
        if let Ok(mut requests) = REQUESTS.lock() {
            if requests.get(&self.key).is_some_and(|r| Arc::ptr_eq(&r.flag, &self.flag)) { requests.remove(&self.key); }
        }
    }
}
fn message(en: bool, zh: &str, english: &str) -> String { if en { english.into() } else { zh.into() } }
fn localize_error(error: String, en: bool) -> String {
    if error == CANCELLED { return error; }
    if error.starts_with("UNAUTHORIZED:") {
        return message(en, "UNAUTHORIZED: 登录状态已变化，请重新登录后重试", "UNAUTHORIZED: Your sign-in has changed. Sign in again and retry.");
    }
    let messages = [
        ("Invalid figure request ID", "请求标识无效，请重新操作"),
        ("Figure request state is unavailable", "当前请求状态不可用，请重试"),
        ("Figure request is already running", "当前绘图请求正在处理中"),
        ("Invalid or oversized SVG", "图像内容无效或超过 64 MB"),
        ("Invalid SVG XML", "SVG 文件内容无效"),
        ("Expected one SVG root", "请选择包含完整画布的 SVG 文件"),
        ("Executable SVG content is not supported", "图中包含可执行内容，请另存为静态 SVG 后重试"),
        ("Executable SVG attributes are not supported", "图中包含可执行内容，请另存为静态 SVG 后重试"),
        ("Invalid SVG attribute", "SVG 图形属性无效"),
        ("External SVG resources are not supported; embed images first", "图中引用了外部图片，请先将图片嵌入 SVG 后重试"),
        ("SVG declarations and external entities are not supported", "请将图像另存为不引用外部资源的静态 SVG 后重试"),
        ("Encoded SVG styles are not supported", "图中文字样式暂不支持，请简化样式后重试"),
        ("External SVG styles are not supported", "图中引用了外部样式，请将样式嵌入图中后重试"),
        ("External or executable SVG styles are not supported", "图中包含外部或可执行样式，请另存为静态 SVG 后重试"),
        ("Invalid SVG nesting", "SVG 图形结构不完整"),
        ("Text outside SVG root", "SVG 图形结构无效"),
        ("SVG root is missing or incomplete", "SVG 画布缺失或不完整"),
        ("Invalid SVG style", "SVG 图形样式无效"),
        ("Figure project is too large", "拼图项目超过 64 MB，请减少图片数量或分辨率"),
        ("Invalid figure project", "拼图项目文件无效"),
        ("Unsupported figure project", "这不是支持的论文拼图项目文件"),
        ("Exported image is too large", "导出图片超过 64 MB，请降低分辨率"),
        ("Invalid image format", "图片格式无效"),
        ("Cannot open figure file", "无法打开绘图文件"),
        ("Cannot read figure file", "无法读取绘图文件"),
        ("Figure file is too large", "绘图文件超过 64 MB"),
        ("Select a local project folder", "请选择有效的本地项目文件夹"),
        ("Cannot access project folder", "无法访问项目文件夹"),
        ("Cannot create figure version", "无法在项目中新建拼图文件夹"),
        ("Invalid SVG image attribute", "SVG 中的图片属性无效"),
        ("Cannot prepare SVG images", "无法处理 SVG 中的图片"),
        ("Invalid SVG encoding", "SVG 文件编码无效"),
        ("The model changed an original image reference; your current figure is unchanged", "模型返回的图片引用无效，原图已保留，请重试"),
        ("The model did not return an SVG", "模型未返回有效图形，原图已保留，请重试"),
        ("The model returned an incomplete SVG", "模型返回的图形不完整，原图已保留，请重试"),
        ("The model made no further progress; your current figure is unchanged", "模型未能继续生成，原图已保留，请调整要求后重试"),
        ("The model restarted its SVG instead of continuing; your current figure is unchanged", "模型续接内容不一致，原图已保留，请重试"),
        ("The generated figure exceeds the document size limit", "生成的图超过 64 MB，原图已保留，请简化绘图要求"),
        ("Invalid figure request", "绘图请求无效，请重试"),
        ("This figure exceeds the model's request size; edit a smaller selection or simplify the figure", "所选内容超过模型可接收的大小，请选择更小的图层或简化图形"),
        ("Cannot connect to model service", "无法连接模型服务，请稍后重试"),
        ("Cannot read model capabilities", "无法获取模型服务信息，请稍后重试"),
        ("Model service is unavailable; check your account", "模型服务暂不可用，请检查账号状态后重试"),
        ("Invalid model capabilities", "模型服务信息无效，请稍后重试"),
        ("Figure conversation is too large", "绘图修改记录过长，请清理记录后重试"),
        ("Enter a figure editing request", "请填写绘图修改要求"),
        ("Figure model connection interrupted", "绘图服务连接中断，原图已保留，请重试"),
        ("Figure model service is unavailable; your current figure is unchanged", "绘图服务暂不可用，原图已保留，请稍后重试"),
        ("Incomplete model response", "模型回复不完整，原图已保留，请重试"),
        ("The model did not return figure content", "未收到模型生成的图形，原图已保留，请重试"),
        ("Unsupported export format", "不支持此导出格式"),
        ("Invalid export folder", "保存位置无效"),
        ("The file exists or cannot be saved; choose another filename", "文件已存在或无法保存，请换一个文件名"),
        ("Unsupported figure action", "不支持此拼图操作"),
        ("Invalid drawing content", "绘图内容无效"),
        ("Drawing content exceeds the size limit", "绘图内容过大"),
        ("Cannot create a new drawing file", "无法新建绘图文件"),
        ("The file could not be completely saved", "文件保存未完成"),
        ("Choose a complete local file or folder path", "请选择完整的本机文件或文件夹路径"),
        ("The selected path does not exist or is inaccessible", "所选路径不存在或无法访问"),
        ("The model connection is busy. Retry shortly.", "对话连接忙，请重试"),
        ("Cannot start the model connection. Reopen the app.", "无法启动对话连接，请重新打开软件"),
        ("Cannot start the model connection", "无法启动对话连接"),
        ("Cannot initialize the model connection", "无法初始化对话连接"),
        ("Cannot start the model connection. Retry shortly.", "对话连接启动失败，请重试"),
    ];
    if let Some((english, chinese)) = messages.iter().find(|(english, chinese)| error == *english || error == *chinese) {
        return if en { (*english).into() } else { (*chinese).into() };
    }
    if en && error.chars().any(|c| ('\u{3400}'..='\u{9fff}').contains(&c)) {
        return "The operation could not be completed. Check your sign-in and folder access, then retry.".into();
    }
    if !en && error.is_ascii() { return "操作未完成，请检查登录状态及保存位置后重试".into(); }
    error
}
fn request_key(body: &Value) -> Result<String, String> {
    uuid::Uuid::parse_str(body["requestId"].as_str().unwrap_or("")).map(|id| id.to_string()).map_err(|_| "Invalid figure request ID".into())
}
fn begin(key: String, owner: &str) -> Result<RequestGuard, String> {
    let mut requests = REQUESTS.lock().map_err(|_| "Figure request state is unavailable")?;
    requests.retain(|_, r| r.started || r.created.elapsed() < Duration::from_secs(600));
    if let Some(r) = requests.get(&key) {
        if r.owner != owner { return Err("UNAUTHORIZED: Figure request belongs to another account".into()); }
        if r.flag.load(Ordering::SeqCst) { return Err(CANCELLED.into()); }
        return Err("Figure request is already running".into());
    }
    let flag = Arc::new(AtomicBool::new(false));
    requests.insert(key.clone(), Request { owner: owner.into(), flag: flag.clone(), started: true, created: Instant::now() });
    Ok(RequestGuard { key, flag })
}
fn cancel(key: String, owner: &str) -> Result<Value, String> {
    let mut requests = REQUESTS.lock().map_err(|_| "Figure request state is unavailable")?;
    requests.retain(|_, r| r.started || r.created.elapsed() < Duration::from_secs(600));
    if let Some(r) = requests.get(&key) {
        if r.owner != owner { return Err("UNAUTHORIZED: Figure request belongs to another account".into()); }
        r.flag.store(true, Ordering::SeqCst);
    } else {
        // Preserve an early stop until the concurrently dispatched generate call registers.
        requests.insert(key, Request { owner: owner.into(), flag: Arc::new(AtomicBool::new(true)), started: false, created: Instant::now() });
    }
    Ok(json!({"ok": true}))
}
async fn cancellable<T>(work: impl std::future::Future<Output = Result<T, String>>, flag: &Arc<AtomicBool>) -> Result<T, String> {
    let mut work = std::pin::pin!(work);
    let mut tick = tokio::time::interval(Duration::from_millis(50));
    std::future::poll_fn(|cx| {
        use std::task::Poll;
        if flag.load(Ordering::SeqCst) { return Poll::Ready(Err(CANCELLED.into())); }
        while tick.poll_tick(cx).is_ready() {}
        work.as_mut().poll(cx)
    }).await
}

fn valid_svg(svg: &str) -> Result<(), String> {
    if svg.len() > MAX_SVG || svg.trim().is_empty() { return Err("Invalid or oversized SVG".into()); }
    let mut reader = Reader::from_str(svg);
    let mut depth = 0usize;
    let mut roots = 0usize;
    let mut style_depth = None;
    let mut style_text = String::new();
    loop {
        let event = reader.read_event().map_err(|_| "Invalid SVG XML")?;
        match &event {
            Event::Start(e) | Event::Empty(e) => {
                let name = e.local_name().as_ref().to_ascii_lowercase();
                if depth == 0 { roots += 1; if name != "svg" || roots != 1 { return Err("Expected one SVG root".into()); } }
                if ["script", "foreignobject", "iframe", "object", "embed", "audio", "video", "link", "set", "animate", "animatetransform", "animatemotion"].contains(&name.as_str()) {
                    return Err("Executable SVG content is not supported".into());
                }
                for attr in e.attributes() {
                    let attr = attr.map_err(|_| "Invalid SVG attribute")?;
                    let key = attr.key.local_name().as_ref().to_ascii_lowercase();
                    let value = attr.normalized_value(XmlVersion::Implicit1_0).map_err(|_| "Invalid SVG attribute")?;
                    if key.starts_with("on") || key == "base" { return Err("Executable SVG attributes are not supported".into()); }
                    if key == "href" && !value.is_empty() && !value.starts_with('#') && !(name == "image" && (value.starts_with("data:image/png;base64,") || value.starts_with("data:image/jpeg;base64,") || value.starts_with("data:image/webp;base64,") || value.starts_with("data:image/gif;base64,"))) {
                        return Err("External SVG resources are not supported; embed images first".into());
                    }
                    if key == "style" || value.to_ascii_lowercase().contains("url(") { valid_css(&value)?; }
                }
                if matches!(event, Event::Start(_)) { depth += 1; if name == "style" { style_depth = Some(depth); style_text.clear(); } }
            }
            Event::End(_) => { if style_depth == Some(depth) { valid_css(&style_text)?; style_depth = None; } depth = depth.checked_sub(1).ok_or("Invalid SVG nesting")?; }
            Event::DocType(_) | Event::PI(_) => return Err("SVG declarations and external entities are not supported".into()),
            Event::Text(e) => {
                let text = e.xml10_content();
                if style_depth.is_some() { style_text.push_str(&text); }
                if depth == 0 && !text.trim().is_empty() { return Err("Text outside SVG root".into()); }
            }
            Event::CData(e) => { if style_depth.is_some() { style_text.push_str(&e.xml10_content()); } }
            Event::GeneralRef(_) if style_depth.is_some() => return Err("Encoded SVG styles are not supported".into()),
            Event::Eof => break,
            _ => {}
        }
    }
    if roots != 1 || depth != 0 { return Err("SVG root is missing or incomplete".into()); }
    Ok(())
}
fn valid_css(value: &str) -> Result<(), String> {
    let lower = value.to_ascii_lowercase();
    if lower.contains("@import") || lower.contains("javascript:") || lower.contains("expression(") || lower.contains("\\") {
        return Err("External or executable SVG styles are not supported".into());
    }
    let mut rest = lower.as_str();
    while let Some(index) = rest.find("url(") {
        rest = &rest[index + 4..];
        let target = rest.trim_start().trim_start_matches(['\'', '"']);
        if !target.starts_with('#') { return Err("External SVG styles are not supported".into()); }
        rest = rest.split_once(')').ok_or("Invalid SVG style")?.1;
    }
    Ok(())
}

fn figure_document(text: &str) -> Result<Value, String> {
    if text.len() > MAX_SVG { return Err("Figure project is too large".into()); }
    let doc: Value = serde_json::from_str(text).map_err(|_| "Invalid figure project")?;
    if doc["format"] != "omaax-figure" || doc["version"] != 1 || !doc["items"].is_array() { return Err("Unsupported figure project".into()); }
    Ok(doc)
}
fn raster(data: &str, format: &str) -> Result<Vec<u8>, String> {
    use base64::Engine;
    if data.len() > MAX_RASTER_URL { return Err("Exported image is too large".into()); }
    let prefix = match format { "png" => "data:image/png;base64,", "jpg" => "data:image/jpeg;base64,", _ => return Err("Invalid image format".into()) };
    let bytes = base64::engine::general_purpose::STANDARD.decode(data.strip_prefix(prefix).ok_or("Invalid image format")?).map_err(|_| "Invalid image format")?;
    if bytes.len() > MAX_SVG { return Err("Exported image is too large".into()); }
    if (format == "png" && !bytes.starts_with(b"\x89PNG\r\n\x1a\n")) || (format == "jpg" && !bytes.starts_with(b"\xff\xd8\xff")) { return Err("Invalid image format".into()); }
    Ok(bytes)
}
fn read_text(path: &Path) -> Result<String, String> {
    let mut text = String::new();
    fs::File::open(path).map_err(|_| "Cannot open figure file")?.take((MAX_SVG + 1) as u64).read_to_string(&mut text).map_err(|_| "Cannot read figure file")?;
    if text.len() > MAX_SVG { return Err("Figure file is too large".into()); }
    Ok(text)
}
fn save_version(project: &str, document: &str, svg: &str, png: Option<&[u8]>) -> Result<Value, String> {
    crate::files::validate_local_path(project)?;
    if !Path::new(project).is_dir() { return Err("Select a local project folder".into()); }
    figure_document(document)?; valid_svg(svg)?;
    let target = fs::canonicalize(project).map_err(|_| "Cannot access project folder")?.join(format!("OMaaX-Figure-{}", uuid::Uuid::new_v4()));
    fs::create_dir(&target).map_err(|_| "Cannot create figure version")?;
    super::new_file(&target.join("figure.omaax.json"), document.as_bytes())?;
    super::new_file(&target.join("figure.svg"), svg.as_bytes())?;
    if let Some(image) = png { super::new_file(&target.join("figure.png"), image)?; }
    Ok(json!({"path": target.to_string_lossy()}))
}
fn audit(paths: &crate::paths::AppPaths, owner: &str, action: &str, result: &Result<Value, String>) {
    if fs::create_dir_all(&paths.logs).is_err() { return; }
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(paths.logs.join("drawing-figure.jsonl")) {
        let path = result.as_ref().ok().and_then(|v| v["path"].as_str());
        let _ = writeln!(file, "{}", json!({"time":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs(),"userId":owner,"action":action,"ok":result.is_ok(),"path":path}));
    }
}

// Images remain byte-for-byte local; the model only needs their position, size and labels.
fn map_image_hrefs(svg: &str, mut replace: impl FnMut(&str) -> Result<String, String>) -> Result<String, String> {
    let mut reader = Reader::from_str(svg);
    let mut writer = Writer::new(Vec::new());
    loop {
        let event = reader.read_event().map_err(|_| "Invalid SVG XML")?;
        let empty = matches!(event, Event::Empty(_));
        match &event {
            Event::Start(element) | Event::Empty(element) if element.local_name().as_ref() == "image" => {
                let name = element.name().as_ref().to_string();
                let mut replacement = BytesStart::new(name);
                for attr in element.attributes() {
                    let attr = attr.map_err(|_| "Invalid SVG image attribute")?;
                    let key = attr.key.as_ref().to_string();
                    let value = attr.normalized_value(XmlVersion::Implicit1_0).map_err(|_| "Invalid SVG image attribute")?;
                    let value = if attr.key.local_name().as_ref() == "href" { replace(&value)? } else { value.into_owned() };
                    replacement.push_attribute((key.as_str(), value.as_str()));
                }
                writer.write_event(if empty { Event::Empty(replacement) } else { Event::Start(replacement) }).map_err(|_| "Cannot prepare SVG images")?;
            }
            Event::Eof => break,
            _ => writer.write_event(event).map_err(|_| "Cannot prepare SVG images")?,
        }
    }
    String::from_utf8(writer.into_inner()).map_err(|_| "Invalid SVG encoding".into())
}
fn image_placeholders(svg: &str) -> Result<(String, Vec<(String, String)>), String> {
    let mut images = Vec::new();
    let compact = map_image_hrefs(svg, |value| {
        if value.starts_with("data:image/") {
            let placeholder = format!("#omaax-embedded-{}", uuid::Uuid::new_v4());
            images.push((placeholder.clone(), value.to_string())); Ok(placeholder)
        } else { Ok(value.to_string()) }
    })?;
    Ok((compact, images))
}
fn restore_images(svg: &str, images: &[(String, String)]) -> Result<String, String> {
    map_image_hrefs(svg, |value| {
        if value.starts_with("#omaax-embedded-") {
            images.iter().find(|(key, _)| key == value).map(|(_, data)| data.clone()).ok_or_else(|| "The model changed an original image reference; your current figure is unchanged".into())
        } else { Ok(value.to_string()) }
    })
}
fn extract_svg(value: &str) -> Result<String, String> {
    let start = value.find("<svg").ok_or("The model did not return an SVG")?;
    let end = value.rfind("</svg>").map(|i| i + 6).or_else(|| value[start..].trim_end().ends_with("/>").then_some(value.trim_end().len())).ok_or("The model returned an incomplete SVG")?;
    let svg = value[start..end].trim().to_string(); valid_svg(&svg)?; Ok(svg)
}
fn append_output(output: &mut String, part: &str) -> Result<(), String> {
    if part.trim().is_empty() || output.ends_with(part) { return Err("The model made no further progress; your current figure is unchanged".into()); }
    if !output.is_empty() && part.trim_start().starts_with("<svg") { return Err("The model restarted its SVG instead of continuing; your current figure is unchanged".into()); }
    if output.len() + part.len() > MAX_SVG { return Err("The generated figure exceeds the document size limit".into()); }
    output.push_str(part); Ok(())
}
fn model_payload(messages: &[Value], max_output: Option<u64>, max_bytes: usize) -> Result<Value, String> {
    let mut payload = json!({"model":"omaax","messages":messages,"stream":false});
    if let Some(limit) = max_output.filter(|v| *v > 0) { payload["max_tokens"] = json!(limit); }
    if serde_json::to_vec(&payload).map_err(|_| "Invalid figure request")?.len() > max_bytes { return Err("This figure exceeds the model's request size; edit a smaller selection or simplify the figure".into()); }
    Ok(payload)
}
async fn model_svg(base: &str, key: &str, body: &Value, flag: &Arc<AtomicBool>, owner_check: impl Fn() -> Result<(), String>) -> Result<Value, String> {
    let client = reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(15)).timeout(Duration::from_secs(660)).build().map_err(|_| "Cannot connect to model service")?;
    let limits = cancellable(async {
        let response = client.get(format!("{base}/modes")).bearer_auth(key).send().await.map_err(|_| "Cannot read model capabilities")?;
        if !response.status().is_success() { return Err("Model service is unavailable; check your account".into()); }
        response.json::<Value>().await.map_err(|_| "Invalid model capabilities".into())
    }, flag).await?;
    let max_output = limits["maxOutputTokens"].as_u64();
    let max_bytes = limits["maxRequestBytes"].as_u64().filter(|v| *v > 0).unwrap_or(1024 * 1024) as usize;
    let svg = body["svg"].as_str().unwrap_or("");
    if !svg.trim().is_empty() { valid_svg(svg)?; }
    let (compact, images) = image_placeholders(svg)?;
    let mut messages = vec![json!({"role":"system","content":"You are the OMaaX scientific figure editor. Return only one complete static SVG, without prose or code fences. Preserve source scientific values, axes, labels and plot contents unless the user explicitly supplies a correction. Never invent measurements or results. Keep existing panels, vector paths, editable text, original element IDs and grouping unless asked to change them. Embedded raster href values beginning #omaax-embedded- are opaque original images: preserve each token exactly; never redraw or fabricate their contents. Perform layout, typography, colour, panel-label and annotation edits. Use only static SVG elements, embedded raster data or local #references: no scripts, event handlers, HTML, animation, external links or fonts. Set an explicit viewBox. On continuation return only the missing suffix, without repeating completed XML."})];
    if let Some(history) = body["history"].as_array() {
        if history.len() > 100 || body["history"].to_string().len() > 160_000 { return Err("Figure conversation is too large".into()); }
        for item in history {
            if matches!(item["role"].as_str(), Some("user" | "assistant")) {
                if let Some(content) = item["content"].as_str() { messages.push(json!({"role":item["role"],"content":content})); }
            }
        }
    }
    let prompt = super::text(body, "prompt", 16_000)?;
    if prompt.trim().is_empty() { return Err("Enter a figure editing request".into()); }
    messages.push(json!({"role":"user","content":format!("Current figure:\n{compact}\nRequested changes:\n{prompt}")}));
    let mut output = String::new();
    loop {
        if flag.load(Ordering::SeqCst) { return Err(CANCELLED.into()); }
        owner_check()?;
        let payload = model_payload(&messages, max_output, max_bytes)?;
        let value = cancellable(async {
            let response = client.post(format!("{base}/chat/completions")).bearer_auth(key).json(&payload).send().await.map_err(|_| "Figure model connection interrupted")?;
            if !response.status().is_success() { return Err("Figure model service is unavailable; your current figure is unchanged".into()); }
            response.json::<Value>().await.map_err(|_| "Incomplete model response".into())
        }, flag).await?;
        if flag.load(Ordering::SeqCst) { return Err(CANCELLED.into()); }
        owner_check()?;
        let choice = &value["choices"][0];
        let content = choice["message"]["content"].as_str().ok_or("The model did not return figure content")?;
        append_output(&mut output, content)?;
        let complete = extract_svg(&output);
        if choice["finish_reason"] != "length" || complete.is_ok() {
            let result = restore_images(&complete?, &images)?;
            valid_svg(&result)?;
            if flag.load(Ordering::SeqCst) { return Err(CANCELLED.into()); }
            owner_check()?;
            return Ok(json!({"svg":result,"text":result}));
        }
        // No step/continuation cap: continue while the provider produces new content.
        messages.push(json!({"role":"assistant","content":content}));
        messages.push(json!({"role":"user","content":"Continue exactly where the SVG was truncated. Return only the remaining suffix; do not repeat existing XML or restart the SVG."}));
    }
}

pub fn run(app: &AppHandle, paths: &crate::paths::AppPaths, action: &str, body: Value) -> Result<Value, String> {
    let en = body["lang"].as_str() == Some("en");
    let user = crate::hub_auth::require(&paths.root, "").map_err(|error| localize_error(error, en))?;
    let result = (|| {
        match action {
            "figure_cancel" => cancel(request_key(&body)?, &user.id),
            "figure_generate" => {
                let guard = begin(request_key(&body)?, &user.id)?;
                if guard.flag.load(Ordering::SeqCst) { return Err(CANCELLED.into()); }
                let (base, key) = crate::company_llm::connection(app, paths)?;
                tauri::async_runtime::block_on(model_svg(&base, &key, &body, &guard.flag, || {
                    if crate::hub_auth::require(&paths.root, "")?.id != user.id { return Err("UNAUTHORIZED: Account changed; generate again".into()); }
                    Ok(())
                }))
            }
            "figure_save" => {
                let project = super::text(&body, "project", 8192)?;
                let doc = super::text(&body, "document", MAX_SVG)?;
                let svg = super::text(&body, "svg", MAX_SVG)?;
                let png = body["png"].as_str().map(|v| raster(v, "png")).transpose()?;
                save_version(project, doc, svg, png.as_deref())
            }
            "figure_open" => {
                let Some(file) = app.dialog().file().add_filter(if en { "Figures" } else { "论文图与拼图项目" }, &["svg", "json"]).blocking_pick_file() else { return Ok(Value::Null); };
                let path = file.to_string(); crate::files::validate_local_path(&path)?;
                let text = read_text(Path::new(&path))?;
                let name = Path::new(&path).file_name().unwrap_or_default().to_string_lossy();
                if path.to_ascii_lowercase().ends_with(".json") { Ok(json!({"name":name,"document":figure_document(&text)?})) }
                else { Ok(json!({"name":name,"svg":text})) } // The editor sanitizes imported SVG before displaying it.
            }
            "figure_export" => {
                let format = super::text(&body, "format", 8)?;
                let bytes = match format {
                    "svg" => { let svg = super::text(&body, "svg", MAX_SVG)?; valid_svg(svg)?; svg.as_bytes().to_vec() }
                    "png" | "jpg" => raster(super::text(&body, "dataUrl", MAX_RASTER_URL)?, format)?,
                    _ => return Err(message(en, "不支持此导出格式", "Unsupported export format")),
                };
                let Some(file) = app.dialog().file().set_file_name(format!("OMaaX-Figure.{format}")).add_filter(if en { "Images" } else { "图片" }, &[format]).blocking_save_file() else { return Ok(Value::Null); };
                let target = file.to_string();
                let parent = Path::new(&target).parent().ok_or("Invalid export folder")?;
                crate::files::validate_local_path(&parent.to_string_lossy())?;
                // Never overwrite an input SVG or an existing result, including symbolic links.
                super::new_file(Path::new(&target), &bytes).map_err(|_| message(en, "文件已存在或无法保存，请换一个文件名", "The file exists or cannot be saved; choose another filename"))?;
                Ok(json!({"path":target}))
            }
            _ => Err("Unsupported figure action".into()),
        }
    })();
    audit(paths, &user.id, action, &result);
    result.map_err(|error| localize_error(error, en))
}

#[cfg(test)]
mod tests {
    use super::*;
    const SVG: &str = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80"><defs><clipPath id="c"><rect width="100" height="80"/></clipPath></defs><g clip-path="url(#c)"><text x="3" y="14">value &amp; label</text><path d="M0 0L10 10"/></g></svg>"##;
    #[test]
    fn static_svg_preserves_vectors_and_rejects_active_or_external_content() {
        assert!(valid_svg(SVG).is_ok());
        assert!(valid_svg("<svg/>").is_ok());
        assert!(valid_svg("<svg><image href='data:image/gif;base64,R0lGODlh'/></svg>").is_ok());
        for svg in [
            "<svg><script>alert(1)</script></svg>", "<svg onload='alert(1)'/>",
            "<svg><foreignObject/></svg>", "<svg><image href='https://example.org/a.png'/></svg>",
            "<svg><image href='&#x6a;avascript:alert(1)'/></svg>", "<svg><animate attributeName='href'/></svg>",
            "<svg><style>@import 'https://example.org/x.css';</style></svg>",
            "<svg><path fill='url(https://example.org/x)'/></svg>",
            "<!DOCTYPE svg [<!ENTITY x SYSTEM 'file:///secret'>]><svg>&x;</svg>",
            "<svg/><svg/>", "<svg><g>", "<svg></g>", "<html/>",
        ] { assert!(valid_svg(svg).is_err(), "accepted {svg}"); }
    }
    #[test]
    fn errors_follow_language_without_losing_control_codes() {
        assert_eq!(localize_error(CANCELLED.into(), false), CANCELLED);
        assert!(localize_error("UNAUTHORIZED: Account changed".into(), false).starts_with("UNAUTHORIZED:"));
        assert_eq!(localize_error("Invalid image format".into(), false), "图片格式无效");
        assert_eq!(localize_error("所选路径不存在或无法访问".into(), true), "The selected path does not exist or is inaccessible");
    }
    #[test]
    fn embedded_rasters_are_not_sent_to_the_model_and_roundtrip_exactly() {
        let source = r#"<svg><image href="data:image/png;base64,aGVsbG8=" width="20"/><image href='data:image/jpeg;base64,bXk=' width='30'/></svg>"#;
        let (compact, replacements) = image_placeholders(source).unwrap();
        assert!(!compact.contains("base64")); assert_eq!(replacements.len(), 2);
        assert!(valid_svg(&compact).is_ok());
        let restored = restore_images(&compact, &replacements).unwrap();
        assert!(restored.contains("data:image/png;base64,aGVsbG8="));
        assert!(restored.contains("data:image/jpeg;base64,bXk="));
        assert!(restore_images(r##"<svg><image href="#omaax-embedded-unknown"/></svg>"##, &replacements).is_err());
    }
    #[test]
    fn saving_new_versions_preserves_original_and_editable_document() {
        let root = std::env::temp_dir().join(format!("omaax-figure-test-{}", uuid::Uuid::new_v4())); fs::create_dir(&root).unwrap();
        fs::write(root.join("original.svg"), SVG).unwrap();
        let doc = r#"{"format":"omaax-figure","version":1,"title":"Two panels","width":100,"height":80,"items":[{"id":"panel-a","svg":"original","x":12}]}"#;
        let a = save_version(root.to_str().unwrap(), doc, SVG, Some(b"png")).unwrap();
        let b = save_version(root.to_str().unwrap(), doc, SVG, None).unwrap();
        assert_ne!(a["path"], b["path"]);
        for saved in [&a, &b] {
            let folder = Path::new(saved["path"].as_str().unwrap());
            assert_eq!(read_text(&folder.join("figure.omaax.json")).unwrap(), doc);
            assert_eq!(read_text(&folder.join("figure.svg")).unwrap(), SVG);
        }
        assert_eq!(read_text(&root.join("original.svg")).unwrap(), SVG);
        assert!(super::super::new_file(&root.join("original.svg"), b"replaced").is_err());
        assert_eq!(read_text(&root.join("original.svg")).unwrap(), SVG);
        assert!(figure_document(r#"{"format":"omaax-drawing","version":1,"items":[]}"#).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn output_budget_comes_from_model_and_unknown_limit_is_omitted() {
        let messages = vec![json!({"role":"user","content":"layout"})];
        assert_eq!(model_payload(&messages, Some(131_072), 1000).unwrap()["max_tokens"], 131_072);
        assert!(model_payload(&messages, None, 1000).unwrap().get("max_tokens").is_none());
        assert!(model_payload(&messages, Some(0), 1000).unwrap().get("max_tokens").is_none());
        assert!(model_payload(&messages, None, 5).is_err());
    }
    #[test]
    fn repeated_or_empty_continuations_do_not_loop_forever() {
        let mut value = "<svg>".to_string();
        assert!(append_output(&mut value, "").is_err());
        assert!(append_output(&mut value, "<svg>").is_err());
        append_output(&mut value, "<text>new</text>").unwrap();
        assert!(append_output(&mut value, "<text>new</text>").is_err());
        append_output(&mut value, "</svg>").unwrap();
        assert_eq!(extract_svg(&value).unwrap(), value);
    }
    #[test]
    fn cancellation_covers_early_stop_duplicate_requests_and_account_isolation() {
        let key = uuid::Uuid::new_v4().to_string();
        cancel(key.clone(), "alice").unwrap();
        assert!(matches!(begin(key.clone(), "alice"), Err(e) if e == CANCELLED));
        assert!(begin(key.clone(), "bob").is_err());
        REQUESTS.lock().unwrap().remove(&key);
        let key = uuid::Uuid::new_v4().to_string(); let guard = begin(key.clone(), "alice").unwrap();
        assert!(begin(key.clone(), "alice").is_err()); assert!(cancel(key.clone(), "bob").is_err());
        cancel(key.clone(), "alice").unwrap(); assert!(guard.flag.load(Ordering::SeqCst));
        drop(guard); assert!(!REQUESTS.lock().unwrap().contains_key(&key));
    }
    #[test]
    fn pending_http_future_is_dropped_promptly_when_stopped() {
        let flag = Arc::new(AtomicBool::new(false)); let stop = flag.clone();
        let thread = std::thread::spawn(move || { std::thread::sleep(Duration::from_millis(90)); stop.store(true, Ordering::SeqCst); });
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let start = Instant::now();
        let result: Result<Value, String> = runtime.block_on(cancellable(std::future::pending(), &flag));
        assert_eq!(result.unwrap_err(), CANCELLED); assert!(start.elapsed() < Duration::from_secs(2)); thread.join().unwrap();
    }
    fn read_request(stream: &mut std::net::TcpStream) -> (String, Value) {
        use std::io::BufRead;
        let mut reader = std::io::BufReader::new(stream.try_clone().unwrap());
        let mut request = String::new(); reader.read_line(&mut request).unwrap();
        let mut length = 0;
        loop {
            let mut line = String::new(); reader.read_line(&mut line).unwrap();
            if line == "\r\n" { break; }
            if line.to_ascii_lowercase().starts_with("content-length:") { length = line.split(':').nth(1).unwrap().trim().parse::<usize>().unwrap(); }
        }
        let mut bytes = vec![0; length]; reader.read_exact(&mut bytes).unwrap();
        (request, if bytes.is_empty() { Value::Null } else { serde_json::from_slice(&bytes).unwrap() })
    }
    #[test]
    fn model_transport_continues_beyond_three_chunks_with_actual_limit() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}/v1", listener.local_addr().unwrap());
        let worker = std::thread::spawn(move || {
            for n in 0..7 {
                let (mut stream, _) = listener.accept().unwrap();
                let (request, body) = read_request(&mut stream);
                let response = if n == 0 {
                    assert!(request.contains("/v1/modes")); json!({"maxOutputTokens":123456,"maxRequestBytes":1_048_576})
                } else {
                    assert_eq!(body["max_tokens"], 123456); assert_eq!(body["model"], "omaax");
                    if n == 1 { json!({"choices":[{"message":{"content":"<svg xmlns=\"http://www.w3.org/2000/svg\">"},"finish_reason":"length"}]}) }
                    else if n == 6 { json!({"choices":[{"message":{"content":"</svg>"},"finish_reason":"stop"}]}) }
                    else { json!({"choices":[{"message":{"content":format!("<text y=\"{n}\">panel {n}</text>")},"finish_reason":"length"}]}) }
                }.to_string();
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", response.len(), response).unwrap();
            }
        });
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let value = runtime.block_on(model_svg(&base, "test-private-key", &json!({"prompt":"Arrange panels","svg":SVG}), &Arc::new(AtomicBool::new(false)), || Ok(()))).unwrap();
        assert!(value["svg"].as_str().unwrap().contains("panel 5")); worker.join().unwrap();
    }
}
