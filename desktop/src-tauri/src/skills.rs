//! 工具中心：目录在 resources/skills/catalog.json；安装 = 复制到 $DSH_HOME/skills/<id>。
use crate::paths::{resource, AppPaths};
use serde::Serialize;
use serde_json::Value;
use std::fs;
use std::path::Path;
use tauri::AppHandle;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkillStatus {
    pub id: String,
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub installed_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

pub fn catalog(app: &AppHandle) -> Vec<Value> {
    let f = resource(app, "skills").join("catalog.json");
    fs::read_to_string(f).ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.get("skills").and_then(|s| s.as_array().cloned()))
        .unwrap_or_default()
}

fn find(app: &AppHandle, id: &str) -> Option<Value> {
    catalog(app).into_iter().find(|s| s.get("id").and_then(|x| x.as_str()) == Some(id))
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 160 && id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

pub fn statuses(app: &AppHandle, p: &AppPaths) -> Vec<SkillStatus> {
    catalog(app).iter().map(|s| {
        let id = s["id"].as_str().unwrap_or_default().to_string();
        let dir = p.skills.join(&id);
        let installed = valid_id(&id) && dir.join("SKILL.md").is_file();
        let version = if installed { fs::read_to_string(dir.join(".biodsh-version")).ok().map(|v| v.trim().to_string()) } else { None };
        SkillStatus { id, state: if installed { "installed" } else { "not_installed" }.into(), installed_version: version, error: None }
    }).collect()
}

fn failure(id: &str, message: impl ToString) -> SkillStatus {
    SkillStatus { id: id.into(), state: "error".into(), installed_version: None, error: Some(message.to_string()) }
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dst)?;
    for e in fs::read_dir(src)? {
        let e = e?;
        let name = e.file_name();
        let n = name.to_string_lossy();
        if n == "__pycache__" || n.ends_with(".pyc") { continue; }
        let t = dst.join(&name);
        if e.file_type()?.is_dir() { copy_dir(&e.path(), &t)?; } else { fs::copy(e.path(), t)?; }
    }
    Ok(())
}

