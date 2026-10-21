//! The lock is on the data directory, not the installation path. Never delete it:
//! OS ownership disappears on exit/crash, while the inode must remain stable.
use std::{fs::{File, OpenOptions}, path::{Path, PathBuf}, sync::Mutex};
static OWNER: Mutex<Option<File>> = Mutex::new(None);

pub fn claim(root: &Path) -> std::io::Result<bool> {
    std::fs::create_dir_all(root)?;
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(root.join(".desktop-owner"))?;
    match file.try_lock() {
        Ok(()) => { *OWNER.lock().unwrap() = Some(file); Ok(true) }
        Err(std::fs::TryLockError::WouldBlock) => {
            std::fs::write(root.join(".desktop-activate"), uuid::Uuid::new_v4().to_string())?;
            Ok(false)
        }
        Err(std::fs::TryLockError::Error(e)) => Err(e),
    }
}

pub fn release() { OWNER.lock().unwrap().take(); }

pub fn data_root() -> PathBuf {
    crate::paths::strip_unc(std::env::var_os("OMAAX_HOME").or_else(||std::env::var_os("BIODSH_HOME")).map(PathBuf::from)
        .unwrap_or_else(|| {
            let home=PathBuf::from(std::env::var_os("USERPROFILE").or_else(||std::env::var_os("HOME")).expect("home directory"));
            let legacy=home.join("BioDSH"); if legacy.exists() {legacy} else {home.join("OMaaX")}
        }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn one_owner_until_release_without_removing_lock_file() {
        let root=std::env::temp_dir().join(format!("omaax-owner-{}",uuid::Uuid::new_v4()));
        assert!(claim(&root).unwrap());
        assert!(!claim(&root).unwrap());
        assert!(root.join(".desktop-activate").exists());
        release();
        assert!(claim(&root).unwrap());
        release();
        std::fs::remove_dir_all(root).unwrap();
    }
}
