use std::{path::Path,time::Duration};
use serde_json::{json,Value};
use crate::hub_auth;
use std::{collections::HashMap,sync::{Arc,Mutex,LazyLock,atomic::{AtomicBool,Ordering}},time::Instant};
static REQUESTS:LazyLock<Mutex<HashMap<String,(Arc<AtomicBool>,Instant)>>>=LazyLock::new(||Mutex::new(HashMap::new()));
pub fn image_bytes(data:&str)->Result<(Vec<u8>,&'static str),String>{
    use base64::Engine;
    if data.len()>17_000_000{return Err("图片超过大小限制".into());}
    let (header,payload)=data.split_once(',').ok_or("图片格式无效")?;
    let ext=match header{"data:image/png;base64"=>"png","data:image/jpeg;base64"=>"jpg","data:image/gif;base64"=>"gif","data:image/webp;base64"=>"webp",_=>return Err("图片格式不支持".into())};
    let bytes=base64::engine::general_purpose::STANDARD.decode(payload).map_err(|_|"图片格式无效")?;
    if bytes.is_empty()||bytes.len()>12*1024*1024{return Err("图片为空或超过大小限制".into());}
    Ok((bytes,ext))
}
pub fn control(action:&str,body:&Value)->Result<Value,String>{
    let key=uuid::Uuid::parse_str(body["requestId"].as_str().unwrap_or("")).map_err(|_|"请求标识无效")?.to_string();
    let mut requests=REQUESTS.lock().map_err(|_|"请求状态不可用")?;
    if action=="cancel" {
        if let Some((flag,_))=requests.remove(&key){flag.store(true,Ordering::SeqCst);}
    } else {
        requests.retain(|_,(_,at)|at.elapsed()<Duration::from_secs(600));
        if requests.len()>=32 || requests.contains_key(&key){return Err("知识库请求过多，请稍后重试".into());}
        requests.insert(key,(Arc::new(AtomicBool::new(false)),Instant::now()));
    }
    Ok(json!({"ok":true}))
}
pub async fn generate(root:std::path::PathBuf,action:String,body:Value)->Result<Value,String>{
    let key=body["requestId"].as_str().unwrap_or("").to_string();
    let flag=REQUESTS.lock().map_err(|_|"请求状态不可用")?.get(&key).map(|(v,_)|v.clone()).ok_or("已中止")?;
    let work=async {
        let (method,path,payload)=route(&action,&body)?;
        let auth_root=root.clone();
        let (hub,token)=tauri::async_runtime::spawn_blocking(move||hub_auth::cloud_session(&auth_root)).await.map_err(|_|"登录状态不可用")??;
        let client=reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(12)).timeout(Duration::from_secs(if action=="chat" {310}else{60})).build().map_err(|_|"无法连接知识库")?;
        let mut req=client.request(reqwest::Method::from_bytes(method.as_bytes()).unwrap(),format!("{}{path}",hub_auth::normalize(&hub)?)).bearer_auth(&token);
        if let Some(value)=payload{req=req.json(&value);}
        let res=req.send().await.map_err(|_|"知识库连接中断，请重试")?;
        let status=res.status().as_u16();let value=res.json::<Value>().await.map_err(|_|"知识库返回异常，请稍后重试")?;
        let current=tauri::async_runtime::spawn_blocking(move||hub_auth::cloud_session(&root)).await.map_err(|_|"登录状态不可用")??;
        if current!=(hub,token){return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
        response(status,value)
    };
    let result=cancelable(work,flag).await;
    if let Ok(mut requests)=REQUESTS.lock(){requests.remove(&key);}
    result
}

async fn cancelable(work:impl std::future::Future<Output=Result<Value,String>>,flag:Arc<AtomicBool>)->Result<Value,String>{
    let mut work=std::pin::pin!(work);
    let mut tick=tokio::time::interval(Duration::from_millis(50));
    std::future::poll_fn(|cx| {
        use std::{future::Future,task::Poll};
        if flag.load(Ordering::SeqCst){return Poll::Ready(Err("已中止".into()));}
        while tick.poll_tick(cx).is_ready(){}
        work.as_mut().poll(cx)
    }).await

}