pub fn install(app: &AppHandle, p: &AppPaths, id: &str) -> SkillStatus {
    let Some(skill) = find(app, id) else {
        return SkillStatus { id: id.into(), state: "error".into(), installed_version: None, error: Some("目录中没有这个技能".into()) };
    };
    if !valid_id(id) { return failure(id, "工具名称无效"); }
    let bundle = skill["bundle"].as_str().unwrap_or("skills");
    let src = resource(app, bundle).join(id);
    let final_dst = p.skills.join(id);
    if final_dst.exists() { return failure(id, "此工具目录已存在，请先检查已添加的工具，不会覆盖原有文件"); }
    let dst = p.root.join("tool-downloads").join(uuid::Uuid::new_v4().to_string());
    let prepare = (|| -> Result<(), String> {
        fs::create_dir_all(&dst).map_err(|e| e.to_string())?;
        if skill["tier"].as_str() == Some("community") {
            let mut command = std::process::Command::new(crate::paths::node_binary(app));
            command.arg(resource(app, "scripts").join("download-skill.mjs")).arg(skill.to_string()).arg(&dst);
            command.env_remove("NODE_OPTIONS").env("NODE_USE_ENV_PROXY", "0");
            #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x0800_0000); }
            let output = command.output().map_err(|e| e.to_string())?;
            if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).trim().to_string()); }
        } else { copy_dir(&src, &dst).map_err(|e| e.to_string())?; }
        if !dst.join("SKILL.md").is_file() { return Err("下载内容缺少工具说明，未添加工具".into()); }
        Ok(())
    })();
    if let Err(e) = prepare { let _ = fs::remove_dir_all(&dst); return failure(id, e); }
    let md = dst.join("SKILL.md");
    let dst_s = final_dst.to_string_lossy().replace('\\', "/");
    let note = if skill["tier"].as_str() == Some("community") {
        let repo = skill["origin"]["repo"].as_str().unwrap_or("a community repository");
        let lic = skill["origin"]["license"].as_str().unwrap_or("see repo license");
        if skill["sourceGuide"].is_object() {
            format!("\n\n## Running in OMaaX Desktop\n\nThis is a user-added source guide for {repo} ({lic}), stored at `{dst_s}`. Read its local references and configure only this tool's required software on the user's chosen computer or SSH server. Adding this guide does not install the analysis software. Use the tool's actual language and runtime, not necessarily Python. Keep original inputs and store new outputs in the user's project/task output directory. Explain results in plain language.\n")
        } else {
        format!("\n\n## Running in OMaaX Desktop\n\nThis skill was collected from {repo} ({lic}). The OMaaX Python environment (scanpy, anndata, pandas, …) is first on PATH, so `python` resolves to it; install any extra package the skill needs with `uv pip install <pkg>` (uv is on PATH) or `python -m pip install <pkg>`. Scripts referenced by this skill live in `{dst_s}`. Explain results to the user in plain language.\n")
        }
    } else {
        let outputs: Vec<String> = skill["outputs"].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
{
        let script = skill["entry"]["script"].as_str().unwrap_or("run.py");
        if script == "run.py" {
            format!("\n\n## Running in OMaaX Desktop\n\nThe OMaaX Python environment (scanpy, anndata, …) is already first on PATH; `python` resolves to it. Run this skill's script directly:\n\n```bash\npython \"{dst_s}/run.py\" --input <input file> --outdir <output directory> --seed 0\n```\n\nWrite outputs into a fresh directory under the current workspace, then summarize the generated files ({}) for the user in plain language.\n", outputs.join(", "))
        } else {
            format!("\n\n## Running in OMaaX Desktop\n\nThis skill is installed at `{dst_s}`; its script is `{dst_s}/{script}` (run it with `python`, which resolves to the OMaaX analysis environment — see the command lines above). Write any outputs into a new subfolder of the current workspace and report their paths to the user in plain language.\n")
        }
    }
    };
    if let Ok(t) = fs::read_to_string(&md) { let _ = fs::write(&md, t.trim_end().to_string() + &note); }
    let version = skill["version"].as_str().unwrap_or("1.0.0").to_string();
    let _ = fs::write(dst.join(".biodsh-version"), format!("{version}\n"));
    if let Err(e) = fs::create_dir_all(&p.skills).and_then(|_| fs::rename(&dst, &final_dst)) {
        let _ = fs::remove_dir_all(&dst);
        return failure(id, e);
    }
    SkillStatus { id: id.into(), state: "installed".into(), installed_version: Some(version), error: None }
}

pub fn uninstall(p: &AppPaths, id: &str) -> SkillStatus {
    if !valid_id(id) { return failure(id, "工具名称无效"); }
    let target = p.skills.join(id);
    if target.symlink_metadata().map(|m| m.file_type().is_symlink()).unwrap_or(false) { return failure(id, "此工具目录是链接，请手动处理"); }
    if target.exists() { if let Err(e) = fs::remove_dir_all(&target) { return failure(id, e); } }
    SkillStatus { id: id.into(), state: "not_installed".into(), installed_version: None, error: None }
}

pub fn readme(app: &AppHandle, p: &AppPaths, id: &str) -> String {
    if !valid_id(id) { return String::new(); }
    let bundle = find(app, id).and_then(|s| s["bundle"].as_str().map(String::from)).unwrap_or("skills".into());
    let f = resource(app, &bundle).join(id).join("SKILL.md");
    let t = fs::read_to_string(p.skills.join(id).join("SKILL.md")).or_else(|_| fs::read_to_string(f)).unwrap_or_else(|_| "添加工具后可查看完整说明；来源和用途请查看概览。".into());
    // 去掉 frontmatter
    if let Some(rest) = t.strip_prefix("---") {
        if let Some(i) = rest.find("\n---") { return rest[i + 4..].trim_start().to_string(); }
    }
    t
}

#[cfg(test)]
mod tests {
    #[test]
    fn reject_traversal_ids() {
        assert!(super::valid_id("bulk-rna_seq"));
        for id in ["", "..", "../data", "a/b", "a\\b", "C:temp"] { assert!(!super::valid_id(id)); }
    }
}
