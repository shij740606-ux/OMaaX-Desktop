// Windows 上 release 模式不弹控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args().any(|arg| arg == "--platform-mcp") { biodsh_desktop_lib::run_platform_mcp(); return; }
    if std::env::args().any(|arg| arg == "--ssh-mcp") { biodsh_desktop_lib::run_mcp(); return; }
    biodsh_desktop_lib::run();
}
