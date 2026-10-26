//! Account-local conversation attachments, independent of expiring proxy URLs.
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, io::{Read, Write}, path::{Path, PathBuf}, sync::Mutex};

pub const MAX_IMAGE: usize = 12 * 1024 * 1024;
const STORAGE: &str = "IMAGE_STORAGE: 图片未能保留到会话，请重试保存";
const DELETED: &str = "IMAGE_SESSION_DELETED: 此问答已删除";
const MAGIC: &[u8] = b"OMaaX-QA-image-v1\n";
static WRITES: Mutex<()> = Mutex::new(());

fn digest(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn reject_link(path: &Path) -> Result<(), String> {
    let meta = fs::symlink_metadata(path).map_err(|_| STORAGE)?;
    #[cfg(windows)] let linked = { use std::os::windows::fs::MetadataExt; meta.file_attributes() & 0x400 != 0 };
    #[cfg(not(windows))] let linked = meta.file_type().is_symlink();
    if linked || meta.file_type().is_symlink() { return Err(STORAGE.into()); }
    Ok(())
}
pub fn session_id(value: &str) -> Result<String, String> {
    uuid::Uuid::parse_str(value).map(|id| id.to_string()).map_err(|_| "会话标识无效".into())
}
fn directory(root: &Path, hub: &str, session: &str, create: bool) -> Result<Option<PathBuf>, String> {
    let session = session_id(session)?;
    let hub = crate::hub_auth::normalize(hub)?;
    if !root.is_absolute() || !root.is_dir() { return Err(STORAGE.into()); }
    for parent in root.ancestors() { reject_link(parent)?; }
    let mut path = root.to_path_buf();
    for part in ["knowledge-images".to_string(), digest(hub.as_bytes()), session] {
        path.push(part);
        match fs::symlink_metadata(&path) {
            Ok(_) => {},
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                if !create { return Ok(None); }
                match fs::create_dir(&path) { Ok(()) => {}, Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}, Err(_) => return Err(STORAGE.into()) }
            },
            Err(_) => return Err(STORAGE.into()),
        }
        reject_link(&path)?;
        if !path.is_dir() { return Err(STORAGE.into()); }
    }
    Ok(Some(path))
}
pub fn validate(bytes: &[u8], mime: &str) -> Result<(), String> {
    if bytes.is_empty() || bytes.len() > MAX_IMAGE { return Err("图片为空或超过大小限制".into()); }
    let valid = match mime {
        "image/png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg" => bytes.starts_with(b"\xff\xd8\xff"),
        "image/gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "image/webp" => bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP"),
        _ => false,
    };
    if !valid { return Err("返回内容不是支持的图片".into()); }
    Ok(())
}
fn attachment_path(directory: &Path, proxy_path: &str) -> PathBuf { directory.join(format!("{}.image", digest(proxy_path.as_bytes()))) }
fn live(directory: &Path) -> Result<(), String> {
    if fs::symlink_metadata(directory.join("deleted")).is_ok() { return Err(DELETED.into()); }
    Ok(())
}
fn read(directory: &Path, proxy_path: &str) -> Result<Option<(Vec<u8>, String)>, String> {
    live(directory)?;
    let target = attachment_path(directory, proxy_path);
    match fs::symlink_metadata(&target) {
        Ok(meta) => { reject_link(&target)?; if !meta.is_file() { return Err(STORAGE.into()); } },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(STORAGE.into()),
    }
    let mut data = Vec::new();
    fs::File::open(&target).map_err(|_| STORAGE)?.take((MAX_IMAGE + 257) as u64).read_to_end(&mut data).map_err(|_| STORAGE)?;
    // A damaged or interrupted older file is a cache miss, never an image response.
    let decode = || -> Option<(Vec<u8>, String)> {
        if data.len() > MAX_IMAGE + 256 || !data.starts_with(MAGIC) { return None; }
        let body = &data[MAGIC.len()..];
        let end = body.iter().position(|byte| *byte == b'\n')?;
        let header = std::str::from_utf8(&body[..end]).ok()?;
        let (mime, checksum) = header.split_once(' ')?;
        let bytes = &body[end + 1..];
        if validate(bytes, mime).is_err() || digest(bytes) != checksum { return None; }
        Some((bytes.to_vec(), mime.to_string()))
    };
    Ok(decode())
}
fn write(directory: &Path, proxy_path: &str, bytes: &[u8], mime: &str, check_owner: &impl Fn() -> Result<(), String>) -> Result<(), String> {
    let _lock = WRITES.lock().map_err(|_| STORAGE)?;
    live(directory)?; reject_link(directory)?;
    let target = attachment_path(directory, proxy_path);
    if fs::symlink_metadata(&target).is_ok() { reject_link(&target)?; if !target.is_file() { return Err(STORAGE.into()); } }
    let temp = directory.join(format!("{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| -> Result<(), String> {
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temp).map_err(|_| STORAGE)?;
        file.write_all(MAGIC).and_then(|_| writeln!(file, "{mime} {}", digest(bytes))).and_then(|_| file.write_all(bytes)).and_then(|_| file.sync_all()).map_err(|_| STORAGE)?;
        drop(file);
        check_owner()?; reject_link(directory)?; live(directory)?;
        fs::rename(&temp, &target).map_err(|_| STORAGE)?;
        Ok(())
    })();
    if result.is_err() { let _ = fs::remove_file(&temp); }
    result
}
pub fn get(root: &Path, hub: &str, session: Option<&str>, proxy_path: &str, refresh: bool, check_owner: impl Fn() -> Result<(), String>, fetch: impl FnOnce() -> Result<(Vec<u8>, String), String>) -> Result<Value, String> {
    if let Some(session) = session { session_id(session)?; }
    check_owner()?;
    let dir = session.map(|session| directory(root, hub, session, false)).transpose();
    if let Ok(Some(Some(dir))) = &dir {
        live(dir)?;
        if !refresh {
            if let Ok(Some((bytes, mime))) = read(dir, proxy_path) {
                check_owner()?; live(dir)?;
                return Ok(json!({"src":format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes)), "persisted":true}));
            }
        }
    }
    let (bytes, mime) = fetch()?;
    validate(&bytes, &mime)?; check_owner()?;
    let persisted = if let Some(session) = session {
        let result = directory(root, hub, session, true).and_then(|dir| write(&dir.ok_or(STORAGE)?, proxy_path, &bytes, &mime, &check_owner));
        if result.as_ref().is_err_and(|error| error == DELETED) { return Err(DELETED.into()); }
        result.is_ok()
    } else { false };
    check_owner()?;
    Ok(json!({"src":format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes)), "persisted":persisted}))
}
// Called only after successful cloud deletion. The tombstone prevents an in-flight
// image download from recreating attachments for an already deleted conversation.
pub fn delete_session(root: &Path, hub: &str, session: &str) -> Result<(), String> {
    let _lock = WRITES.lock().map_err(|_| STORAGE)?;
    let dir = directory(root, hub, session, true)?.ok_or(STORAGE)?;
    let marker = dir.join("deleted");
    if !marker.exists() { fs::OpenOptions::new().create_new(true).write(true).open(&marker).and_then(|file| file.sync_all()).map_err(|_| STORAGE)?; }
    for item in fs::read_dir(&dir).map_err(|_| STORAGE)? {
        let entry = item.map_err(|_| STORAGE)?;
        let name = entry.file_name().to_string_lossy().to_string();
        let attachment = name.strip_suffix(".image").is_some_and(|key| key.len() == 64 && key.bytes().all(|byte| byte.is_ascii_hexdigit()));
        let temporary = name.strip_suffix(".tmp").is_some_and(|key| uuid::Uuid::parse_str(key).is_ok());
        if attachment || temporary {
            reject_link(&entry.path())?;
            if entry.file_type().map_err(|_| STORAGE)?.is_file() { fs::remove_file(entry.path()).map_err(|_| STORAGE)?; }
        }
    }
    Ok(())
}

