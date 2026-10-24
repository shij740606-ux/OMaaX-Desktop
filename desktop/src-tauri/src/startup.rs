use std::sync::OnceLock;

const SKIP_INTRO: &str = "OMAAX_SKIP_STARTUP_INTRO";
static LAUNCH: OnceLock<serde_json::Value> = OnceLock::new();

fn new_launch() -> serde_json::Value {
    let skip = std::env::var(SKIP_INTRO).ok().as_deref() == Some("1");
    std::env::remove_var(SKIP_INTRO);
    serde_json::json!({"id": uuid::Uuid::new_v4().to_string(), "skipIntro": skip})
}

pub fn initialize() { LAUNCH.get_or_init(new_launch); }

pub fn initialization_script() -> String {
    format!("window.__OMAAX_STARTUP__={};", LAUNCH.get_or_init(new_launch))
}

// Account switching restarts the process; this display-only marker never grants access.
pub fn arm_account_restart() { std::env::set_var(SKIP_INTRO, "1"); }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn internal_restart_skips_once_and_a_fresh_launch_plays_again() {
        let previous = std::env::var_os(SKIP_INTRO);
        std::env::remove_var(SKIP_INTRO);
        let cold = new_launch();
        assert_eq!(cold["skipIntro"], false);
        arm_account_restart();
        let login = new_launch();
        assert_eq!(login["skipIntro"], true);
        assert!(std::env::var_os(SKIP_INTRO).is_none());
        arm_account_restart();
        let logout = new_launch();
        assert_eq!(logout["skipIntro"], true);
        let reopened = new_launch();
        assert_eq!(reopened["skipIntro"], false);
        assert_ne!(cold["id"], reopened["id"]);
        assert_ne!(login["id"], logout["id"]);
        if let Some(value) = previous { std::env::set_var(SKIP_INTRO, value); }
    }
}
