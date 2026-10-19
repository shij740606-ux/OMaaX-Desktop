//! OMaaX Desktop（Tauri 版）主进程：一个无边框窗口里放两个 WebView —— "ui"（我们的 React 壳子）
//! 和 "dsh"（dsh web 原版界面，作为子视图贴在对话页的内容区）。
mod dsh;
mod hub_auth;
mod cloud_qa;
mod education;
pub use hub_auth::check_model;
mod files_server;
mod refdata;
mod demos;
mod files;
mod git;
mod migrate;
mod updater;
mod paths;
mod platform_auth;
mod platform_mcp;
pub use platform_mcp::run as run_platform_mcp;
mod knowledge;
mod ssh_hosts;
mod personal_compute;
pub use ssh_hosts::run_mcp;
mod pyenv;
mod settings;
mod skills;
#[cfg(target_os = "linux")]
mod linux_layout;

use dsh::DshManager;
use paths::AppPaths;
use pyenv::PyEnvManager;
use serde::Deserialize;
use settings::{read_credential, write_credential, AppSettings, CredentialStatus};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, WebviewUrl, Window};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

#[derive(Clone, Copy, Deserialize, Default)]
pub struct Rect { x: f64, y: f64, width: f64, height: f64 }

pub struct AppState {
    paths: AppPaths,
    settings: Mutex<AppSettings>,
    dsh: Arc<DshManager>,
    pyenv: Arc<PyEnvManager>,
    dsh_bounds: Mutex<(Rect, bool)>,
    files: Option<Arc<files_server::FilesServer>>,
}

const DSH_INIT_SCRIPT: &str = r#"(() => {
  const css = `    [class*="_sidebarCol"] { visibility: hidden !important; overflow: hidden !important; min-width: 0 !important; }
    [class*="_handle"][data-side="left"] { display: none !important; }
    [class*="_headline"], [class*="_heroGlow"] { display: none !important; }

    [data-biodsh-hidden="1"] { display: none !important; }
    .biodsh-sum { margin: 6px 0 10px; display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 10px; background: rgba(120,120,128,.09); color: #6e6e73; font-size: 12.5px; cursor: pointer; user-select: none; line-height: 1.4; }
    .biodsh-sum:hover { background: rgba(120,120,128,.16); }
    .biodsh-sum .biodsh-chev { display: inline-block; font-size: 10px; transition: transform .15s; opacity: .8; }
    .biodsh-sum[data-open="1"] .biodsh-chev { transform: rotate(90deg); }
    .biodsh-sum .biodsh-n { margin-left: auto; opacity: .7; font-variant-numeric: tabular-nums; }
    .biodsh-sum[data-live="1"] .biodsh-txt { font-weight: 600; background: linear-gradient(90deg, #6e6e73 0%, #1d1d1f 35%, #b8b8bd 50%, #1d1d1f 65%, #6e6e73 100%); background-size: 200% 100%; -webkit-background-clip: text; background-clip: text; color: transparent; animation: biodsh-shimmer 1.6s linear infinite; }
    body[data-ds-dark-theme] .biodsh-sum { background: rgba(255,255,255,.07); color: #98989d; }
    body[data-ds-dark-theme] .biodsh-sum[data-live="1"] .biodsh-txt { background: linear-gradient(90deg, #8e8e93 0%, #f5f5f7 35%, #5a5a5f 50%, #f5f5f7 65%, #8e8e93 100%); background-size: 200% 100%; -webkit-background-clip: text; background-clip: text; }
    @keyframes biodsh-shimmer { from { background-position: 200% 0 } to { background-position: -200% 0 } }
    /* 展开后的中间步骤：缩进一点，和正文区分 */
    [data-biodsh-mid="1"] { margin-left: 10px !important; border-left: 2px solid rgba(120,120,128,.18); padding-left: 8px; }
    /* 最终回答里的思考行：收起时隐藏 */
    [data-biodsh-final="1"][data-biodsh-open="0"] [data-variant="think"] { display: none !important; }
    /* 正文里提到的图片文件 → 自动缩略图 */
    .biodsh-fig { display: block; max-width: min(100%, 720px); margin: 8px 0 12px; border-radius: 10px; border: 1px solid rgba(120,120,128,.18); cursor: zoom-in; background: #fff; }
    .biodsh-fig[data-zoom="1"] { max-width: 100%; cursor: zoom-out; }
    /* dsh 自己的运行状态行也用跑马灯 */
    [class*="_turnStatus"] { display: none !important; }
  `;
  const apply = () => {
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const fix = () => { const f = document.querySelector('[class*="_frame"]'); if (!f) return; const v = f.style.gridTemplateColumns; if (v && !v.startsWith('0px')) f.style.gridTemplateColumns = v.replace(/^\S+/, '0px'); };
    const mo = new MutationObserver(fix);
    const start = () => { const f = document.querySelector('[class*="_frame"]'); if (!f) { setTimeout(start, 200); return; } mo.observe(f, { attributes: true, attributeFilter: ['style'] }); fix(); };
    start();
    // ---- 收纳中间过程：每一轮里的上下文注入 / 思考 / 工具调用折成一行，点开才逐级展开；最终回答保持可见
  const KIND = (el) => el.getAttribute('data-chat-flow-kind') || '';
  const open = new Set(); // 展开的组（以该轮 user 的 flow-key 记）
  const titleOf = (el) => {
    const t = el.querySelector('[class*="_title"]'); const s = el.querySelector('[class*="_summary"]');
    const a = (t?.textContent || '').trim(), b = (s?.textContent || '').trim();
    return (b || a).slice(0, 60);
  };
  const isThink = (el) => !!el.querySelector('[data-variant="think"]');
  const hasMd = (el) => !!el.querySelector('[class*="_markdown_"]');
  const regroup = () => {
    const col = document.querySelector('[class*="_column"]'); if (!col) return;
    const items = Array.from(col.children).filter((c) => c.hasAttribute('data-chat-flow-kind'));
    const running = !!document.querySelector('[data-streaming="true"], [data-state="running"]');
    let turnKey = null, run = [];
    const flush = (ended, i) => {
      if (!turnKey) return;
      let final = null;
      if (ended && run.length && KIND(run[run.length - 1]) === 'assistant-step' && hasMd(run[run.length - 1])) final = run.pop();
      const mid = run;
      const anchor = final || (mid.length ? mid[mid.length - 1].nextElementSibling : null);
      // 清理旧的摘要（同一轮）
      const isOpen = open.has(turnKey);
      let sum = col.querySelector(`.biodsh-sum[data-turn="${CSS.escape(turnKey)}"]`);
      if (mid.length === 0) { if (sum) sum.remove(); }
      else {
        if (!sum) {
          const key = turnKey; // 必须固定住：turnKey 是循环变量，闭包里直接用会拿到最后一轮的值（之前点不开的原因）
          sum = document.createElement('div'); sum.className = 'biodsh-sum'; sum.setAttribute('data-turn', key);
          sum.innerHTML = '<span class="biodsh-chev">▶</span><span class="biodsh-txt"></span><span class="biodsh-n"></span>';
          sum.addEventListener('click', (ev) => { ev.stopPropagation(); if (open.has(key)) open.delete(key); else open.add(key); regroup(); });
        }
        if (sum.nextElementSibling !== mid[0]) col.insertBefore(sum, mid[0]);
        const thinks = mid.filter((e) => KIND(e) === 'assistant-step').length;
        const tools = mid.filter((e) => KIND(e) === 'tool-call').length;
        const live = !ended && running;
        sum.setAttribute('data-open', isOpen ? '1' : '0'); sum.setAttribute('data-live', live ? '1' : '0');
        const last = mid[mid.length - 1];
        const lastTitle = KIND(last) === 'tool-call' ? titleOf(last) : '思考';
        sum.querySelector('.biodsh-txt').textContent = live ? `OMaaX 正在：${lastTitle}…` : `OMaaX 完成了 ${mid.length} 步（思考 ${thinks} · 操作 ${tools}）· 点开查看过程`;
        sum.querySelector('.biodsh-n').textContent = live ? `${mid.length} 步` : '';
        for (const e of mid) { e.setAttribute('data-biodsh-hidden', isOpen ? '0' : '1'); e.setAttribute('data-biodsh-mid', '1'); }
      }
      if (final) { final.setAttribute('data-biodsh-final', '1'); final.setAttribute('data-biodsh-open', isOpen ? '1' : '0'); final.removeAttribute('data-biodsh-hidden'); final.removeAttribute('data-biodsh-mid'); }
    };
    for (let i = 0; i < items.length; i++) {
      const el = items[i], k = KIND(el);
      if (k === 'user') { flush(true, i); turnKey = el.getAttribute('data-chat-flow-key') || String(i); run = []; el.removeAttribute('data-biodsh-hidden'); continue; }
      if (k === 'turn-tail') { flush(true, i); turnKey = null; run = []; continue; }
      if (k === 'context' || k === 'assistant-step' || k === 'tool-call') { if (turnKey) run.push(el); continue; }
    }
    flush(false, items.length); // 还在进行中的一轮：全部当中间过程，摘要显示“正在…”
  };
  // ---- 正文里提到的图片文件（`outputs/x.png`、C:\...\y.jpg）→ 自动插入缩略图（智能体按人设会自己用 ![]() 内嵌；这是兜底）
  const IMG_RE = /^[\w./\\:\-()（）\u4e00-\u9fa5 ]+\.(png|jpe?g|gif|webp|svg)$/i;
  const cwdOf = () => {
    const ctx = window.__biodsh || {}; const ws = ctx.ws || {};
    const crumb = document.querySelector('[class*="_crumbCurrent"]');
    const title = (crumb?.textContent || '').trim();
    return ws[title] || '';
  };
  const toUrl = (p) => {
    const ctx = window.__biodsh || {}; if (!ctx.files) return null;
    let abs = p.replace(/\\/g, '/');
    if (!/^[A-Za-z]:\//.test(abs) && !abs.startsWith('/')) { const cwd = cwdOf(); if (cwd) { abs = cwd.replace(/\\/g, '/').replace(/\/$/, '') + '/' + abs.replace(/^\.\//, ''); } else { abs = abs.split('/').pop(); } }  // 无 cwd 就只发文件名，交给服务端在工作区里搜
    return ctx.files + '/' + encodeURIComponent(abs).replace(/%2F/g, '/').replace(/%3A/g, ':');
  };
  const decorate = () => {
    document.querySelectorAll('[data-biodsh-final="1"] [class*="_markdown_"] code, [data-biodsh-final="1"] [class*="_markdown_"] a').forEach((el) => {
      if (el.dataset.biodshImg) return; el.dataset.biodshImg = '1';
      const t = (el.textContent || '').trim(); if (!IMG_RE.test(t) || t.length > 200) return;
      const url = toUrl(t); if (!url) return;
      const md = el.closest('[class*="_markdown_"]'); if (!md) return;
      if (md.querySelector(`img[data-src-key="${CSS.escape(t)}"]`)) return;
      if (md.querySelector(`img[src="${CSS.escape(url)}"]`)) return; // 智能体已经自己内嵌了
      const block = el.closest('p, li, td, th, h1, h2, h3, h4, blockquote') || el;
      const holder = block.closest('table') || block;
      const img = document.createElement('img'); img.className = 'biodsh-fig'; img.loading = 'lazy'; img.src = url; img.alt = t; img.setAttribute('data-src-key', t);
      img.addEventListener('error', () => img.remove());
      img.addEventListener('click', () => img.setAttribute('data-zoom', img.getAttribute('data-zoom') === '1' ? '0' : '1'));
      holder.insertAdjacentElement('afterend', img);
    });
  };
  // 隐藏 dsh 冗余的会话顶栏(面包屑+Session日志):我们外壳已有一条对话栏 + 侧栏显示会话,
  // 两条栏重复、还有截断/对齐问题。精确定位:含面包屑 _crumbs 的那个 _header 才隐藏,不碰消息内的 header。
  const hideHeader = () => { document.querySelectorAll('[class*="_crumbs"]').forEach((c) => {
    // 只隐藏真正的会话顶栏:面包屑若出现在某条消息/工具块/markdown 里,别顺着 closest 把整块消息内容也隐掉。
    if (c.closest('[data-chat-flow-kind]') || c.closest('[class*="_message"]') || c.closest('[class*="_markdown_"]')) return;
    const h = c.closest('[class*="_header"]'); if (h && !h.closest('[data-chat-flow-kind]') && !h.closest('[class*="_message"]')) h.style.display = 'none';
  }); };
  let scheduled = false;
  const schedule = () => { if (scheduled) return; scheduled = true; requestAnimationFrame(() => { scheduled = false; try { hideHeader(); regroup(); decorate(); } catch (e) { console.warn('biodsh collapse', e); } }); };
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-state', 'data-streaming'] });
  schedule();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply); else apply();
})();"#;