#[cfg(test)] mod tests {
    use super::*;
    fn get(root: &Path, hub: &str, session: Option<&str>, proxy_path: &str, check_owner: impl Fn() -> Result<(), String>, fetch: impl FnOnce() -> Result<(Vec<u8>, String), String>) -> Result<Value, String> {
        super::get(root, hub, session, proxy_path, false, check_owner, fetch)
    }
    fn root() -> PathBuf { let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../validation-local").join(format!("omaax-qa-images-{}", uuid::Uuid::new_v4())); fs::create_dir_all(&path).unwrap(); fs::canonicalize(path).unwrap() }
    fn image() -> (Vec<u8>, String) { (base64::engine::general_purpose::STANDARD.decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=").unwrap(), "image/png".into()) }
    const HUB: &str = "https://cloud.example";
    const URL: &str = "/api/figures/proxy?url=paper&expires=old&sig=test";
    #[test] fn persisted_image_survives_reopen_and_expired_remote_without_fetching() {
        let root = root(); let session = uuid::Uuid::new_v4().to_string();
        let first = get(&root, HUB, Some(&session), URL, || Ok(()), || Ok(image())).unwrap();
        assert_eq!(first["persisted"], true);
        // No process-local state is needed; a second invocation reads only disk.
        let again = get(&root, HUB, Some(&session), URL, || Ok(()), || panic!("expired remote must not be consulted")).unwrap();
        assert_eq!(first, again); fs::remove_dir_all(root).unwrap();
    }
    #[test] fn account_hub_session_and_url_archives_are_separate() {
        let root = root(); let other = root.join("another-account"); fs::create_dir(&other).unwrap();
        let session = uuid::Uuid::new_v4().to_string(); let other_session = uuid::Uuid::new_v4().to_string();
        get(&root, HUB, Some(&session), URL, || Ok(()), || Ok(image())).unwrap();
        for (account, hub, id, url) in [(&other, HUB, session.as_str(), URL), (&root, "https://other.example", session.as_str(), URL), (&root, HUB, other_session.as_str(), URL), (&root, HUB, session.as_str(), "/api/figures/proxy?different=1")] {
            assert_eq!(get(account, hub, Some(id), url, || Ok(()), || Err("remote unavailable".into())).unwrap_err(), "remote unavailable");
        }
        assert!(session_id("../elsewhere").is_err()); fs::remove_dir_all(root).unwrap();
    }
    #[test] fn corrupt_files_and_failed_downloads_can_be_retried() {
        let root = root(); let session = uuid::Uuid::new_v4().to_string();
        assert!(get(&root, HUB, Some(&session), URL, || Ok(()), || Err("expired".into())).is_err());
        let first = get(&root, HUB, Some(&session), URL, || Ok(()), || Ok(image())).unwrap();
        let dir = directory(&root, HUB, &session, false).unwrap().unwrap();
        fs::write(attachment_path(&dir, URL), b"damaged").unwrap();
        let repaired = get(&root, HUB, Some(&session), URL, || Ok(()), || Ok(image())).unwrap();
        assert_eq!(first, repaired);
        assert!(get(&root, HUB, Some(&session), "bad", || Ok(()), || Ok((b"<html>error</html>".to_vec(),"image/png".into()))).is_err());
        assert!(!attachment_path(&dir, "bad").exists()); fs::remove_dir_all(root).unwrap();
    }
    #[test] fn explicit_refresh_bypasses_bad_display_cache_without_erasing_it_on_failure() {
        let root = root(); let session = uuid::Uuid::new_v4().to_string();
        let original = get(&root, HUB, Some(&session), URL, || Ok(()), || Ok((b"\x89PNG\r\n\x1a\nincomplete".to_vec(), "image/png".into()))).unwrap();
        assert_eq!(original["persisted"], true);
        assert_eq!(super::get(&root, HUB, Some(&session), URL, true, || Ok(()), || Err("proxy expired".into())).unwrap_err(), "proxy expired");
        assert_eq!(get(&root, HUB, Some(&session), URL, || Ok(()), || panic!("failed refresh must preserve the existing record")).unwrap(), original);
        let repaired = super::get(&root, HUB, Some(&session), URL, true, || Ok(()), || Ok(image())).unwrap();
        assert_eq!(repaired["persisted"], true); assert_ne!(repaired["src"], original["src"]);
        assert_eq!(get(&root, HUB, Some(&session), URL, || Ok(()), || panic!("repaired image must be persistent")).unwrap(), repaired);
        fs::remove_dir_all(root).unwrap();
    }
    #[test] fn deletion_is_session_scoped_and_blocks_late_downloads() {
        let root = root(); let session = uuid::Uuid::new_v4().to_string(); let other = uuid::Uuid::new_v4().to_string();
        for id in [&session, &other] { get(&root, HUB, Some(id), URL, || Ok(()), || Ok(image())).unwrap(); }
        let late = get(&root, HUB, Some(&session), "uncached", || Ok(()), || { delete_session(&root, HUB, &session).unwrap(); Ok(image()) });
        assert_eq!(late.unwrap_err(), DELETED);
        assert_eq!(get(&root, HUB, Some(&session), URL, || Ok(()), || panic!("deleted conversation must not fetch")).unwrap_err(), DELETED);
        assert_eq!(get(&root, HUB, Some(&other), URL, || Ok(()), || panic!("other conversation preserved")).unwrap()["persisted"], true);
        delete_session(&root, HUB, &session).unwrap(); fs::remove_dir_all(root).unwrap();
    }
    #[test] fn changed_account_or_unwritable_storage_never_reports_persisted() {
        let root = root(); let session = uuid::Uuid::new_v4().to_string();
        assert!(get(&root, HUB, Some(&session), URL, || Err("changed account".into()), || panic!("must not fetch")).is_err());
        fs::write(root.join("knowledge-images"), b"not a directory").unwrap();
        let value = get(&root, HUB, Some(&session), URL, || Ok(()), || Ok(image())).unwrap();
        assert_eq!(value["persisted"], false); assert!(value["src"].as_str().unwrap().starts_with("data:image/png"));
        fs::remove_dir_all(root).unwrap();
    }
    #[test] fn account_change_during_download_never_writes_or_returns_the_image() {
        let root = root(); let session = uuid::Uuid::new_v4().to_string();
        let changed = std::cell::Cell::new(false);
        let result = get(&root, HUB, Some(&session), URL, || if changed.get() { Err("changed account".into()) } else { Ok(()) }, || { changed.set(true); Ok(image()) });
        assert_eq!(result.unwrap_err(), "changed account");
        assert!(!root.join("knowledge-images").exists()); fs::remove_dir_all(root).unwrap();
    }
    #[test] fn image_types_and_sizes_are_verified_before_archive() {
        for (bytes, mime) in [(b"\x89PNG\r\n\x1a\nrest".as_slice(), "image/png"), (b"\xff\xd8\xffrest", "image/jpeg"), (b"GIF89arest", "image/gif"), (b"RIFF1234WEBPpayload", "image/webp")] { validate(bytes, mime).unwrap(); }
        for (bytes, mime) in [(b"<svg/>".as_slice(), "image/svg+xml"), (b"<html/>", "image/png"), (b"\x89PNG\r\n\x1a\n", "image/jpeg"), (b"RIFF1234BAD!", "image/webp")] { assert!(validate(bytes, mime).is_err()); }
        assert!(validate(&vec![0; MAX_IMAGE + 1], "image/png").is_err());
    }
    #[test] fn image_archive_restart_reader() {
        let Some(root) = std::env::var_os("OMAAX_TEST_QA_IMAGE_ROOT") else { return; };
        let session = std::env::var("OMAAX_TEST_QA_IMAGE_SESSION").unwrap();
        let hub = std::env::var("OMAAX_TEST_QA_IMAGE_HUB").unwrap();
        let value = get(Path::new(&root), &hub, Some(&session), URL, || Ok(()), || panic!("new process must read archive, not expired remote")).unwrap();
        assert_eq!(value["persisted"], true);
    }
    #[test] fn real_http_image_is_read_in_a_new_process_after_proxy_expires() {
        use std::io::{Read, Write};
        let root = root(); let session = uuid::Uuid::new_v4().to_string();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hub = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap(); socket.set_read_timeout(Some(std::time::Duration::from_secs(5))).unwrap();
            let mut request = Vec::new(); let mut buffer = [0; 1024];
            while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") { let count = socket.read(&mut buffer).unwrap(); assert!(count > 0); request.extend_from_slice(&buffer[..count]); }
            let request = String::from_utf8(request).unwrap();
            assert!(request.starts_with(&format!("GET {URL} HTTP/1.1"))); assert!(request.to_ascii_lowercase().contains("authorization: bearer fixture-token"));
            let (bytes, _) = image();
            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", bytes.len()).unwrap(); socket.write_all(&bytes).unwrap();
        });
        let client = reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(std::time::Duration::from_secs(3)).build().unwrap();
        let image = get(&root, &hub, Some(&session), URL, || Ok(()), || super::super::fetch_image(&client, &hub, "fixture-token", URL, &|| Ok(()))).unwrap();
        assert_eq!(image["persisted"], true); server.join().unwrap();
        let output = std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact", "cloud_qa::images::tests::image_archive_restart_reader", "--nocapture"])
            .env("OMAAX_TEST_QA_IMAGE_ROOT", &root).env("OMAAX_TEST_QA_IMAGE_SESSION", &session).env("OMAAX_TEST_QA_IMAGE_HUB", &hub).output().unwrap();
        assert!(output.status.success(), "{}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        assert!(String::from_utf8_lossy(&output.stdout).contains("1 passed")); fs::remove_dir_all(root).unwrap();
    }
}