fn id(body:&Value)->Result<String,String>{
    uuid::Uuid::parse_str(body["id"].as_str().unwrap_or("")).map(|v|v.to_string()).map_err(|_|"会话标识无效".into())
}
pub fn route(action:&str,body:&Value)->Result<(&'static str,String,Option<Value>),String>{
    Ok(match action {
        "list" => ("GET","/api/sessions".into(),None),
        "create" => {let title=body["title"].as_str().unwrap_or("知识库问答").chars().take(80).collect::<String>();("POST","/api/sessions".into(),Some(json!({"title":title})))},
        "image" => ("GET",image_path(body["url"].as_str().unwrap_or(""),&hub_auth::configured_hub())?,None),
        "detail" => ("GET",format!("/api/sessions/{}",id(body)?),None),
        "delete" => ("DELETE",format!("/api/sessions/{}",id(body)?),None),
        "message" => {
            let role=body["role"].as_str().unwrap_or("");let text=body["text"].as_str().unwrap_or("");
            if !matches!(role,"user"|"assistant") || text.len()>1_000_000 {return Err("消息内容无效".into());}
            let mut blocks=body.get("blocks").cloned().unwrap_or(json!([]));
            if !blocks.is_array() || blocks.to_string().len()>2_000_000 {return Err("来源内容过大".into());}
            // The cloud persists assistant content as blocks and may omit its text field.
            if role=="assistant" && !text.is_empty(){
                let escaped=text.replace('&',"&amp;").replace('<',"&lt;").replace('>',"&gt;").replace('"',"&quot;");
                blocks.as_array_mut().unwrap().insert(0,json!({"type":"text","html":format!("<div data-omaax-markdown=\"1\" style=\"white-space:pre-wrap\">{escaped}</div>")}));
            }
            ("POST",format!("/api/sessions/{}/messages",id(body)?),Some(json!({"role":role,"text":text,"blocks":blocks})))
        },
        "chat"|"search" => {
            let q=body["question"].as_str().unwrap_or("").trim();
            if q.is_empty() || q.chars().count()>8000 {return Err("请输入 1–8000 字的问题".into());}
            ("POST",format!("/api/qa/{action}"),Some(json!({"question":q,"use_llm":action=="chat"})))
        },
        _=>return Err("不支持此知识库操作".into())
    })
}
fn image_path(value:&str,hub:&str)->Result<String,String>{
    let base=reqwest::Url::parse(&hub_auth::normalize(hub)?).map_err(|_|"图片地址无效")?;
    let url=base.join(value).map_err(|_|"图片地址无效")?;
    if url.origin()!=base.origin() || url.path()!="/api/figures/proxy" || !url.username().is_empty() || url.password().is_some() || url.fragment().is_some() || url.query().is_none() {
        return Err("此图片来源暂不支持".into());
    }
    Ok(format!("{}?{}",url.path(),url.query().unwrap()))
}
fn response(status:u16,value:Value)->Result<Value,String>{
    match status {
        200..=299 if value.get("ok")!=Some(&json!(false)) && value.get("success")!=Some(&json!(false)) => Ok(value),
        401=>Err("UNAUTHORIZED: 登录已过期，请重新登录".into()),
        403=>Err("FORBIDDEN: 当前账号没有知识库问答权限，请联系管理员".into()),
        404=>Err("会话不存在或已被删除，请刷新列表".into()),
        502..=504=>Err("知识库正在更新或暂不可用，请稍后重试".into()),
        _=>Err("知识库请求未完成，请稍后重试".into())
    }
}
fn image_retryable(status:u16)->bool {matches!(status,404|408|429|500..=599)}
fn image_status(status:u16)->Result<(),String>{
    match status {
        200..=299=>Ok(()),
        401=>Err("UNAUTHORIZED: 登录已过期，请重新登录后查看图片".into()),
        403=>Err("IMAGE_DENIED: 图片链接已失效或访问被拒绝，请重新获取回答中的图片".into()),
        404=>Err("IMAGE_MISSING: 文献图片暂不可用，请稍后重新加载".into()),
        _=>Err("IMAGE_UNAVAILABLE: 图片服务暂不可用，请稍后重新加载".into()),
    }
}
pub fn call(root:&Path,action:&str,body:Value)->Result<Value,String>{
    let (method,path,payload)=route(action,&body)?;
    let (hub,token)=hub_auth::cloud_session(root)?;
    let client=reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(12)).timeout(Duration::from_secs(if action=="chat" {310}else{60})).build().map_err(|_|"无法连接知识库")?;
    let mut req=client.request(reqwest::Method::from_bytes(method.as_bytes()).unwrap(),format!("{}{path}",hub_auth::normalize(&hub)?)).bearer_auth(&token);
    if let Some(value)=payload {req=req.json(&value);}
    let res=if action=="image" {
        let mut attempt=0;
        loop {
            if hub_auth::cloud_session(root)?!=(hub.clone(),token.clone()){return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
            let result=req.try_clone().ok_or("图片请求无效")?.send();
            let retry=result.as_ref().map(|r|image_retryable(r.status().as_u16())).unwrap_or(true);
            if !retry || attempt==2 {break result;}
            drop(result);
            attempt+=1;
            std::thread::sleep(Duration::from_millis(500*attempt));
        }
    }else{req.send()}.map_err(|_|"知识库响应超时或连接中断，请稍后重试")?;
    let status=res.status().as_u16();
    if action=="image" {
        use std::io::Read;
        use base64::Engine;
        image_status(status)?;
        let mime=res.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v|v.to_str().ok()).unwrap_or("").split(';').next().unwrap_or("").trim().to_string();
        if !matches!(mime.as_str(),"image/png"|"image/jpeg"|"image/gif"|"image/webp"){return Err("返回内容不是支持的图片".into());}
        let limit=12*1024*1024;let mut bytes=Vec::new();
        res.take(limit+1).read_to_end(&mut bytes).map_err(|_|"图片加载中断，请重试")?;
        if bytes.is_empty() || bytes.len() as u64>limit {return Err("图片为空或超过大小限制".into());}
        if hub_auth::cloud_session(root)?!=(hub,token){return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
        return Ok(json!({"src":format!("data:{mime};base64,{}",base64::engine::general_purpose::STANDARD.encode(bytes))}));
    }
    let value=res.json::<Value>().map_err(|_|"知识库返回异常，请稍后重试")?;
    // Discard results arriving after logout, account switching or revocation.
    if hub_auth::cloud_session(root)? != (hub,token) {return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
    response(status,value)
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn saved_images_are_bounded_and_format_restricted(){
        assert_eq!(image_bytes("data:image/png;base64,eA==").unwrap(),(vec![b'x'],"png"));
        for bad in ["https://example.org/a.png","data:text/html;base64,eA==","data:image/svg+xml;base64,eA==","data:image/png;base64,","data:image/png;base64,!!!!"]{assert!(image_bytes(bad).is_err());}
    }
    #[test] fn cancel_pending_work_and_release_request(){
        let key=uuid::Uuid::new_v4().to_string();let body=json!({"requestId":key});
        control("begin",&body).unwrap();
        let flag=REQUESTS.lock().unwrap().get(&key).unwrap().0.clone();
        let worker=std::thread::spawn(move||{std::thread::sleep(Duration::from_millis(80));control("cancel",&body).unwrap();});
        let runtime=tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap();
        let started=Instant::now();
        let result=runtime.block_on(async{tokio::time::timeout(Duration::from_secs(2),cancelable(std::future::pending(),flag)).await});
        assert_eq!(result.unwrap().unwrap_err(),"已中止");assert!(started.elapsed()<Duration::from_secs(2));
        worker.join().unwrap();assert!(!REQUESTS.lock().unwrap().contains_key(&key));
    }
    #[test] fn image_failures_are_bounded_and_not_mislabeled(){
        for code in [404,408,429,500,502,503,504]{assert!(image_retryable(code));}
        for code in [200,301,401,403,422]{assert!(!image_retryable(code));}
        assert!(image_status(404).unwrap_err().contains("IMAGE_MISSING"));
        assert!(image_status(403).unwrap_err().contains("IMAGE_DENIED"));
        assert!(image_status(401).unwrap_err().contains("UNAUTHORIZED"));
    }
    #[test] fn endpoints_and_payloads_are_constrained(){
        assert!(route("https://other.test",&json!({})).is_err());
        for action in ["detail","delete","message"] {assert!(route(action,&json!({"id":"../admin/users"})).is_err());}
        assert!(route("chat",&json!({"question":"  "})).is_err());
        assert!(route("chat",&json!({"question":"a".repeat(8001)})).is_err());
        let (_,p,b)=route("search",&json!({"question":"SPL","token":"secret","use_llm":true})).unwrap();
        assert_eq!(p,"/api/qa/search");assert_eq!(b.unwrap(),json!({"question":"SPL","use_llm":false}));
    }
    #[test] fn failures_never_become_answers(){
        for s in [401,403,404,422,500,502,503,504] {assert!(response(s,json!({"error":{"message":"secret stack"}})).is_err());}
        assert!(response(200,json!({"success":false})).is_err());
        assert_eq!(response(200,json!({"answer":"ok","sources":[]})).unwrap()["answer"],"ok");
    }
    #[test] fn image_credentials_only_go_to_the_configured_proxy(){
        let hub="https://cloud.example";
        assert_eq!(image_path("/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fa.jpg&sig=abc",hub).unwrap(),"/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fa.jpg&sig=abc");
        for url in ["https://evil.example/api/figures/proxy?x=1","//evil.example/api/figures/proxy?x=1","/api/admin/users?x=1","javascript:alert(1)","data:image/svg+xml,evil","https://u:p@cloud.example/api/figures/proxy?x=1","/api/figures/proxy?x=1#bad"] {assert!(image_path(url,hub).is_err(),"{url}");}
    }
}