fn trace(paths: &AppPaths, line: &str) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(paths.logs.join("trace.log")) { let _ = writeln!(f, "{line}"); }
}

fn workspace_of(state: &AppState) -> String {
    let w = state.settings.lock().unwrap().workspace.clone();
    if w.is_empty() { state.paths.workspace.to_string_lossy().into() } else { w }
}

/// 把 dsh 子视图贴到壳子汇报的矩形上；不可见时隐藏。
fn layout_dsh(app: &AppHandle, state: &AppState) {
    let Some(view) = app.get_webview("dsh") else { return };
    let (r, visible) = *state.dsh_bounds.lock().unwrap();
    #[cfg(target_os = "linux")]
    {
        let _ = linux_layout::layout_chat(&view, r, visible);
        return;
    }
    #[cfg(not(target_os = "linux"))]
    {
    trace(&state.paths, &format!("layout_dsh visible={} rect=({},{},{},{})", visible, r.x, r.y, r.width, r.height));
    if visible && r.width > 0.0 {
        let _ = view.set_position(LogicalPosition::new(r.x, r.y));
        let _ = view.set_size(LogicalSize::new(r.width, r.height));
        let _ = view.show();
    } else {
        // Windows 上子视图 hide() 可能无效：同时把它挪出屏幕并缩为 1×1，确保不遮挡浮层
        let _ = view.hide();
        let _ = view.set_position(LogicalPosition::new(-20000.0, -20000.0));
        let _ = view.set_size(LogicalSize::new(1.0, 1.0));
    }
    trace(&state.paths, &format!("layout_dsh finished visible={visible}"));
    }
}

fn ensure_dsh_view(app: &AppHandle, state: &AppState, url: &str) {
    if std::env::var_os("BIODSH_NO_DSH_VIEW").is_some() { return; }
    if let Some(view) = app.get_webview("dsh") {
        let _ = view.navigate(url.parse().unwrap());
        layout_dsh(app, state);
        return;
    }
    let Some(window) = app.get_window("main") else { return };
    let opener = app.clone();
    let builder = tauri::webview::WebviewBuilder::new("dsh", WebviewUrl::External(url.parse().unwrap())).data_directory(state.paths.root.join("webview-dsh"))
        // 关键:关掉 Tauri 自带的拖放拦截。开着时(默认 true)WebView2 会吞掉 dsh 网页里
        // <input type=file>/文件夹选择的原生对话框 → dsh 那边"新增本地文件夹/附件"的选项框弹不出来。
        // 关掉后原生选择框恢复,且网页自身的 HTML5 拖放(ondrop)照常可用。
        .disable_drag_drop_handler()
        .initialization_script(DSH_INIT_SCRIPT)
        .on_navigation(move |u| {
            if u.host_str() == Some("127.0.0.1") || u.scheme() == "about" { return true; }
            let _ = opener.opener().open_url(u.to_string(), None::<&str>);
            false
        });
    let (r, _) = *state.dsh_bounds.lock().unwrap();
    if window.add_child(builder, LogicalPosition::new(r.x, r.y), LogicalSize::new(r.width.max(1.0), r.height.max(1.0))).is_ok() {
        layout_dsh(app, state);
    }
}

