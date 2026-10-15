//! Only the project-scoped capability is readable here; no platform refresh token is used.
use serde_json::{json, Value};
use std::io::{BufRead, Write};

pub fn run() {
    let url = std::env::var("BIODSH_AGENT_PLATFORM").unwrap_or_default();
    let project = std::env::var("BIODSH_AGENT_PROJECT").unwrap_or_default();
    for line in std::io::stdin().lock().lines().map_while(Result::ok) {
        if line.len() > 262144 { break; }
        let Ok(request) = serde_json::from_str::<Value>(&line) else { continue };
        let Some(id) = request.get("id") else { continue };
        let response = (|| -> Result<Value, String> {
            let secret = keyring::Entry::new("BioDSH.ProtectedAgent", "desktop-agent-capability")
                .map_err(|_| "安全存储不可用")?.get_password().map_err(|_| "请在计算任务页授权对话使用所选数据")?;
            let grant: Value = serde_json::from_str(&secret).map_err(|_| "授权无效")?;
            if !scope_matches(&grant, &url, &project) { return Err("请重新授权当前项目".into()); }
            let endpoint = grant["endpoint"].as_str().ok_or("授权无效")?;
            let token = grant["token"].as_str().ok_or("授权无效")?;
            ureq::post(endpoint).timeout(std::time::Duration::from_secs(20))
                .set("Authorization", &format!("Bearer {token}"))
                .send_json(request.clone()).map_err(|_| "对话授权已过期或平台不可用，请重新授权")?
                .into_json().map_err(|_| "工具返回格式错误".into())
        })();
        let value = response.unwrap_or_else(|message| json!({"jsonrpc":"2.0", "id":id,
            "error":{"code":-32001,"message":message}}));
        println!("{value}"); let _ = std::io::stdout().flush();
    }
}

fn scope_matches(grant: &Value, url: &str, project: &str) -> bool {
    !url.is_empty() && !project.is_empty()
        && grant["platform"].as_str() == Some(url)
        && grant["project"].as_str() == Some(project)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_project_or_platform_cannot_reuse_current_grant() {
        let grant = json!({"platform":"https://platform", "project":"a"});
        assert!(scope_matches(&grant, "https://platform", "a"));
        assert!(!scope_matches(&grant, "https://platform", "b"));
        assert!(!scope_matches(&grant, "https://other", "a"));
        assert!(!scope_matches(&json!({}), "", ""));
    }
}
