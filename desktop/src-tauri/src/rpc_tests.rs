use super::{auth_cookie, cookie_cache, dsh_raw, dsh_rpc_args, origin_of};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};

#[test]
fn preferences_use_direct_args_and_existing_session_calls_keep_their_wrapper() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/?token=fixture", listener.local_addr().unwrap());
    let server = std::thread::spawn(move || {
        for index in 0..3 {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(std::time::Duration::from_secs(5))).unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut request = String::new();
            reader.read_line(&mut request).unwrap();
            let mut length = 0;
            let mut cookie = String::new();
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if line == "\r\n" { break; }
                if let Some((name, value)) = line.split_once(':') {
                    if name.eq_ignore_ascii_case("content-length") { length = value.trim().parse().unwrap(); }
                    if name.eq_ignore_ascii_case("cookie") { cookie = value.trim().to_string(); }
                }
            }
            let response = if index == 0 {
                assert!(request.starts_with("GET /?token=fixture "));
                "HTTP/1.1 303 See Other\r\nSet-Cookie: dsh-auth-fixture=local; HttpOnly\r\nLocation: /\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".to_string()
            } else {
                assert_eq!(cookie, "dsh-auth-fixture=local");
                let mut body = vec![0; length];
                reader.read_exact(&mut body).unwrap();
                let body: Value = serde_json::from_slice(&body).unwrap();
                assert_eq!(body["type"], "client-request");
                let (endpoint, args) = if index == 1 {
                    ("settings/update", json!({"ns":"ui-theme","patch":{"preference":"dark"}}))
                } else {
                    ("session/list", json!({"_request":{}}))
                };
                assert!(request.starts_with(&format!("POST /api/{endpoint} ")));
                assert_eq!(body["method"], endpoint);
                assert_eq!(body["payload"]["args"], args);
                let answer = json!({"result":{"ok":true,"value":{"accepted":true}}}).to_string();
                format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", answer.len(), answer)
            };
            stream.write_all(response.as_bytes()).unwrap();
        }
    });
    assert_eq!(auth_cookie(&url, true).as_deref(), Some("dsh-auth-fixture=local"));
    let result = dsh_rpc_args(&url, "settings/update", json!({"ns":"ui-theme","patch":{"preference":"dark"}})).unwrap();
    assert_eq!(result["accepted"], true);
    assert_eq!(dsh_raw(&url, "session/list", "_request", json!({})).unwrap()["accepted"], true);
    server.join().unwrap();
    cookie_cache().lock().unwrap().remove(&origin_of(&url));
}