// ---------- 命令 ----------
#[tauri::command]
fn app_info(app: AppHandle, state: State<'_, AppState>) -> serde_json::Value {
    trace(&state.paths, "app_info called");
    serde_json::json!({ "version": app.package_info().version.to_string(), "dshVersion": dsh_version(&app), "platform": if cfg!(windows) { "win32" } else if cfg!(target_os = "macos") { "darwin" } else { "linux" }, "paths": state.paths, "dark": false, "runtime": "tauri" })
}
#[tauri::command]
fn settings_get(state: State<'_, AppState>) -> AppSettings { state.settings.lock().unwrap().clone() }
#[tauri::command]
fn settings_set(app: AppHandle, state: State<'_, AppState>, patch: serde_json::Value) -> Result<AppSettings,String> {
    if let Some(w)=patch["workspace"].as_str() { hub_auth::local_path(&state.paths.root,w)?; }
    if patch["remoteDshUrl"].as_str().is_some_and(|v|!v.is_empty()) || patch["mcpServers"].as_array().is_some_and(|v|!v.is_empty()) {return Err("此连接方式暂不可用，请使用对话模型设置或添加分析服务器".into());}
    let mut s = state.settings.lock().unwrap();
    if let Some(b) = patch.get("useChinaMirror").and_then(|x| x.as_bool()) { s.use_china_mirror = b; }
    if let Some(w) = patch.get("workspace").and_then(|x| x.as_str()) { s.workspace = w.into(); }
    if let Some(b) = patch.get("onboarded").and_then(|x| x.as_bool()) { s.onboarded = b; }
    for (k, setter) in [("mode", 0), ("offlineBaseUrl", 1), ("offlineModel", 2), ("offlineApiKey", 3), ("remoteDshUrl", 4)] {
        if let Some(v) = patch.get(k).and_then(|x| x.as_str()) {
            match setter { 0 => s.mode = v.into(), 1 => s.offline_base_url = v.into(), 2 => s.offline_model = v.into(), 3 => s.offline_api_key = v.into(), _ => s.remote_dsh_url = v.into() }
        }
    }
    for (k, setter) in [("platformUrl", 0), ("currentProjectId", 1)] {
        if let Some(v) = patch.get(k).and_then(|x| x.as_str()) {
            match setter { 0 => s.platform_url = v.trim().into(), _ => s.current_project_id = v.trim().into() }
        }
    }
    for (k, setter) in [("imageBaseUrl", 0), ("imageModel", 1), ("imageApiKey", 2)] {
        if let Some(v) = patch.get(k).and_then(|x| x.as_str()) {
            match setter { 0 => s.image_base_url = v.trim().into(), 1 => s.image_model = v.trim().into(), _ => s.image_api_key = v.trim().into() }
        }
    }
    if let Some(value) = patch.get("ragflowUrl").and_then(|v| v.as_str()) { s.ragflow_url = value.trim().into(); }
    if let Some(value) = patch.get("ragflowMode").and_then(|v| v.as_str()).filter(|v| matches!(*v, "local" | "server")) { s.ragflow_mode = value.into(); }
    if let Some(arr) = patch.get("mcpServers") { if let Ok(list) = serde_json::from_value::<Vec<settings::McpServer>>(arr.clone()) { s.mcp_servers = list; } }
    if let Some(b) = patch.get("demosSeeded").and_then(|x| x.as_bool()) { s.demos_seeded = b; }
    let mut lang_changed = None;
    if let Some(l) = patch.get("language").and_then(|x| x.as_str()) { if s.language != l { lang_changed = Some(l.to_string()); } s.language = l.into(); }
    let mut theme_changed = None;
    if let Some(t) = patch.get("theme").and_then(|x| x.as_str()) { if s.theme != t { theme_changed = Some(t.to_string()); } s.theme = t.into(); }
    s.save(&state.paths);
    let out = s.clone();
    drop(s);
    if let Some(l) = lang_changed.as_deref() { dsh::set_locale(&state.paths, l); }
    if theme_changed.is_some() || lang_changed.is_some() {
        if let Some(t) = theme_changed.as_deref() { dsh::set_theme(&state.paths, t); }
        if let Some(v) = app.get_webview("dsh") { let _ = v.eval("location.reload()"); }
    }
    Ok(out)
}
#[tauri::command]
fn credential_get(state: State<'_, AppState>) -> CredentialStatus { read_credential(&state.paths) }
#[tauri::command]
async fn platform_session(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().platform_url.clone();
    tauri::async_runtime::spawn_blocking(move || platform_auth::status(&url))
        .await.map_err(|_| "连接检查未能完成，请重试".to_string())?
}
#[tauri::command]
async fn platform_login(state: State<'_, AppState>, username: String, password: String) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().platform_url.clone();
    tauri::async_runtime::spawn_blocking(move || platform_auth::login(&url, &username, &password))
        .await.map_err(|_| "设备授权未能完成，请重试".to_string())?
}
#[tauri::command]
async fn platform_logout(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().platform_url.clone();
    tauri::async_runtime::spawn_blocking(move || platform_auth::logout(&url))
        .await.map_err(|_| "撤销授权未能完成，请重试".to_string())?
}
#[tauri::command]
fn platform_nodes(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    platform_auth::nodes(&state.settings.lock().unwrap().platform_url)
}
#[tauri::command]
fn platform_node_metrics(state: State<'_, AppState>, node_id: String, minutes: u32) -> Result<serde_json::Value, String> {
    platform_auth::node_metrics(&state.settings.lock().unwrap().platform_url, &node_id, minutes)
}
#[tauri::command]
fn platform_update_node(state: State<'_, AppState>, node_id: String, status: String) -> Result<serde_json::Value, String> {
    platform_auth::update_node(&state.settings.lock().unwrap().platform_url, &node_id, &status)
}
#[tauri::command]
fn platform_jobs(state: State<'_, AppState>, project_id: String) -> Result<serde_json::Value, String> {
    platform_auth::jobs(&state.settings.lock().unwrap().platform_url, &project_id)
}
#[tauri::command]
fn platform_job_kinds(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    platform_auth::job_kinds(&state.settings.lock().unwrap().platform_url)
}
#[tauri::command]
fn platform_create_job(state: State<'_, AppState>, input: serde_json::Value) -> Result<serde_json::Value, String> {
    platform_auth::create_job(&state.settings.lock().unwrap().platform_url, input)
}
#[tauri::command]
fn platform_cancel_job(state: State<'_, AppState>, job_id: String) -> Result<serde_json::Value, String> {
    platform_auth::cancel_job(&state.settings.lock().unwrap().platform_url, &job_id)
}
#[tauri::command]
fn platform_job_events(state: State<'_, AppState>, job_id: String) -> Result<serde_json::Value, String> {
    platform_auth::job_events(&state.settings.lock().unwrap().platform_url, &job_id)
}
#[tauri::command]
fn platform_job_artifacts(state: State<'_, AppState>, job_id: String) -> Result<serde_json::Value, String> {
    platform_auth::job_artifacts(&state.settings.lock().unwrap().platform_url, &job_id)
}
#[tauri::command]
fn platform_download_job_artifact(state: State<'_, AppState>, job_id: String, artifact_id: String, relative_path: String) -> Result<String, String> {
    if !job_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') { return Err("任务编号无效".into()); }
    let relative = std::path::Path::new(&relative_path);
    if relative.components().any(|part| !matches!(part, std::path::Component::Normal(_))) { return Err("结果文件名无效".into()); }
    let target = state.paths.root.join("results").join(&job_id).join(relative);
    platform_auth::download_job_artifact(&state.settings.lock().unwrap().platform_url, &job_id, &artifact_id, &target)?;
    Ok(target.to_string_lossy().into_owned())
}

#[tauri::command]
fn knowledge_credential() -> knowledge::CredentialStatus { knowledge::credential() }
#[tauri::command]
fn ssh_hosts_list(state: State<'_, AppState>) -> Result<Vec<ssh_hosts::Host>, String> { ssh_hosts::list(&state.paths.root) }
#[tauri::command]
fn ssh_hosts_save(state: State<'_, AppState>, draft: ssh_hosts::Draft) -> Result<Vec<ssh_hosts::Host>, String> { ssh_hosts::save(&state.paths.root, draft) }
#[tauri::command]
fn ssh_hosts_remove(state: State<'_, AppState>, id: String) -> Result<Vec<ssh_hosts::Host>, String> { ssh_hosts::remove(&state.paths.root, &id) }
#[tauri::command]
fn ssh_hosts_select(state: State<'_, AppState>, id: String, selected: bool) -> Result<Vec<ssh_hosts::Host>, String> { ssh_hosts::select(&state.paths.root, &id, selected) }
#[tauri::command]
fn ssh_hosts_trust(state: State<'_, AppState>, id: String, host_key: String, fingerprint: String) -> Result<Vec<ssh_hosts::Host>, String> { ssh_hosts::trust(&state.paths.root, &id, &host_key, &fingerprint) }
#[tauri::command]
async fn ssh_hosts_test(app: AppHandle, state: State<'_, AppState>, id: String, probe: bool) -> Result<serde_json::Value, String> {
    let root = state.paths.root.clone(); let node = paths::node_binary(&app); let script = paths::resource(&app, "ssh").join("client.cjs");
    tauri::async_runtime::spawn_blocking(move || ssh_hosts::call(&root, &node, &script, &id, if probe { "probe" } else { "test" }, "", 20, false)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
async fn personal_compute(app: AppHandle, state: State<'_, AppState>, id: String, action: String, body: serde_json::Value) -> Result<serde_json::Value,String> {
    let root=state.paths.root.clone(); let node=paths::node_binary(&app); let script=paths::resource(&app,"ssh").join("client.cjs");
    tauri::async_runtime::spawn_blocking(move || {
        if action=="transfers" {personal_compute::transfers(&root)}
        else if action=="transfer_resume" {personal_compute::resume_transfer(&root,&node,&script,body["id"].as_str().unwrap_or(""))}
        else if action=="schedule" {
            let ids:Vec<String>=serde_json::from_value(body["hosts"].clone()).map_err(|_|"候选服务器无效")?;
            personal_compute::schedule(&root,&node,&script,&ids,body["spec"].clone(),false)
        } else if action=="install_environment" { ssh_hosts::install_environment(&root,&node,&script,&id,body["password"].as_str().unwrap_or("")) } else if action=="transfer" { personal_compute::transfer(&root,&node,&script,&id,body["target"].as_str(),body["source"].clone()) }
        else { personal_compute::request(&root,&node,&script,&id,&action,body,false) }
    }).await.map_err(|e|e.to_string())?
}
#[tauri::command]
async fn ssh_hosts_metrics(app: AppHandle, state: State<'_, AppState>, id: String) -> Result<serde_json::Value, String> {
    let root = state.paths.root.clone(); let node = paths::node_binary(&app); let script = paths::resource(&app, "ssh").join("client.cjs");
    tauri::async_runtime::spawn_blocking(move || ssh_hosts::metrics(&root, &node, &script, &id, false)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn ssh_hosts_files(app:AppHandle,state:State<'_,AppState>,id:String,body:serde_json::Value)->Result<serde_json::Value,String>{
    let root=state.paths.root.clone();let node=paths::node_binary(&app);let script=paths::resource(&app,"ssh").join("client.cjs");
    tauri::async_runtime::spawn_blocking(move||ssh_hosts::files(&root,&node,&script,&id,body)).await.map_err(|e|e.to_string())?
}
#[tauri::command]
fn knowledge_set_credential(key: String) -> Result<knowledge::CredentialStatus, String> { knowledge::set_credential(&key) }
#[tauri::command]
async fn knowledge_datasets(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().ragflow_url.clone();
    tauri::async_runtime::spawn_blocking(move || knowledge::datasets(&url)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
async fn knowledge_create_dataset(state: State<'_, AppState>, name: String, description: String) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().ragflow_url.clone();
    tauri::async_runtime::spawn_blocking(move || knowledge::create_dataset(&url, &name, &description)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
async fn knowledge_documents(state: State<'_, AppState>, dataset_id: String) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().ragflow_url.clone(); let root = state.paths.root.clone();
    tauri::async_runtime::spawn_blocking(move || knowledge::documents(&root, &url, &dataset_id)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
async fn knowledge_add_files(app: AppHandle, state: State<'_, AppState>, dataset_id: String) -> Result<knowledge::SyncResult, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().add_filter("Word 和 PDF", &["pdf", "doc", "docx"]).pick_files(move |paths| { let _ = tx.send(paths); });
    let files = tauri::async_runtime::spawn_blocking(move || rx.recv().ok().flatten().unwrap_or_default().into_iter().map(|p| std::path::PathBuf::from(p.to_string())).collect::<Vec<_>>()).await.map_err(|e| e.to_string())?;
    let url = state.settings.lock().unwrap().ragflow_url.clone(); let root = state.paths.root.clone();
    Ok(tauri::async_runtime::spawn_blocking(move || knowledge::upload_files(&root, &url, &dataset_id, &files)).await.map_err(|e| e.to_string())?)
}
#[tauri::command]
fn knowledge_folders(state: State<'_, AppState>) -> Vec<knowledge::Folder> { knowledge::folders(&state.paths.root) }
#[tauri::command]
async fn knowledge_add_folder(app: AppHandle, state: State<'_, AppState>, dataset_id: String) -> Result<knowledge::SyncResult, String> {
    let (tx, rx) = std::sync::mpsc::channel(); app.dialog().file().pick_folder(move |path| { let _ = tx.send(path); });
    let Some(directory) = tauri::async_runtime::spawn_blocking(move || rx.recv().ok().flatten().map(|p| p.to_string())).await.map_err(|e| e.to_string())? else { return Ok(knowledge::SyncResult::default()) };
    let url = state.settings.lock().unwrap().ragflow_url.clone(); let root = state.paths.root.clone();
    Ok(tauri::async_runtime::spawn_blocking(move || knowledge::add_folder(&root, &url, &dataset_id, &directory)).await.map_err(|e| e.to_string())?)
}
#[tauri::command]
fn knowledge_remove_folder(state: State<'_, AppState>, dataset_id: String, path: String) -> Vec<knowledge::Folder> { knowledge::remove_folder(&state.paths.root, &dataset_id, &path) }
#[tauri::command]
async fn knowledge_sync_folders(state: State<'_, AppState>) -> Result<knowledge::SyncResult, String> {
    let url = state.settings.lock().unwrap().ragflow_url.clone(); let root = state.paths.root.clone();
    Ok(tauri::async_runtime::spawn_blocking(move || knowledge::sync_folders(&root, &url)).await.map_err(|e| e.to_string())?)
}
#[tauri::command]
async fn knowledge_search(state: State<'_, AppState>, dataset_ids: Vec<String>, question: String) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().ragflow_url.clone();
    tauri::async_runtime::spawn_blocking(move || knowledge::search(&url, dataset_ids, &question)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
async fn credential_set(app: AppHandle, state: State<'_, AppState>, key: String) -> Result<CredentialStatus, String> {
    write_credential(&state.paths, &key);
    // 让新 key 立刻生效：重启 dsh（key 通过环境变量注入）
    if state.dsh.status.lock().unwrap().state != "stopped" { let _ = dsh_restart(app, state.clone()).await; }
    Ok(read_credential(&state.paths))
}

#[tauri::command]
async fn env_status(app: AppHandle, state: State<'_, AppState>) -> Result<pyenv::EnvStatus, String> {
    let (py, paths) = (Arc::clone(&state.pyenv), state.paths.clone());
    tauri::async_runtime::spawn_blocking(move || py.probe(&app, &paths)).await.map_err(|e| e.to_string())
}
#[tauri::command]
async fn env_install(app: AppHandle, state: State<'_, AppState>) -> Result<pyenv::EnvStatus, String> {
    let (py, paths, china) = (Arc::clone(&state.pyenv), state.paths.clone(), state.settings.lock().unwrap().use_china_mirror);
    tauri::async_runtime::spawn_blocking(move || py.install(&app, &paths, china)).await.map_err(|e| e.to_string())
}

#[tauri::command]
fn dsh_status(state: State<'_, AppState>) -> dsh::DshStatus { state.dsh.status.lock().unwrap().clone() }
#[tauri::command]
async fn dsh_start(app: AppHandle, state: State<'_, AppState>) -> Result<dsh::DshStatus, String> {
    let (d, paths, ws) = (Arc::clone(&state.dsh), state.paths.clone(), workspace_of(&state));
    let app2 = app.clone();
    hub_auth::local_path(&state.paths.root,&ws)?;
    let ws2 = ws.clone();
    let (offline, offline_key, remote) = {
        let st = state.settings.lock().unwrap();
        dsh::set_theme(&state.paths, &st.theme);
        dsh::set_locale(&state.paths, &st.language);
        let offline = st.mode == "offline";
        if offline && !st.offline_base_url.trim().is_empty() {
            dsh::set_llm_override(&state.paths, Some((st.offline_base_url.trim(), st.offline_model.trim())));
        } else {
            dsh::set_llm_override(&state.paths, None);
        }
        (offline, st.offline_api_key.clone(), st.remote_dsh_url.trim().to_string())
    };
    let (extra_env, mcp, _seed_demos) = {
        let st = state.settings.lock().unwrap();
        let env = vec![
            ("BIODSH_FILES_URL".to_string(), state.files.as_ref().map(|f| f.base_url()).unwrap_or_default()),
            ("BIODSH_IMAGE_BASE_URL".to_string(), st.image_base_url.clone()),
            ("BIODSH_IMAGE_API_KEY".to_string(), st.image_api_key.clone()),
            ("BIODSH_IMAGE_MODEL".to_string(), st.image_model.clone()),
            ("BIODSH_REFDATA".to_string(), refdata::dir(&state.paths).to_string_lossy().to_string()),
        ];
        (env, st.mcp_servers.clone(), !st.demos_seeded)
    };
    // 离线模式 + 配置了远程服务器：不在本机启动，直接连服务器上的 dsh
    if offline && !remote.is_empty() {
        return Err("账号模式不允许连接未隔离的远程智能体".into());
        #[allow(unreachable_code)]
        let mut s = state.dsh.status.lock().unwrap();
        s.state = "running".into();
        s.url = Some(remote.clone());
        s.log.push(format!("[remote] using {remote}"));
        drop(s);
        let st2 = state.inner();
        ensure_dsh_view(&app, st2, &remote);
        return Ok(state.dsh.status.lock().unwrap().clone());
    }
    let key = if offline { Some(offline_key) } else { None };
    let st = tauri::async_runtime::spawn_blocking(move || d.start_with(&app2, &paths, &ws2, key, extra_env, &mcp)).await.map_err(|e| e.to_string())?;
    if st.state == "running" {
        if let Some(u) = &st.url {
            // 让默认工作区在 dsh 里注册（幂等：已存在则原样返回）
            let (u2, ws3) = (u.clone(), ws.clone());
            let _ = tauri::async_runtime::spawn_blocking(move || {
                if let Ok(v) = dsh_call(&u2, "workspace.create", serde_json::json!({ "path": ws3 })) {
                    if v.get("created").and_then(|x| x.as_bool()) == Some(true) {
                        if let Some(id) = v.get("workspace").and_then(|w| w.get("workspaceId")).and_then(|x| x.as_str()) {
                            let _ = dsh_call(&u2, "workspace.rename", serde_json::json!({ "workspaceId": id, "title": "我的分析" }));
                        }
                    }
                }
            }).await;
            // 把所有已注册工作区加入图片服务白名单
            if let Some(f) = state.files.clone() {
                let u5 = u.clone();
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    if let Ok(v) = dsh_call(&u5, "workspace.list", serde_json::json!({})) {
                        for w in v.get("items").and_then(|x| x.as_array()).cloned().unwrap_or_default() {
                            if let Some(p) = w.get("path").and_then(|x| x.as_str()) { f.add_root(std::path::PathBuf::from(p)); }
                        }
                    }
                }).await;
            }
            let u = u.clone(); let st2 = state.inner(); ensure_dsh_view(&app, st2, &u);
        }
    }
    Ok(st)
}
/// 把「工作区标题 → 路径」和图片服务地址告诉 dsh 视图（对话里内嵌缩略图要靠它把相对路径拼成绝对路径）
#[tauri::command]
fn dsh_set_context(app: AppHandle, state: State<'_, AppState>, workspaces: serde_json::Value) {
    if let Some(v) = app.get_webview("dsh") {
        let files = state.files.as_ref().map(|f| f.base_url()).unwrap_or_default();
        if let Some(f) = state.files.as_ref() { for (_, p) in workspaces.as_object().cloned().unwrap_or_default() { if let Some(p) = p.as_str() { f.add_root(std::path::PathBuf::from(p)); } } }
        let js = format!("window.__biodsh = Object.assign(window.__biodsh || {{}}, {{ files: {}, ws: {} }});", serde_json::to_string(&files).unwrap_or_default(), workspaces);
        let _ = v.eval(&js);
    }
}
#[tauri::command]
fn refdata_list(state: State<'_, AppState>) -> Vec<refdata::PackStatus> { refdata::list(&state.paths) }
#[tauri::command]
async fn refdata_install(app: AppHandle, state: State<'_, AppState>, id: String) -> Result<refdata::PackStatus, String> {
    let paths = state.paths.clone();
    tauri::async_runtime::spawn_blocking(move || refdata::install(&app, &paths, &id)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
fn refdata_remove(state: State<'_, AppState>, id: String) -> Vec<refdata::PackStatus> { refdata::remove(&state.paths, &id) }
/// 设置页「重新安装示范项目」：再复制一遍数据+成品图表(不覆盖已有文件)并注册 4 个工作区。
/// 抹掉 .demo-content-v3 marker → 前端随后 restartDsh(),dsh 启动前会重跑 restore_demo_sessions,
/// 把 4 个范例的真跑对话(带步数/图)重新导入并挂回项目下。
#[tauri::command]
async fn demos_seed(_app: AppHandle, _state: State<'_, AppState>) -> Result<Vec<demos::Demo>, String> { Ok(Vec::new()) }

#[tauri::command]
async fn dsh_restart(app: AppHandle, state: State<'_, AppState>) -> Result<dsh::DshStatus, String> {
    state.dsh.stop(&app);
    dsh_start(app, state).await
}
#[tauri::command]
fn dsh_stop(app: AppHandle, state: State<'_, AppState>) { state.dsh.stop(&app); }
#[tauri::command]
async fn dsh_reload(app: AppHandle) { if let Some(v) = app.get_webview("dsh") { let _ = v.eval("location.reload()"); } }
#[tauri::command]
async fn dsh_bounds(app: AppHandle, state: State<'_, AppState>, rect: Rect, visible: bool) -> Result<(),String> {
    // WebView2 layout must run outside the synchronous IPC callback on Windows.
    trace(&state.paths, &format!("dsh_bounds x={} y={} w={} h={} visible={}", rect.x, rect.y, rect.width, rect.height, visible));
    *state.dsh_bounds.lock().unwrap() = (rect, visible);
    layout_dsh(&app, &state);
    Ok(())
}

#[tauri::command]
fn skills_catalog(app: AppHandle) -> Vec<serde_json::Value> { skills::catalog(&app) }
#[tauri::command]
fn skills_statuses(app: AppHandle, state: State<'_, AppState>) -> Vec<skills::SkillStatus> { skills::statuses(&app, &state.paths) }
#[tauri::command]
fn skills_readme(app: AppHandle, state: State<'_, AppState>, id: String) -> String { skills::readme(&app, &state.paths, &id) }
#[tauri::command]
async fn skills_install(app: AppHandle, state: State<'_, AppState>, id: String) -> Result<skills::SkillStatus, String> {
    let paths = state.paths.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let r = skills::install(&app, &paths, &id);
        let _ = app.emit("event", serde_json::json!({ "type": "skills", "statuses": skills::statuses(&app, &paths) }));
        r
    }).await.map_err(|e| e.to_string())
}
#[tauri::command]
fn skills_uninstall(app: AppHandle, state: State<'_, AppState>, id: String) -> skills::SkillStatus {
    let r = skills::uninstall(&state.paths, &id);
    let _ = app.emit("event", serde_json::json!({ "type": "skills", "statuses": skills::statuses(&app, &state.paths) }));
    r
}

#[tauri::command]
fn client_log(state: State<'_, AppState>, line: String) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(state.paths.logs.join("ui.log")) { let _ = writeln!(f, "{line}"); }
}
/// DeepSeek 账户余额（GET https://api.deepseek.com/user/balance）
#[tauri::command]
async fn deepseek_balance(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    if state.settings.lock().unwrap().mode == "offline" { return Err("offline-mode".into()); }
    let Some(key) = settings::read_credential_value(&state.paths) else { return Err("no-key".into()) };
    tauri::async_runtime::spawn_blocking(move || {
        let resp = ureq::get("https://api.deepseek.com/user/balance").set("Authorization", &format!("Bearer {key}")).timeout(std::time::Duration::from_secs(15)).call().map_err(|e| e.to_string())?;
        resp.into_json::<serde_json::Value>().map_err(|e| e.to_string())
    }).await.map_err(|e| e.to_string())?
}
#[tauri::command]
async fn git_status(state: State<'_,AppState>,path: String) -> Result<git::GitStatus,String> { hub_auth::local_path(&state.paths.root,&path)?; Ok(tauri::async_runtime::spawn_blocking(move || git::status(&path)).await.unwrap_or_default()) }
#[tauri::command]
async fn git_init(state: State<'_,AppState>,path: String) -> Result<git::GitStatus, String> { hub_auth::local_path(&state.paths.root,&path)?; tauri::async_runtime::spawn_blocking(move || git::init(&path)).await.map_err(|e| e.to_string())? }
#[tauri::command]
async fn git_commit(state: State<'_,AppState>,path: String, message: String) -> Result<git::GitStatus, String> { hub_auth::local_path(&state.paths.root,&path)?; tauri::async_runtime::spawn_blocking(move || git::commit(&path, &message)).await.map_err(|e| e.to_string())? }
/// 从 dsh web URL 取「源」(scheme://host:port)，丢掉 path 和 ?token=…。
/// dsh 0.1.2 起 URL 带 `?token=`，直接 `{url}/api/…` 会拼出坏地址(token 夹在中间)导致 RPC 全挂、项目列不出来。
fn origin_of(u: &str) -> String {
    if let Some(p) = u.find("://") {
        let after = p + 3;
        let rest = &u[after..];
        let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        return format!("{}{}", &u[..after], &rest[..end]);
    }
    u.trim_end_matches('/').to_string()
}
// 项目标题存储：dsh 0.1.2 去掉了 workspace 模型(项目=会话 cwd 分组,无标题字段)。
// 为兼容旧 app 的 workspace.create/rename/list(给项目起名、加空项目),这里用一个 cwd→标题 的小 JSON 兜住。
// 存在数据目录里,随升级保留。也充当「已知项目」登记表(没有会话的空项目也能显示)。
static TITLES_PATH: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();
fn load_titles() -> serde_json::Map<String, serde_json::Value> {
    TITLES_PATH.get().and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str::<serde_json::Map<String, serde_json::Value>>(&t).ok()).unwrap_or_default()
}
fn save_titles(m: &serde_json::Map<String, serde_json::Value>) {
    if let Some(p) = TITLES_PATH.get() {
        if let Some(dir) = p.parent() { let _ = std::fs::create_dir_all(dir); }
        let _ = std::fs::write(p, serde_json::to_string(m).unwrap_or_default());
    }
}
fn basename(p: &str) -> String { p.rsplit(['\\', '/']).find(|s| !s.is_empty()).unwrap_or(p).to_string() }
/// 把"我们的 workspaceId(= 文件夹路径)"解析成 dsh 0.1.2 的真实 workspaceId。
/// dsh 的 `workspace/create` 是幂等的:目录已注册就直接返回,没注册就登记。返回其真实 workspaceId。
/// 会话必须用真实 workspaceId 创建才会绑进工作区,否则 dsh 显示"选择工作区"、项目用不了。
fn dsh_workspace_id(url: &str, path: &str) -> Option<String> {
    dsh_raw(url, "workspace/create", "request", serde_json::json!({ "path": path })).ok()
        .and_then(|v| v.get("workspace").and_then(|w| w.get("workspaceId")).and_then(|x| x.as_str()).map(String::from))
}
/// dsh 0.1.2 的工作区持久化文件(dsh-atomic-write 原子写,读到的始终是合法 JSON)。与标题库同目录。
fn ws_json_path() -> Option<std::path::PathBuf> { TITLES_PATH.get().and_then(|p| p.parent()).map(|d| d.join("workspace.json")) }
/// 一次性迁移标记:把老适配(标题库)里的项目登记进 dsh 真实工作区,只做一次。
fn ws_migrated_marker() -> Option<std::path::PathBuf> { TITLES_PATH.get().and_then(|p| p.parent()).map(|d| d.join(".biodsh-ws-migrated-v2")) }
fn norm_path(p: &str) -> String { p.trim_end_matches(['\\', '/']).replace('/', "\\").to_lowercase() }
/// 读 dsh 真实工作区列表(tables.workspaces,按 global.workspaceIds 顺序),重塑成前端 workspace 形状。
fn read_workspaces() -> Option<(Vec<serde_json::Value>, serde_json::Value)> {
    use serde_json::json;
    let doc: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(ws_json_path()?).ok()?).ok()?;
    let table = doc.pointer("/tables/workspaces")?.as_object()?;
    let archived = doc.pointer("/global/archivedSessionIds").cloned().unwrap_or_else(|| json!([]));
    let ids: Vec<String> = doc.pointer("/global/workspaceIds").and_then(|x| x.as_array())
        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
        .unwrap_or_else(|| table.keys().cloned().collect());
    let items = ids.iter().filter_map(|id| table.get(id).map(|w| json!({
        "workspaceId": id,
        "title": w.get("title").cloned().unwrap_or_else(|| json!("")),
        "path": w.get("path").cloned().unwrap_or_else(|| json!("")),
        "sessionIds": w.get("sessionIds").cloned().unwrap_or_else(|| json!([])),
        "updatedAt": w.get("updatedAt").cloned().unwrap_or_else(|| json!("")),
    }))).collect();
    Some((items, archived))
}
/// 升级迁移(只做一次):把旧版(0.1.1 workspace 模型)里的项目名 + 示范项目名,搬进 0.1.2 的标题库。
/// 让老用户升级后项目/范例仍显示原来的名字,而不是文件夹名。纯读旧数据、写新库,不动任何会话/项目内容。
fn migrate_titles(paths: &AppPaths, demos_res: &std::path::Path) {
    let Some(store) = TITLES_PATH.get() else { return };
    if store.exists() { return; }
    let mut m = serde_json::Map::new();
    // 1) 旧 workspace.json 的用户项目标题(cwd → title)
    if let Ok(t) = std::fs::read_to_string(paths.dsh_home.join("storages").join("workspace.json")) {
        if let Ok(doc) = serde_json::from_str::<serde_json::Value>(&t) {
            if let Some(ws) = doc.pointer("/tables/workspaces").and_then(|x| x.as_object()) {
                for (_, w) in ws {
                    if let (Some(p), Some(title)) = (w.get("path").and_then(|x| x.as_str()), w.get("title").and_then(|x| x.as_str())) {
                        if !title.is_empty() { m.insert(p.to_string(), serde_json::json!(title)); }
                    }
                }
            }
        }
    }
    // 2) 示范项目标题(cwd = ~/OMaaX/demos/<id>)
    if let Ok(rd) = std::fs::read_dir(demos_res) {
        for e in rd.flatten().filter(|e| e.path().is_dir()) {
            let id = e.file_name().to_string_lossy().to_string();
            if let Some(title) = std::fs::read_to_string(e.path().join("demo.json")).ok()
                .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
                .and_then(|v| v.get("title").and_then(|x| x.as_str()).map(String::from)) {
                let cwd = paths.root.join("demos").join(&id).to_string_lossy().to_string();
                m.entry(cwd).or_insert(serde_json::json!(title));
            }
        }
    }
    if !m.is_empty() { save_titles(&m); }
}

static DSH_COOKIES: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, String>>> = std::sync::OnceLock::new();
fn cookie_cache() -> &'static std::sync::Mutex<std::collections::HashMap<String, String>> {
    DSH_COOKIES.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}
/// dsh 0.1.2 起 /api 需要浏览器会话签名 cookie：GET `/?token=…`(index 交换)拿到 `dsh-auth-*` cookie，后续 /api 带上它。
/// (0.1.1 无此鉴权层，旧 app 无 cookie 也能调 → 升级后 RPC 全 401、项目列不出来。) 按源缓存；401 时强制重换。
fn auth_cookie(full_url: &str, force: bool) -> Option<String> {
    let origin = origin_of(full_url);
    if !force { if let Some(c) = cookie_cache().lock().unwrap().get(&origin) { return Some(c.clone()); } }
    // 关键：dsh 0.1.2 的 index `/?token=` 会 303 重定向到干净 URL，Set-Cookie 在那个 303 上。
    // ureq 默认跟随重定向 → 最终响应没有 cookie（这正是升级后 app RPC 全 401、项目列不出来的直接原因）。
    // 禁用重定向，从 303/200 本身读 dsh-auth cookie。
    let agent = ureq::builder().redirects(0).timeout(std::time::Duration::from_secs(10)).build();
    let resp = match agent.get(full_url).call() {
        Ok(r) => r,
        Err(ureq::Error::Status(_, r)) => r,
        Err(_) => return None,
    };
    let cookie = resp.all("set-cookie").into_iter()
        .find(|sc| sc.starts_with("dsh-auth-"))
        .map(|sc| sc.split(';').next().unwrap_or(sc).trim().to_string())?;
    cookie_cache().lock().unwrap().insert(origin, cookie.clone());
    Some(cookie)
}
/// 调 dsh 的 HTTP RPC：POST /api/<method>，信封 {type:'client-request', rpcId, method, payload}
/// dsh 0.1.2 原生 RPC：POST /api/<endpoint>，信封 payload={args:{<wire>: args}}，带浏览器会话 cookie。
fn dsh_raw(url: &str, endpoint: &str, wire: &str, args: serde_json::Value) -> Result<serde_json::Value, String> {
    let payload = serde_json::json!({ "args": { wire: args } });
    let body = serde_json::json!({ "type": "client-request", "rpcId": uuid::Uuid::new_v4().to_string(), "method": endpoint, "payload": payload });
    let ep = format!("{}/api/{}", origin_of(url), endpoint);
    let post = |cookie: Option<String>| {
        let mut req = ureq::post(&ep).set("Content-Type", "application/json").timeout(std::time::Duration::from_secs(30));
        if let Some(c) = cookie { req = req.set("Cookie", &c); }
        req.send_json(body.clone())
    };
    let resp = match post(auth_cookie(url, false)) {
        Err(ureq::Error::Status(401, _)) => post(auth_cookie(url, true)), // cookie 过期/换端口 → 重换一次
        other => other,
    }.map_err(|e| match e { ureq::Error::Status(c, r) => format!("HTTP {c}: {}", r.into_string().unwrap_or_default()), e => e.to_string() })?;
    let v: serde_json::Value = resp.into_json().map_err(|e| e.to_string())?;
    let result = v.get("result").cloned().unwrap_or(serde_json::Value::Null);
    if result.get("ok").and_then(|x| x.as_bool()) == Some(true) { Ok(result.get("value").cloned().unwrap_or(serde_json::Value::Null)) }
    else { Err(result.get("error").map(|e| e.to_string()).unwrap_or_else(|| "rpc failed".into())) }
}
/// 兼容层：把 app 旧的 RPC 方法名/数据模型翻译到 dsh 0.1.2 新协议（0.1.2 去掉了 workspace，改成会话+cwd）。
/// 见记忆 dsh-012-rpc-protocol。renderer 完全不用改。
fn dsh_call(url: &str, method: &str, payload: serde_json::Value) -> Result<serde_json::Value, String> {
    use serde_json::json;
    match method {
        // 「项目」= dsh 0.1.2 的真实工作区(读 workspace.json:真实 workspaceId + 真实 sessionIds)。
        // 老适配把项目只写进标题库、没在 dsh 建工作区 → 会话不绑定、点开显示"选择工作区"。这里改成
        // 直接读 dsh 真实工作区;并做一次性迁移:把标题库里(磁盘存在、dsh 还没登记)的项目注册进 dsh。
        "workspace.list" => {
            if let Some(marker) = ws_migrated_marker() {
                if !marker.exists() {
                    let existing: std::collections::HashSet<String> = read_workspaces()
                        .map(|(items, _)| items.iter().filter_map(|it| it.get("path").and_then(|x| x.as_str()).map(norm_path)).collect())
                        .unwrap_or_default();
                    // 待登记的项目路径 = 标题库登记的 ∪ 现有会话的 cwd(把老 cwd 模式下的项目也保住,如 desproject)。
                    let mut paths: std::collections::HashSet<String> = load_titles().keys().cloned().collect();
                    if let Ok(v) = dsh_raw(url, "session/list", "_request", json!({})) {
                        if let Some(items) = v.get("items").and_then(|x| x.as_array()) {
                            for it in items {
                                if let Some(cwd) = it.get("cwd").and_then(|x| x.as_str()) {
                                    if !cwd.is_empty() { paths.insert(cwd.to_string()); }
                                }
                            }
                        }
                    }
                    for p in &paths {
                        if std::path::Path::new(p).is_dir() && !existing.contains(&norm_path(p)) {
                            let _ = dsh_raw(url, "workspace/create", "request", json!({ "path": p }));
                        }
                    }
                    let _ = std::fs::write(&marker, "1");
                }
            }
            match read_workspaces() {
                Some((items, archived)) => Ok(json!({ "items": items, "archivedSessionIds": archived })),
                // 读不到 workspace.json(极少数情况)→ 退回会话 cwd 分组,至少能列出来
                None => {
                    let v = dsh_raw(url, "session/list", "_request", json!({}))?;
                    let empty = vec![];
                    let items = v.get("items").and_then(|x| x.as_array()).unwrap_or(&empty);
                    let mut map: std::collections::BTreeMap<String, Vec<String>> = Default::default();
                    for it in items {
                        let cwd = it.get("cwd").and_then(|x| x.as_str()).unwrap_or("").to_string();
                        if cwd.is_empty() { continue; }
                        let sid = it.get("sessionId").and_then(|x| x.as_str()).unwrap_or("").to_string();
                        map.entry(cwd).or_default().push(sid);
                    }
                    let titles = load_titles();
                    for k in titles.keys() { map.entry(k.clone()).or_default(); }
                    let ws: Vec<serde_json::Value> = map.into_iter().map(|(cwd, sids)| {
                        let title = titles.get(&cwd).and_then(|x| x.as_str()).map(String::from).unwrap_or_else(|| basename(&cwd));
                        json!({ "workspaceId": cwd, "title": title, "path": cwd, "sessionIds": sids, "updatedAt": "" })
                    }).collect();
                    Ok(json!({ "items": ws, "archivedSessionIds": [] }))
                }
            }
        }
        // 0.1.2 的 session/list 条目已与 renderer 的 SessionRow(含 projections.values)对齐 → 原样透传
        "session.list" => dsh_raw(url, "session/list", "_request", json!({})),
        // 新对话：workspaceId 通常已是 dsh 真实工作区 id(来自 workspace.list),直接建会话即绑定。
        // 若传的是文件夹路径(含分隔符,如 demos/默认工作区首建),先解析成真实 workspaceId;都失败才退回 cwd。
        "session.create" => {
            let mut a = serde_json::Map::new();
            if let Some(p) = payload.get("workspaceId").and_then(|x| x.as_str()).filter(|s| !s.is_empty()) {
                if p.contains(['\\', '/', ':']) {
                    match dsh_workspace_id(url, p) {
                        Some(wid) => { a.insert("workspaceId".into(), json!(wid)); }
                        None => { a.insert("cwd".into(), json!(p)); }
                    }
                } else {
                    a.insert("workspaceId".into(), json!(p));
                }
            }
            dsh_raw(url, "session/create", "request", serde_json::Value::Object(a))
        }
        "session.rename" => dsh_raw(url, "session/rename", "request", payload),
        // dsh 0.1.2 的 session/prompt 必填 requestId(缺了整条被拒 → "数据库/让智能体分析"点了没反应)。
        // 前端多处发 prompt 没带 requestId,这里统一补上,覆盖所有调用方。
        "session.prompt" => {
            let mut p = payload;
            if p.get("requestId").is_none() {
                if let Some(o) = p.as_object_mut() { o.insert("requestId".into(), json!(uuid::Uuid::new_v4().to_string())); }
            }
            dsh_raw(url, "session/prompt", "request", p)
        }
        // 加项目 → dsh 原生 workspace/create{path}(幂等),直接返回真实工作区({workspace:{真实id,...}, created})。
        // 同时把路径记进标题库(仅作一次性迁移来源;列表已直接读 dsh)。
        "workspace.create" => {
            let path = payload.get("path").and_then(|x| x.as_str()).unwrap_or("").to_string();
            if path.is_empty() { return Ok(json!({ "created": false, "workspace": { "workspaceId": "" } })); }
            let v = dsh_raw(url, "workspace/create", "request", json!({ "path": path }))?;
            let mut m = load_titles(); if !m.contains_key(&path) { m.insert(path.clone(), json!(basename(&path))); save_titles(&m); }
            Ok(v)
        }
        // 起名/归档/移除 → workspaceId 已是 dsh 真实 id(来自 workspace.list),直接调原生 API。
        "workspace.rename" => {
            if let (Some(id), Some(t)) = (payload.get("workspaceId").and_then(|x| x.as_str()), payload.get("title").and_then(|x| x.as_str())) {
                let _ = dsh_raw(url, "workspace/rename", "request", json!({ "workspaceId": id, "title": t }));
            }
            Ok(json!({ "ok": true }))
        }
        "workspace.archiveSession" => dsh_raw(url, "workspace/archiveSession", "request", payload),
        "workspace.delete" => {
            if let Some(id) = payload.get("workspaceId").and_then(|x| x.as_str()) {
                let _ = dsh_raw(url, "workspace/delete", "request", json!({ "workspaceId": id }));
            }
            Ok(json!({ "ok": true }))
        }
        // 通用兜底：a.b → /api/a/b，args={request: payload}
        other => dsh_raw(url, &other.replacen('.', "/", 1), "request", payload),
    }
}
fn dsh_url(state: &AppState) -> Result<String, String> { state.dsh.status.lock().unwrap().url.clone().ok_or_else(|| "dsh 未运行".into()) }

#[tauri::command]
async fn dsh_rpc(state: State<'_, AppState>, method: String, payload: serde_json::Value) -> Result<serde_json::Value, String> {
    if method.starts_with("workflow.") || method.starts_with("server.") {let root=state.paths.root.clone();tauri::async_runtime::spawn_blocking(move ||hub_auth::require(&root,"bioflow")).await.map_err(|e|e.to_string())??;}
    for key in ["path","cwd"] { if let Some(path)=payload[key].as_str(){hub_auth::local_path(&state.paths.root,path)?;} }
    if !["session.budget","session.budgetReset","workflow.select","server.files","server.context","workspace.list","workspace.create","workspace.rename","workspace.archiveSession","workspace.delete","session.list","session.create","session.rename","session.prompt"].contains(&method.as_str()) {return Err("此对话操作未授权".into());}
    if matches!(method.as_str(),"session.budget"|"session.budgetReset") {
        let sid=payload["sessionId"].as_str().ok_or("请选择对话")?;
        personal_compute::session_context(&state.paths.dsh_home,Some(sid))?;
        let file=state.paths.dsh_home.join("workflow-bindings").join(format!("budget-{sid}.json"));
        let mut value:serde_json::Value=match std::fs::read(&file) {Ok(bytes)=>serde_json::from_slice(&bytes).map_err(|_|"消耗记录损坏，请联系管理员")?,Err(e) if e.kind()==std::io::ErrorKind::NotFound=>serde_json::json!({}),Err(_)=>return Err("无法读取消耗记录".into())};
        if method=="session.budgetReset" {
            value["calls"]=serde_json::json!(0);value["tokens"]=serde_json::json!(0);value["failures"]=serde_json::json!(0);value["paused"]=serde_json::json!(false);value["reported"]=serde_json::json!(false);
            if let Some(parent)=file.parent(){std::fs::create_dir_all(parent).map_err(|e|e.to_string())?;}
            let temp=file.with_extension(format!("{}.tmp",uuid::Uuid::new_v4()));
            std::fs::write(&temp,serde_json::to_vec(&value).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
            std::fs::rename(&temp,&file).map_err(|e|e.to_string())?;
        }
        return Ok(value);
    }
    if method=="workflow.select" {return personal_compute::bind_workflow(&state.paths.dsh_home,&payload);}
    if method=="server.files" {
        let sid=payload["sessionId"].as_str().ok_or("请选择对话")?;personal_compute::session_context(&state.paths.dsh_home,Some(sid))?;
        return Ok(std::fs::read(state.paths.dsh_home.join("session-servers").join(format!("{sid}.json"))).ok().and_then(|b|serde_json::from_slice::<serde_json::Value>(&b).ok()).unwrap_or_else(||serde_json::json!([])));
    }
    if method=="server.context" {
        let home=state.paths.dsh_home.clone();let sid=payload["sessionId"].as_str().map(String::from);let url=dsh_url(&state).ok();
        return tauri::async_runtime::spawn_blocking(move || {
        let mut context=personal_compute::session_context(&home,sid.as_deref())?;
        if let Some(sid)=sid.as_deref() {
            if let Some(url)=url {if let Ok(rows)=dsh_call(&url,"session.list",serde_json::json!({})) {
                if let Some(row)=rows["items"].as_array().and_then(|items|items.iter().find(|row|row["sessionId"]==sid)) {
                    if let Some(title)=row.pointer("/projections/values/title").and_then(serde_json::Value::as_str).filter(|name|!name.trim().is_empty()){context["sessionName"]=serde_json::json!(title);}
                }
            }}
            personal_compute::save_session_context(&home,&context)?;
        }
        Ok(context)
        }).await.map_err(|e|e.to_string())?;
    }
    let url = dsh_url(&state)?;
    tauri::async_runtime::spawn_blocking(move || dsh_call(&url, &method, payload)).await.map_err(|e| e.to_string())?
}
/// 切换内嵌视图到某个会话：dsh 没有 URL 路由，靠它自己记在 localStorage 的“当前会话”+ 刷新。
#[tauri::command]
async fn dsh_open_session(app: AppHandle, session_id: String) {
    if let Some(v) = app.get_webview("dsh") {
        let js = format!("try {{ localStorage.setItem('dsh.sessions.current', JSON.stringify({{ sessionId: {} }})); }} catch (e) {{}} location.reload();", serde_json::to_string(&session_id).unwrap_or_default());
        let _ = v.eval(&js);
    }
}
#[tauri::command]
async fn dsh_new_session(app: AppHandle, state: State<'_, AppState>, workspace_id: String) -> Result<String, String> {
    let url = dsh_url(&state)?;
    let v = tauri::async_runtime::spawn_blocking(move || dsh_call(&url, "session.create", serde_json::json!({ "workspaceId": workspace_id }))).await.map_err(|e| e.to_string())??;
    let id = v.get("sessionId").and_then(|x| x.as_str()).ok_or("no sessionId")?.to_string();
    dsh_open_session(app, id.clone()).await;
    Ok(id)
}

/// 语义搜索:用包里的 e5 模型把一句查询编码成归一化向量(离线,spawn 一次 node)。
/// 前端拿到向量后与预计算的数据库/技能向量做余弦排序。模型不可用/出错则返回 Err,前端回退词法。
#[tauri::command]
async fn embed_query(app: AppHandle, text: String) -> Result<Vec<f32>, String> {
    let node = crate::paths::node_binary(&app);
    let embed_dir = crate::paths::resource(&app, "embed");
    let script = embed_dir.join("embed-query.mjs");
    let models = embed_dir.join("models");
    tauri::async_runtime::spawn_blocking(move || {
        let mut cmd = std::process::Command::new(&node);
        cmd.arg(&script).arg(&text).current_dir(&embed_dir)
            .env("BIODSH_EMBED_OFFLINE", "1").env("BIODSH_EMBED_CACHE", &models)
            .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped());
        #[cfg(windows)] { use std::os::windows::process::CommandExt; cmd.creation_flags(0x0800_0000); }
        let out = cmd.output().map_err(|e| e.to_string())?;
        if !out.status.success() { return Err(String::from_utf8_lossy(&out.stderr).trim().to_string()); }
        serde_json::from_slice::<Vec<f32>>(&out.stdout).map_err(|e| format!("向量解析失败: {e}"))
    }).await.map_err(|e| e.to_string())?
}

/// 读数据库目录 + 预计算向量(资源文件),供 DatabaseView 渲染与语义搜索。
#[tauri::command]
fn db_catalog(app: AppHandle) -> Result<serde_json::Value, String> {
    let dir = crate::paths::resource(&app, "databases");
    let cat: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(dir.join("catalog.json")).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let vecs: serde_json::Value = std::fs::read_to_string(dir.join("vectors.json")).ok()
        .and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_else(|| serde_json::json!({}));
    Ok(serde_json::json!({ "catalog": cat, "vectors": vecs }))
}

/// 轻量差量更新:查 delta.json(验签+比版本)。有更新返回 Delta,调用方据 fullRequired 决定走轻量还是整包。
#[tauri::command]
async fn light_update_check() -> Result<Option<updater::Delta>, String> {
    Ok(None)
}

/// 轻量差量更新:下载并校验 delta 里的变化文件(落地 .new)→ 停 dsh → 生成替换助手 → 退出重启。
#[tauri::command]
async fn light_update_apply(_app: AppHandle, _state: State<'_, AppState>, _delta: updater::Delta) -> Result<(), String> {
    Err("OMaaX 尚未配置独立更新源，无法安装更新".into())
}

fn dsh_version(app: &AppHandle) -> String {
    let f = paths::resource(app, "dsh/node_modules").join("@deepseek-ai").join("dsh").join("package.json");
    std::fs::read_to_string(f).ok().and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok()).and_then(|v| v.get("version").and_then(|x| x.as_str()).map(String::from)).unwrap_or_else(|| "?".into())
}

/// 检查更新：dsh 内核最新版（npm registry）与本应用最新版（GitHub Releases，可选）
#[tauri::command]
async fn check_updates(app: AppHandle, state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    if state.settings.lock().unwrap().mode == "offline" { return Err("offline-mode".into()); }
    let current = dsh_version(&app);
    let app_version = app.package_info().version.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        let reg = ureq::get("https://registry.npmmirror.com/@deepseek-ai/dsh").timeout(std::time::Duration::from_secs(15)).call().map_err(|e| e.to_string())?;
        let v: serde_json::Value = reg.into_json().map_err(|e| e.to_string())?;
        let latest = v["dist-tags"]["latest"].as_str().unwrap_or("?").to_string();
        Ok(serde_json::json!({ "dsh": { "current": current, "latest": latest, "outdated": latest != "?" && latest != current }, "app": { "current": app_version } }))
    }).await.map_err(|e| e.to_string())?
}

/// 右键"问一下"：直接调 DeepSeek 聊天接口，非流式，回答简短。
#[tauri::command]
async fn assistant_ask(state: State<'_, AppState>, model: String, question: String, context: String) -> Result<String, String> {
    let (endpoint, key, model) = {
        let st = state.settings.lock().unwrap();
        if st.mode == "offline" {
            if st.offline_base_url.trim().is_empty() { return Err("还没有配置本地／内网模型，请在设置的「模型连接」中填写接口地址".into()); }
            (format!("{}/chat/completions", st.offline_base_url.trim().trim_end_matches('/')), if st.offline_api_key.is_empty() { "local".into() } else { st.offline_api_key.clone() }, if st.offline_model.trim().is_empty() { model } else { st.offline_model.trim().to_string() })
        } else {
            let Some(key) = settings::read_credential_value(&state.paths) else { return Err("还没填 API Key，去「更多 → 模型 API Key」填一下".into()) };
            ("https://api.deepseek.com/chat/completions".to_string(), key, model)
        }
    };
    tauri::async_runtime::spawn_blocking(move || {
        let system = format!("你是 OMaaX 桌面软件的内置向导。用户是不懂生信和编程的医生/实验人员，请用最通俗的中文、不超过 120 字回答，必要时分 1-3 步。当前界面上下文：{context}");
        let body = serde_json::json!({ "model": model, "messages": [{ "role": "system", "content": system }, { "role": "user", "content": question }], "temperature": 0.3, "max_tokens": 400 });
        let resp = ureq::post(&endpoint).set("Authorization", &format!("Bearer {key}")).set("Content-Type", "application/json").timeout(std::time::Duration::from_secs(60)).send_json(body)
            .map_err(|e| match e { ureq::Error::Status(c, r) => format!("HTTP {c}: {}", r.into_string().unwrap_or_default().chars().take(200).collect::<String>()), e => e.to_string() })?;
        let v: serde_json::Value = resp.into_json().map_err(|e| e.to_string())?;
        v["choices"][0]["message"]["content"].as_str().map(String::from).ok_or_else(|| "没有得到回答".into())
    }).await.map_err(|e| e.to_string())?
}
/// 导出对话记录（dsh 的 session.export 是 GET 下载接口，不是 RPC）→ 让用户选保存位置
#[tauri::command]
async fn session_export(app: AppHandle, state: State<'_, AppState>, session_id: String) -> Result<Option<String>, String> {
    let url = dsh_url(&state)?;
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().set_file_name(format!("biodsh-对话-{}.zip", &session_id.chars().take(12).collect::<String>())).add_filter("对话记录", &["zip"]).save_file(move |p| { let _ = tx.send(p); });
    let Some(target) = tauri::async_runtime::spawn_blocking(move || rx.recv().ok().flatten()).await.map_err(|e| e.to_string())? else { return Ok(None) };
    let target = target.to_string();
    let t2 = target.clone();
    tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        let mut req = ureq::get(&format!("{}/api/session/export?sessionId={}&includeDescendants=true", origin_of(&url), session_id)).timeout(std::time::Duration::from_secs(120));
        if let Some(c) = auth_cookie(&url, false) { req = req.set("Cookie", &c); }
        let resp = req.call().map_err(|e| e.to_string())?;
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut resp.into_reader(), &mut bytes).map_err(|e| e.to_string())?;
        std::fs::write(&t2, bytes).map_err(|e| e.to_string())
    }).await.map_err(|e| e.to_string())??;
    Ok(Some(target))
}
#[tauri::command]
async fn workspace_files(state: State<'_, AppState>, path: Option<String>) -> Result<Vec<files::FileEntry>, String> {
    if let (Some(f), Some(p)) = (state.files.as_ref(), path.as_ref()) { f.add_root(std::path::PathBuf::from(p)); }
    let ws = path.filter(|p| !p.trim().is_empty()).unwrap_or_else(|| workspace_of(&state));
    hub_auth::local_path(&state.paths.root,&ws)?;
    Ok(tauri::async_runtime::spawn_blocking(move || files::list(&ws)).await.map_err(|e| e.to_string())?)
}
#[tauri::command]
async fn read_workspace_image(state: State<'_, AppState>, rel: String, path: Option<String>) -> Result<String, String> {
    let ws = path.filter(|p| !p.trim().is_empty()).unwrap_or_else(|| workspace_of(&state));
    hub_auth::local_path(&state.paths.root,&ws)?;
    tauri::async_runtime::spawn_blocking(move || files::read_image(&ws, &rel)).await.map_err(|e| e.to_string())?
}
fn ratings_path(state: &AppState) -> std::path::PathBuf { state.paths.root.join("ratings.json") }
#[tauri::command]
fn ratings_get(state: State<'_, AppState>) -> serde_json::Value {
    std::fs::read_to_string(ratings_path(&state)).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_else(|| serde_json::json!({}))
}
#[tauri::command]
fn ratings_set(state: State<'_, AppState>, id: String, vote: i32, comment: String) -> serde_json::Value {
    let mut all = ratings_get(state.clone());
    if vote == 0 { if let Some(o) = all.as_object_mut() { o.remove(&id); } }
    else {
        all[&id] = serde_json::json!({ "vote": vote, "comment": comment, "at": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) });
    }
    let _ = std::fs::write(ratings_path(&state), serde_json::to_string_pretty(&all).unwrap_or_default());
    all
}
/// 永久删除一个对话：先通过 dsh 归档（从列表消失），再删除它在 $DSH_HOME/sessions/*/ 下的记录目录
#[tauri::command]
async fn session_delete(state: State<'_, AppState>, session_id: String) -> Result<u32, String> {
    if !session_id.starts_with("session-") || session_id.contains(['/', '\\', '.']) { return Err("非法会话 id".into()); }
    if let Ok(url) = dsh_url(&state) {
        let (u, sid) = (url.clone(), session_id.clone());
        let _ = tauri::async_runtime::spawn_blocking(move || dsh_call(&u, "workspace.archiveSession", serde_json::json!({ "sessionId": sid }))).await;
    }
    let root = state.paths.dsh_home.join("sessions");
    let sid = session_id.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut removed = 0u32;
        if let Ok(rd) = std::fs::read_dir(&root) {
            for e in rd.flatten() {
                let p = e.path().join(&sid);
                if p.is_dir() && std::fs::remove_dir_all(&p).is_ok() { removed += 1; }
                let f = e.path().join(format!("{sid}.jsonl"));
                if f.is_file() && std::fs::remove_file(&f).is_ok() { removed += 1; }
            }
        }
        Ok(removed)
    }).await.map_err(|e| e.to_string())?
}

/// 计算环境扩展包：往 bioenv 里追加安装一组软件包（uv pip install），进度走 env 事件
#[tauri::command]
async fn env_install_extra(app: AppHandle, state: State<'_, AppState>, packages: Vec<String>) -> Result<pyenv::EnvStatus, String> {
    let (py, paths, china) = (Arc::clone(&state.pyenv), state.paths.clone(), state.settings.lock().unwrap().use_china_mirror);
    tauri::async_runtime::spawn_blocking(move || py.install_extra(&app, &paths, china, &packages)).await.map_err(|e| e.to_string())
}
#[tauri::command]
async fn migrate_scan(app: AppHandle) -> Result<Vec<migrate::Source>, String> {
    let home = app.path().home_dir().map_err(|e| e.to_string())?;
    Ok(tauri::async_runtime::spawn_blocking(move || migrate::scan(&home)).await.map_err(|e| e.to_string())?)
}
#[tauri::command]
async fn migrate_import(app: AppHandle, state: State<'_, AppState>, source_id: String) -> Result<migrate::ImportResult, String> {
    let home = app.path().home_dir().map_err(|e| e.to_string())?;
    let (skills_root, ws) = (state.paths.skills.clone(), std::path::PathBuf::from(workspace_of(&state)));
    Ok(tauri::async_runtime::spawn_blocking(move || migrate::import(&home, &skills_root, &ws, &source_id)).await.map_err(|e| e.to_string())?)
}
#[tauri::command]
fn open_path(app: AppHandle,state: State<'_,AppState>, path: String) -> Result<(),String> { hub_auth::local_path(&state.paths.root,&path)?; app.opener().open_path(path, None::<&str>).map_err(|e|e.to_string()) }
#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(),String> { let url=hub_auth::external_url(&url)?;app.opener().open_url(url,None::<&str>).map_err(|e|e.to_string()) }
#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |p| { let _ = tx.send(p); });
    tauri::async_runtime::spawn_blocking(move || rx.recv().ok().flatten().map(|p| p.to_string())).await.ok().flatten()
}
#[tauri::command]
async fn window_control(app: AppHandle, action: String) {
    let Some(w) = app.get_window("main") else { return };
    match action.as_str() {
        "minimize" => { let _ = w.minimize(); }
        "maximize" => { if w.is_maximized().unwrap_or(false) { let _ = w.unmaximize(); } else { let _ = w.maximize(); } }
        "close" => { let _ = w.close(); }
        _ => {}
    }
}

fn build_main_window(app: &AppHandle) -> tauri::Result<Window> {
    let builder = tauri::window::WindowBuilder::new(app, "main")
        .title("OMaaX")
        .inner_size(1240.0, 720.0)
        .min_inner_size(960.0, 600.0)
        .center();
    #[cfg(target_os = "macos")]
    let builder = builder.title_bar_style(tauri::TitleBarStyle::Overlay).hidden_title(true);
    #[cfg(not(target_os = "macos"))]
    let builder = builder.decorations(false);
    let window = builder.build()?;
    if let Some(icon) = app.default_window_icon() { window.set_icon(icon.clone())?; }
    let size = window.inner_size()?.to_logical::<f64>(window.scale_factor()?);
    let _ui = window.add_child(
        tauri::webview::WebviewBuilder::new("ui", WebviewUrl::App("index.html".into())).data_directory(app.state::<AppState>().paths.root.join("webview-ui")).disable_drag_drop_handler().auto_resize(),
        LogicalPosition::new(0.0, 0.0),
        LogicalSize::new(size.width, size.height),
    )?;
    #[cfg(target_os = "linux")]
    linux_layout::prepare_shell(&_ui)?;
    Ok(window)
}

#[tauri::command]
async fn education_start(app:AppHandle,state:State<'_,AppState>)->Result<String,String>{
 let root=state.paths.root.clone();tauri::async_runtime::spawn_blocking(move||education::start(&app,&root)).await.map_err(|e|e.to_string())?
}
#[tauri::command]
async fn education_delete(app:AppHandle,state:State<'_,AppState>,path:String)->Result<serde_json::Value,String>{
 let root=state.paths.root.clone();tauri::async_runtime::spawn_blocking(move||education::delete(&app,&root,&path)).await.map_err(|e|e.to_string())?
}
#[tauri::command]
async fn hub_status(app: AppHandle, state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let root=state.paths.root.clone();
    let value=tauri::async_runtime::spawn_blocking(move || hub_auth::status(&root)).await.map_err(|e|e.to_string())?;
    if value["authenticated"] != true { education::stop(); state.dsh.stop(&app); if let Some(v)=app.get_webview("dsh") { let _=v.close(); } }
    Ok(value)
}
#[tauri::command]
async fn cloud_qa(app:AppHandle,state:State<'_,AppState>,action:String,body:serde_json::Value)->Result<serde_json::Value,String>{
    let root=state.paths.root.clone();
    tauri::async_runtime::spawn_blocking(move||{
        cloud_qa::route(&action,&body)?;
        hub_auth::cloud_session(&root)?;
        if action=="delete" && !app.dialog().message("删除此知识库会话及全部消息？此操作不可撤销。").title("确认删除").buttons(tauri_plugin_dialog::MessageDialogButtons::OkCancel).blocking_show(){return Err("已取消删除".into());}
        cloud_qa::call(&root,&action,body)
    }).await.map_err(|_|"知识库请求未完成".to_string())?
}
#[tauri::command]
async fn hub_login(app: AppHandle, state: State<'_, AppState>, hub: String, username: String, password: String, remember: Option<bool>) -> Result<(),String> {
    education::stop(); state.dsh.stop(&app);
    tauri::async_runtime::spawn_blocking(move || {
        hub_auth::login(&hub,&username,&password)?;
        if let Err(error)=hub_auth::remember_login(&username,&password,remember.unwrap_or(false)).and_then(|_|hub_auth::arm_login_restart()) {
            let _=hub_auth::logout();return Err(error);
        }
        Ok::<(),String>(())
    }).await.map_err(|e|e.to_string())??;
    app.restart();
}
#[tauri::command]
async fn hub_remembered_login(webview:tauri::Webview)->Result<Option<hub_auth::RememberedLogin>,String>{
    if webview.label()!="ui" {return Err("FORBIDDEN: 仅登录界面可读取保存的登录信息".into());}
    tauri::async_runtime::spawn_blocking(hub_auth::remembered_login).await.map_err(|e|e.to_string())?
}
#[tauri::command]
async fn hub_forget_login(webview:tauri::Webview)->Result<(),String>{
    if webview.label()!="ui" {return Err("FORBIDDEN: 仅登录界面可修改保存的登录信息".into());}
    tauri::async_runtime::spawn_blocking(hub_auth::forget_login).await.map_err(|e|e.to_string())?
}
#[tauri::command]
async fn hub_logout(app: AppHandle, state: State<'_, AppState>) -> Result<(),String> {
    education::stop(); state.dsh.stop(&app); if let Some(v)=app.get_webview("dsh") { let _=v.close(); }
    tauri::async_runtime::spawn_blocking(hub_auth::logout).await.map_err(|e|e.to_string())??;
    app.restart();
}
#[tauri::command]
async fn hub_admin(app:AppHandle,state:State<'_,AppState>) -> Result<(),String> {
    let root=state.paths.root.clone();let user=tauri::async_runtime::spawn_blocking(move||hub_auth::require(&root,"")).await.map_err(|e|e.to_string())??;
    if user.role!="admin" {return Err("仅管理员可以打开管理后台".into());}
    app.opener().open_url(format!("{}/admin/",hub_auth::configured_hub()),None::<&str>).map_err(|_|"无法打开管理后台".into())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let paths = AppPaths::detect(&handle);
            // 版本升级后 WebView2 可能继续用缓存里的旧页面（程序是新的、界面是旧的）。
            // 版本号变化时清掉 HTTP/代码缓存（保留 localStorage 等站点数据）。
            {
                let stamp = paths.root.join(".app-version");
                let current = handle.package_info().version.to_string();
                let prev = std::fs::read_to_string(&stamp).unwrap_or_default();
                if prev.trim() != current {
                    if let Ok(local) = handle.path().app_local_data_dir() {
                        for sub in ["EBWebView/Default/Cache", "EBWebView/Default/Code Cache", "EBWebView/Default/GPUCache"] {
                            let _ = std::fs::remove_dir_all(local.join(sub));
                        }
                    }
                    // dsh 的会话渲染缓存在升级过程中可能损坏/失配（表现为个别会话打不开、提示重启也无效）。
                    // 它可从原始会话日志重建，版本变化时删掉让它重建；原始记录（sessions/）不受影响。
                    let _ = std::fs::remove_file(paths.dsh_home.join("storages").join("session_projcache.json"));
                    let _ = std::fs::write(&stamp, &current);
                }
            }
            // Tools are installed explicitly by the user in the tool center.
            let settings = AppSettings::load(&paths);
            // 本地只读图片服务：让对话里能内嵌显示工作区里的图
            let files = files_server::FilesServer::start(vec![paths.root.clone(), std::path::PathBuf::from(&settings.workspace)]);
            let _ = TITLES_PATH.set(paths.dsh_home.join("storages").join("biodsh-project-titles.json"));

            app.manage(AppState { paths, settings: Mutex::new(settings), dsh: Arc::new(DshManager::new()), pyenv: Arc::new(PyEnvManager::new()), dsh_bounds: Mutex::new((Rect::default(), false)), files });
            let watchdog=handle.clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(15));
                let st=watchdog.state::<AppState>();
                if hub_auth::require(&st.paths.root, "").is_err() {
                    st.dsh.stop(&watchdog);
                    if let Some(view)=watchdog.get_webview("dsh") {let _=view.close();}
                    let _=watchdog.emit("hub-locked", ());
                }
            });
            build_main_window(&handle)?;
            Ok(())
        })
        .on_page_load(|webview, payload| {
            if let Some(st) = webview.app_handle().try_state::<AppState>() { trace(&st.paths, &format!("page_load {} {:?} {}", webview.label(), payload.event(), payload.url())); }
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if window.label() == "main" { education::stop(); let app = window.app_handle(); if let Some(st) = app.try_state::<AppState>() { st.dsh.stop(app); } }
            }
        })
        .invoke_handler(|invoke: tauri::ipc::Invoke| {
            let app=invoke.message.webview().app_handle().clone();
            tauri::async_runtime::spawn_blocking(move || {
                let check=(|| { let unit=hub_auth::command_unit(invoke.message.command())?; if let Some(unit)=unit { let st=app.state::<AppState>();let user=hub_auth::require(&st.paths.root,unit)?;if matches!(invoke.message.command(),"personal_compute"|"ssh_hosts_save"|"ssh_hosts_remove"|"session_delete"|"session_export"){hub_auth::audit(&st.paths.root,&user,invoke.message.command());} } Ok::<(),String>(()) })();
                if let Err(error)=check { invoke.resolver.reject(error); return; }
                let handler: Box<dyn Fn(tauri::ipc::Invoke)->bool> = Box::new(tauri::generate_handler![cloud_qa,education_start,education_delete,hub_status, hub_login, hub_remembered_login, hub_forget_login, hub_logout, hub_admin,
            app_info, settings_get, settings_set, credential_get, credential_set, personal_compute, platform_session, platform_login, platform_project_collaboration, platform_storage, platform_logout, platform_nodes, platform_node_metrics, platform_update_node, platform_jobs, platform_job_kinds, platform_create_job, platform_scheduling_nodes, platform_scheduling, ssh_hosts_bind, platform_cancel_job, platform_job_events, platform_job_artifacts, platform_download_job_artifact,
            knowledge_credential, knowledge_set_credential, knowledge_datasets, knowledge_create_dataset, knowledge_documents, knowledge_add_files, knowledge_folders, knowledge_add_folder, knowledge_remove_folder, knowledge_sync_folders, knowledge_search,
            ssh_hosts_list, ssh_hosts_save, ssh_hosts_remove, ssh_hosts_select, ssh_hosts_trust, ssh_hosts_test, ssh_hosts_metrics, ssh_hosts_files,
            env_status, env_install, dsh_status, dsh_start, dsh_restart, dsh_stop, dsh_reload, dsh_bounds,
            skills_catalog, skills_statuses, skills_readme, skills_install, skills_uninstall,
            open_path, open_external, pick_folder, window_control, client_log, deepseek_balance, git_status, git_init, git_commit, dsh_rpc, dsh_open_session, dsh_new_session, check_updates, assistant_ask, session_export, workspace_files, read_workspace_image, ratings_get, ratings_set, session_delete, env_install_extra, migrate_scan, migrate_import, refdata_list, refdata_install, refdata_remove, demos_seed, dsh_set_context, embed_query, db_catalog, light_update_check, light_update_apply
                ]);
                handler(invoke);
            });
            true
        })
        .run(tauri::generate_context!())
        .expect("error while running OMaaX");
}

#[tauri::command]
fn platform_scheduling_nodes(state: State<'_, AppState>) -> Result<serde_json::Value, String> { platform_auth::scheduling_nodes(&state.settings.lock().unwrap().platform_url) }
#[tauri::command]
fn platform_scheduling(state: State<'_, AppState>, job_id: String) -> Result<serde_json::Value, String> { platform_auth::scheduling(&state.settings.lock().unwrap().platform_url, &job_id) }
#[tauri::command]
fn ssh_hosts_bind(state: State<'_, AppState>, id: String, node_id: Option<String>) -> Result<Vec<ssh_hosts::Host>, String> {
    if let Some(ref value) = node_id {
        let nodes = platform_auth::scheduling_nodes(&state.settings.lock().unwrap().platform_url)?;
        if !nodes.as_array().is_some_and(|items| items.iter().any(|node| node["id"].as_str() == Some(value))) { return Err("计算节点不存在".into()); }
    }
    ssh_hosts::bind(&state.paths.root, &id, node_id)
}

#[tauri::command]
async fn platform_project_collaboration(state: State<'_, AppState>, project_id: String, action: String, body: serde_json::Value) -> Result<serde_json::Value, String> {
    let url=state.settings.lock().unwrap().platform_url.clone();
    tauri::async_runtime::spawn_blocking(move || platform_auth::project_collaboration(&url, &project_id, &action, body)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn platform_storage(state: State<'_, AppState>, action: String, body: serde_json::Value) -> Result<serde_json::Value, String> {
    let url = state.settings.lock().unwrap().platform_url.clone();
    tauri::async_runtime::spawn_blocking(move || platform_auth::storage(&url, &action, body)).await.map_err(|e| e.to_string())?
}
