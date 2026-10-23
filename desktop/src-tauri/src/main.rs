// Windows 上 release 模式不弹控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
include!(concat!(env!("OUT_DIR"), "/local_code_key.rs"));

fn main() {
    // Only child processes receive this local code key; it is not a model credential.
    std::env::set_var("OMAAX_LOCAL_CODE_KEY", LOCAL_CODE_KEY);
    if std::env::args().any(|arg| arg == "--company-llm-session") { biodsh_desktop_lib::company_session_cli(); return; }
    if std::env::args().any(|arg| arg == "--auth-check") { biodsh_desktop_lib::check_model(); return; }
    if std::env::args().any(|arg| arg == "--platform-mcp") { biodsh_desktop_lib::run_platform_mcp(); return; }
    if std::env::args().any(|arg| arg == "--ssh-mcp") { biodsh_desktop_lib::run_mcp(); return; }
    biodsh_desktop_lib::run();
}
