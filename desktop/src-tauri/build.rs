fn main() {
    let root = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let desktop = root.parent().unwrap();
    let output = std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let platform = match std::env::consts::OS { "windows" => "win32-x64", "macos" => if cfg!(target_arch="aarch64") {"darwin-arm64"} else {"darwin-x64"}, _ => "linux-x64" };
    let node = desktop.join("resources/node").join(platform).join(if cfg!(windows){"node.exe"}else{"bin/node"});
    for component in ["ssh", "documents", "browser"] {
        let status = std::process::Command::new(&node)
            .arg(desktop.join("scripts/protect-local-code.mjs"))
            .arg(desktop.join("resources").join(component))
            .arg(desktop.join("protected-resources").join(component))
            .arg(desktop.join("protected-resources/local-code.key"))
            .arg(output.join("local_code_key.rs"))
            .status().expect("Cannot prepare protected local modules");
        assert!(status.success(), "Local module protection failed");
        println!("cargo:rerun-if-changed=../resources/{component}");
    }
    println!("cargo:rerun-if-changed=../scripts/protect-local-code.mjs");
    tauri_build::build()
}
