//! Only the project-scoped capability is readable here; no platform refresh token is used.
use serde_json::{json, Value};
use std::io::{BufRead, Write};

pub fn run() {
    for line in std::io::stdin().lock().lines().map_while(Result::ok) {
        if line.len() > 262144 { break; }
        let Ok(request) = serde_json::from_str::<Value>(&line) else { continue };
        let Some(id) = request.get("id") else { continue };
        let value=json!({"jsonrpc":"2.0", "id":id,"error":{"code":-32001,"message":"旧平台工具未接入账号隔离，已停用"}});
        println!("{value}"); let _ = std::io::stdout().flush();
    }
}

#[cfg(test)]
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
