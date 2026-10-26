use std::{path::Path,time::Duration};
use serde_json::{json,Value};
use crate::hub_auth;
#[path="cloud_qa_images.rs"] mod images;
use std::{collections::HashMap,sync::{Arc,Mutex,LazyLock,atomic::{AtomicBool,Ordering}},time::Instant};
struct QueryRequest {
    cancelled:Arc<AtomicBool>,
    started:AtomicBool,
    session:Mutex<Option<(String,String)>>,
    created:Instant,
}
static REQUESTS:LazyLock<Mutex<HashMap<String,Arc<QueryRequest>>>>=LazyLock::new(||Mutex::new(HashMap::new()));
static LOGGING_OUT:AtomicBool=AtomicBool::new(false);
pub struct QueryShutdown;
impl Drop for QueryShutdown {
    fn drop(&mut self){LOGGING_OUT.store(false,Ordering::SeqCst);}
}
// Keep this guard until credentials are removed/restart begins. If logout fails,
// dropping it allows the still-signed-in account to use knowledge queries again.
pub async fn prepare_logout()->QueryShutdown {
    prepare_logout_with_timeout(Duration::from_secs(4)).await
}
async fn prepare_logout_with_timeout(timeout:Duration)->QueryShutdown {
    let requests={
        let mut requests=REQUESTS.lock().unwrap_or_else(|error|error.into_inner());
        LOGGING_OUT.store(true,Ordering::SeqCst);
        requests.drain().collect::<Vec<_>>()
    };
    let mut cancellations=tokio::task::JoinSet::new();
    for (key,request) in requests {
        request.cancelled.store(true,Ordering::SeqCst);
        let session=request.session.lock().ok().and_then(|session|session.clone());
        if let Some((hub,token))=session {
            cancellations.spawn(async move {cancel_remote(&hub,&token,&key).await});
        }
    }
    // A slow/unreachable cloud must not trap the user in logout. All local work
    // is already cancelled; unfinished remote cancellation tasks are aborted.
    let _=tokio::time::timeout(timeout,async {while cancellations.join_next().await.is_some(){}}).await;
    QueryShutdown
}
fn request_id(body:&Value)->Result<String,String>{
    uuid::Uuid::parse_str(body["requestId"].as_str().unwrap_or("")).map(|v|v.to_string()).map_err(|_|"请求标识无效".into())
}
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
    let key=request_id(body)?;
    let mut requests=REQUESTS.lock().map_err(|_|"请求状态不可用")?;
    if LOGGING_OUT.load(Ordering::SeqCst){return Err("QUERY_CANCELLED".into());}
    if action!="begin" {return Err("不支持此知识库操作".into());}
    requests.retain(|_,request|request.created.elapsed()<Duration::from_secs(600));
    if requests.len()>=32 || requests.contains_key(&key){return Err("知识库请求过多，请稍后重试".into());}
    requests.insert(key,Arc::new(QueryRequest{cancelled:Arc::new(AtomicBool::new(false)),started:AtomicBool::new(false),session:Mutex::new(None),created:Instant::now()}));
    Ok(json!({"ok":true}))
}
pub async fn cancel(body:&Value)->Result<Value,String>{
    let key=request_id(body)?;
    let request=REQUESTS.lock().map_err(|_|"请求状态不可用")?.get(&key).cloned();
    let Some(request)=request else{return Ok(json!({"ok":true}));};
    // Capture the original account, including when logout races with cancellation.
    let session={
        let session=request.session.lock().map_err(|_|"请求状态不可用")?;
        request.cancelled.store(true,Ordering::SeqCst);
        session.clone()
    };
    // Retain the request until the remote attempt finishes so logout can also
    // see a stop click whose cancellation is still in flight.
    let result=if let Some((hub,token))=session {cancel_remote(&hub,&token,&key).await}else{Ok(json!({"ok":true}))};
    if let Ok(mut requests)=REQUESTS.lock(){
        if requests.get(&key).is_some_and(|current|Arc::ptr_eq(current,&request)){requests.remove(&key);}
    }
    result
}
async fn cancel_remote(hub:&str,token:&str,key:&str)->Result<Value,String>{
    let client=reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(4)).timeout(Duration::from_secs(8)).build().map_err(|_|"取消请求未能送达云端")?;
    let url=format!("{}/api/qa/cancel/{key}",hub_auth::normalize(hub)?);
    for attempt in 0..4 {
        let res=client.post(&url).bearer_auth(token).send().await.map_err(|_|"取消请求未能送达云端")?;
        let status=res.status().as_u16();
        if status==404 {
            // A very early click can arrive before the cloud registers the query.
            if attempt<3 {tokio::time::sleep(Duration::from_millis(250*(1<<attempt))).await;continue;}
            // The query may be finished, or this endpoint may not track cancellations.
            return Ok(json!({"ok":true,"cloud_cancelled":false}));
        }
        let value=res.json::<Value>().await.map_err(|_|"云端取消状态暂时无法确认")?;
        return response(status,value);
    }
    unreachable!()
}
pub async fn generate(root:std::path::PathBuf,action:String,body:Value,on_event:Option<tauri::ipc::Channel<Value>>)->Result<Value,String>{
    let key=request_id(&body)?;
    let request=REQUESTS.lock().map_err(|_|"请求状态不可用")?.get(&key).cloned().ok_or("QUERY_CANCELLED")?;
    if request.started.swap(true,Ordering::SeqCst){return Err("该查询已提交".into());}
    let streaming=action=="chat" && on_event.is_some();
    let work=async {
        let (method,mut path,payload)=route(&action,&body)?;
        if streaming {path="/api/qa/chat/stream".into();}
        let auth_root=root.clone();
        let (hub,token)=tauri::async_runtime::spawn_blocking(move||hub_auth::cloud_session(&auth_root)).await.map_err(|_|"登录状态不可用")??;
        let client=reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).connect_timeout(Duration::from_secs(12)).timeout(Duration::from_secs(if action=="chat" {310}else{60})).build().map_err(|_|"无法连接知识库")?;
        let mut req=client.request(reqwest::Method::from_bytes(method.as_bytes()).unwrap(),format!("{}{path}",hub_auth::normalize(&hub)?)).bearer_auth(&token);
        if let Some(value)=payload{req=req.json(&value);}
        if streaming {req=req.header(reqwest::header::ACCEPT,"text/event-stream");}
        {
            let mut session=request.session.lock().map_err(|_|"请求状态不可用")?;
            if request.cancelled.load(Ordering::SeqCst){return Err("QUERY_CANCELLED".into());}
            *session=Some((hub.clone(),token.clone()));
        }
        let res=req.send().await.map_err(|_|"知识库连接中断，请重试")?;
        if let Some(channel)=on_event.as_ref().filter(|_|streaming) {
            return read_stream(res,|event,payload|channel.send(json!({"event":event,"payload":payload})).map_err(|_|"QA_STREAM_CLOSED: 问答窗口已关闭".to_string()),||{
                let (root,expected)=(root.clone(),(hub.clone(),token.clone()));
                async move {
                    let current=tauri::async_runtime::spawn_blocking(move||hub_auth::cloud_session(&root)).await.map_err(|_|"登录状态不可用")??;
                    if current!=expected {return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
                    Ok(())
                }
            }).await;
        }
        let status=res.status().as_u16();let value=res.json::<Value>().await.map_err(|_|"知识库返回异常，请稍后重试")?;
        let current=tauri::async_runtime::spawn_blocking(move||hub_auth::cloud_session(&root)).await.map_err(|_|"登录状态不可用")??;
        if current!=(hub,token){return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
        response(status,value)
    };
    let result=cancelable(work,request.cancelled.clone()).await;
    if streaming && result.is_err() && !request.cancelled.load(Ordering::SeqCst) {
        // A closed renderer, invalidated account or broken stream must not leave cloud work running.
        let session=request.session.lock().ok().and_then(|session|session.clone());
        if let Some((hub,token))=session {let key=key.clone();tauri::async_runtime::spawn(async move{let _=cancel_remote(&hub,&token,&key).await;});}
    }
    if let Ok(mut requests)=REQUESTS.lock(){
        if !request.cancelled.load(Ordering::SeqCst) && requests.get(&key).is_some_and(|current|Arc::ptr_eq(current,&request)){requests.remove(&key);}
    }
    result
}

const MAX_SSE_FRAME:usize=4*1024*1024;
const MAX_SSE_BYTES:usize=32*1024*1024;
const INVALID_STREAM:&str="QA_STREAM_INVALID: 知识库返回异常，请稍后重试";
#[derive(Default)]
struct SseDecoder {line:Vec<u8>,event:String,data:Vec<String>,frame_bytes:usize,total_bytes:usize}
impl SseDecoder {
    fn push(&mut self,chunk:&[u8])->Result<Vec<(String,Value)>,String>{
        self.total_bytes=self.total_bytes.checked_add(chunk.len()).ok_or(INVALID_STREAM)?;
        if self.total_bytes>MAX_SSE_BYTES {return Err(INVALID_STREAM.into());}
        let mut events=Vec::new();
        for &byte in chunk {
            self.frame_bytes+=1;
            if self.frame_bytes>MAX_SSE_FRAME {return Err(INVALID_STREAM.into());}
            if byte!=b'\n' {self.line.push(byte);continue;}
            if self.line.last()==Some(&b'\r') {self.line.pop();}
            let line=String::from_utf8(std::mem::take(&mut self.line)).map_err(|_|INVALID_STREAM)?;
            if line.is_empty() {
                if !self.data.is_empty() && matches!(self.event.as_str(),"progress"|"answer_delta"|"evidence"|"complete"|"error"|"timeout") {
                    let payload=serde_json::from_str::<Value>(&self.data.join("\n")).map_err(|_|INVALID_STREAM)?;
                    if !payload.is_object() {return Err(INVALID_STREAM.into());}
                    events.push((self.event.clone(),payload));
                    if matches!(self.event.as_str(),"complete"|"error"|"timeout"){return Ok(events);}
                }
                self.event.clear();self.data.clear();self.frame_bytes=0;
            }else if !line.starts_with(':') {
                let (field,value)=line.split_once(':').unwrap_or((&line,""));
                let value=value.strip_prefix(' ').unwrap_or(value);
                match field {"event"=>self.event=value.into(),"data"=>self.data.push(value.into()),_=>{}}
            }
        }
        Ok(events)
    }
}

fn stream_error(value:&Value)->String {
    let code=value["code"].as_str().unwrap_or("").to_ascii_uppercase();
    match code.as_str() {
        "QUERY_CANCELLED"=>"QUERY_CANCELLED",
        "UNAUTHORIZED"=>"UNAUTHORIZED: 登录已过期，请重新登录",
        "FORBIDDEN"=>"FORBIDDEN: 当前账号没有知识库问答权限，请联系管理员",
        "INVALID_CITATION"=>"QA_INVALID_CITATION: 引用核对未完成，请重试",
        "TIMEOUT"=>"QA_TIMEOUT: 检索超时，请重试",
        "UPSTREAM_DOWN"|"UPSTREAM_ERROR"=>"QA_UNAVAILABLE: 知识库服务暂不可用，请稍后重试",
        _ if value["retryable"]==false=>"QA_REQUEST_FAILED: 知识库请求未完成，请联系管理员",
        _=>"QA_REQUEST_FAILED: 知识库请求未完成，请重试",
    }.into()
}

async fn read_stream<Check,Checked>(mut res:reqwest::Response,mut emit:impl FnMut(&str,Value)->Result<(),String>,mut check_account:Check)->Result<Value,String>
where Check:FnMut()->Checked,Checked:std::future::Future<Output=Result<(),String>> {
    if !res.status().is_success() {
        let status=res.status().as_u16();
        if matches!(status,404|405){return Err("QA_UNAVAILABLE: 知识库服务暂不可用，请稍后重试".into());}
        let value=res.json::<Value>().await.unwrap_or(Value::Null);
        return response(status,value);
    }
    if !res.headers().get(reqwest::header::CONTENT_TYPE).and_then(|value|value.to_str().ok()).unwrap_or("").split(';').next().unwrap_or("").trim().eq_ignore_ascii_case("text/event-stream") {return Err(INVALID_STREAM.into());}
    let mut decoder=SseDecoder::default();
    loop {
        let chunk=res.chunk().await.map_err(|error|if error.is_timeout(){"QA_TIMEOUT: 检索超时，请重试"}else{"QA_CONNECTION_CLOSED: 知识库连接中断，请重试"})?;
        let Some(chunk)=chunk else{return Err("QA_CONNECTION_CLOSED: 知识库连接意外关闭，请重试".into());};
        let events=decoder.push(&chunk)?;
        if events.is_empty(){continue;}
        // Revalidate before forwarding each network batch, not only after the final answer.
        check_account().await?;
        for (event,payload) in events {
            match event.as_str() {
                "complete"=>{
                    if !payload["answer"].is_string(){return Err(INVALID_STREAM.into());}
                    return response(200,payload);
                },
                "error"=>return Err(stream_error(&payload)),
                "timeout"=>return Err("QA_TIMEOUT: 检索超时，请重试".into()),
                "answer_delta" if !payload["content"].is_string()=>return Err(INVALID_STREAM.into()),
                "progress"|"answer_delta"|"evidence"=>emit(&event,payload)?,
                _=>{},
            }
        }
    }
}

async fn cancelable(work:impl std::future::Future<Output=Result<Value,String>>,flag:Arc<AtomicBool>)->Result<Value,String>{
    let mut work=std::pin::pin!(work);
    let mut tick=tokio::time::interval(Duration::from_millis(50));
    std::future::poll_fn(|cx| {
        use std::task::Poll;
        if flag.load(Ordering::SeqCst){return Poll::Ready(Err("QUERY_CANCELLED".into()));}
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
            ("POST",format!("/api/qa/{action}"),Some(json!({"question":q,"use_llm":action=="chat","request_id":request_id(body)?})))
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
    if value["error"]["code"].as_str().is_some_and(|code|code.eq_ignore_ascii_case("QUERY_CANCELLED")) {return Err("QUERY_CANCELLED".into());}
    if value["error"]["code"].as_str().is_some_and(|code|matches!(code.to_ascii_uppercase().as_str(),"INVALID_CITATION"|"TIMEOUT"|"UPSTREAM_DOWN"|"UPSTREAM_ERROR")) {return Err(stream_error(&value["error"]));}
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
static IMAGE_DOWNLOADS:(Mutex<usize>,std::sync::Condvar)=(Mutex::new(0),std::sync::Condvar::new());
struct ImageDownload;
impl ImageDownload {
    fn acquire()->Result<Self,String>{
        let (count,wake)=&IMAGE_DOWNLOADS;
        let mut count=count.lock().map_err(|_|"图片加载暂不可用")?;
        while *count>=3 {count=wake.wait(count).map_err(|_|"图片加载暂不可用")?;}
        *count+=1;Ok(Self)
    }
}
impl Drop for ImageDownload {
    fn drop(&mut self){let (count,wake)=&IMAGE_DOWNLOADS;if let Ok(mut count)=count.lock(){*count=count.saturating_sub(1);wake.notify_one();}}
}
fn fetch_image(client:&reqwest::blocking::Client,hub:&str,token:&str,path:&str,check_owner:&impl Fn()->Result<(),String>)->Result<(Vec<u8>,String),String>{
    use std::io::Read;
    let _download=ImageDownload::acquire()?;
    let mut attempt=0;
    let res=loop {
        check_owner()?;
        let result=client.get(format!("{}{path}",hub_auth::normalize(hub)?)).bearer_auth(token).send();
        let retry=result.as_ref().map(|r|image_retryable(r.status().as_u16())).unwrap_or(true);
        if !retry || attempt==2 {break result;}
        drop(result);attempt+=1;
        std::thread::sleep(Duration::from_millis(500*attempt));
    }.map_err(|_|"知识库响应超时或连接中断，请稍后重试")?;
    image_status(res.status().as_u16())?;
    let mime=res.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v|v.to_str().ok()).unwrap_or("").split(';').next().unwrap_or("").trim().to_ascii_lowercase();
    if !matches!(mime.as_str(),"image/png"|"image/jpeg"|"image/gif"|"image/webp"){return Err("返回内容不是支持的图片".into());}
    if res.content_length().is_some_and(|length|length>images::MAX_IMAGE as u64){return Err("图片为空或超过大小限制".into());}
    let mut bytes=Vec::new();
    res.take((images::MAX_IMAGE+1) as u64).read_to_end(&mut bytes).map_err(|_|"图片加载中断，请重试")?;
    images::validate(&bytes,&mime)?;check_owner()?;
    Ok((bytes,mime))
}
pub fn call(root:&Path,action:&str,body:Value)->Result<Value,String>{
    let (method,path,payload)=route(action,&body)?;
    let (hub,token)=hub_auth::cloud_session(root)?;
    let client=reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(12)).timeout(Duration::from_secs(if action=="chat" {310}else{60})).build().map_err(|_|"无法连接知识库")?;
    if action=="image" {
        let session=body.get("sessionId").filter(|value|!value.is_null()).map(|value|images::session_id(value.as_str().unwrap_or(""))).transpose()?;
        let check_owner=|| {if hub_auth::cloud_session(root)?!=(hub.clone(),token.clone()){return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());} Ok(())};
        return images::get(root,&hub,session.as_deref(),&path,body["refresh"].as_bool().unwrap_or(false),&check_owner,||fetch_image(&client,&hub,&token,&path,&check_owner));
    }
    let mut req=client.request(reqwest::Method::from_bytes(method.as_bytes()).unwrap(),format!("{}{path}",hub_auth::normalize(&hub)?)).bearer_auth(&token);
    if let Some(value)=payload {req=req.json(&value);}
    let res=req.send().map_err(|_|"知识库响应超时或连接中断，请稍后重试")?;
    let status=res.status().as_u16();
    let value=res.json::<Value>().map_err(|_|"知识库返回异常，请稍后重试")?;
    // Discard results arriving after logout, account switching or revocation.
    if hub_auth::cloud_session(root)? != (hub.clone(),token) {return Err("UNAUTHORIZED: 登录已变化，请重新登录".into());}
    let mut result=response(status,value)?;
    if action=="delete" {
        let removed=images::delete_session(root,&hub,&id(&body)?).is_ok();
        if let Some(object)=result.as_object_mut(){object.insert("imagesRemoved".into(),json!(removed));}
    }
    Ok(result)
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn image_download_queue_is_bounded_and_rechecks_account_after_waiting(){
        let held=(0..3).map(|_|ImageDownload::acquire().unwrap()).collect::<Vec<_>>();
        let (send,receive)=std::sync::mpsc::channel();
        let changed=Arc::new(AtomicBool::new(false));let check=changed.clone();
        let worker=std::thread::spawn(move||{
            let client=reqwest::blocking::Client::builder().no_proxy().build().unwrap();
            let result=fetch_image(&client,"http://127.0.0.1:1","test-only","/api/figures/proxy?x=1",&||if check.load(Ordering::SeqCst){Err("changed account".into())}else{Ok(())});
            send.send(result).unwrap();
        });
        assert!(receive.recv_timeout(Duration::from_millis(100)).is_err());
        changed.store(true,Ordering::SeqCst);drop(held);
        assert_eq!(receive.recv_timeout(Duration::from_secs(3)).unwrap().unwrap_err(),"changed account");worker.join().unwrap();
    }
    #[test] fn saved_images_are_bounded_and_format_restricted(){
        assert_eq!(image_bytes("data:image/png;base64,eA==").unwrap(),(vec![b'x'],"png"));
        for bad in ["https://example.org/a.png","data:text/html;base64,eA==","data:image/svg+xml;base64,eA==","data:image/png;base64,","data:image/png;base64,!!!!"]{assert!(image_bytes(bad).is_err());}
    }
    #[test] fn cancel_pending_work_and_release_request(){
        let key=uuid::Uuid::new_v4().to_string();let body=json!({"requestId":key});
        control("begin",&body).unwrap();
        let flag=REQUESTS.lock().unwrap().get(&key).unwrap().cancelled.clone();
        let worker=std::thread::spawn(move||{std::thread::sleep(Duration::from_millis(80));tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(cancel(&body)).unwrap();});
        let runtime=tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap();
        let started=Instant::now();
        let result=runtime.block_on(async{tokio::time::timeout(Duration::from_secs(2),cancelable(std::future::pending(),flag)).await});
        assert_eq!(result.unwrap().unwrap_err(),"QUERY_CANCELLED");assert!(started.elapsed()<Duration::from_secs(2));
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
        let key=uuid::Uuid::new_v4().to_string();
        for action in ["chat","search"] {
            let (_,p,b)=route(action,&json!({"question":"SPL","requestId":key,"token":"secret","use_llm":true})).unwrap();
            assert_eq!(p,format!("/api/qa/{action}"));assert_eq!(b.unwrap(),json!({"question":"SPL","use_llm":action=="chat","request_id":key}));
            assert!(route(action,&json!({"question":"SPL","requestId":"../other"})).is_err());
        }
    }
    #[test] fn failures_never_become_answers(){
        for s in [401,403,404,422,500,502,503,504] {assert!(response(s,json!({"error":{"message":"secret stack"}})).is_err());}
        assert!(response(200,json!({"success":false})).is_err());
        assert_eq!(response(200,json!({"answer":"ok","sources":[]})).unwrap()["answer"],"ok");
        assert_eq!(response(409,json!({"ok":false,"error":{"code":"QUERY_CANCELLED","message":"查询已被取消"}})).unwrap_err(),"QUERY_CANCELLED");
        assert_ne!(response(409,json!({"error":{"code":"OTHER"}})).unwrap_err(),"QUERY_CANCELLED");
    }
    #[test] fn cancel_before_dispatch_is_idempotent_and_prevents_generation(){
        let body=json!({"requestId":uuid::Uuid::new_v4().to_string()});
        control("begin",&body).unwrap();
        assert!(control("begin",&body).is_err());
        let rt=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async {
            REQUESTS.lock().unwrap().get(&request_id(&body).unwrap()).unwrap().started.store(true,Ordering::SeqCst);
            assert_eq!(generate(std::path::PathBuf::new(),"chat".into(),body.clone(),None).await.unwrap_err(),"该查询已提交");
            assert_eq!(cancel(&body).await.unwrap()["ok"],true);
            assert_eq!(cancel(&body).await.unwrap()["ok"],true);
            assert_eq!(generate(std::path::PathBuf::new(),"chat".into(),body,None).await.unwrap_err(),"QUERY_CANCELLED");
        });
    }
    #[test] fn cancel_cloud_retries_registration_race_and_uses_original_credentials(){
        use std::io::{Read,Write};
        for statuses in [vec![404,200],vec![404,404,404,404],vec![401],vec![502]] {
        let last=*statuses.last().unwrap();
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hub=format!("http://{}",listener.local_addr().unwrap());
        let body=json!({"requestId":uuid::Uuid::new_v4().to_string()});let key=request_id(&body).unwrap();
        control("begin",&body).unwrap();
        let request=REQUESTS.lock().unwrap().get(&key).unwrap().clone();
        *request.session.lock().unwrap()=Some((hub,"original-test-token".into()));
        let server=std::thread::spawn(move||{
            for status in statuses {
                let (mut socket,_)=listener.accept().unwrap();socket.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut bytes=Vec::new();let mut b=[0;1024];
                while !bytes.windows(4).any(|w|w==b"\r\n\r\n") {let n=socket.read(&mut b).unwrap();assert!(n>0);bytes.extend_from_slice(&b[..n]);}
                let headers=String::from_utf8(bytes).unwrap();
                assert!(headers.starts_with(&format!("POST /api/qa/cancel/{key} HTTP/1.1")));
                assert!(headers.to_lowercase().contains("authorization: bearer original-test-token"));
                let payload=if status==200 {json!({"ok":true,"cancelled":key})}else{json!({"ok":false,"error":{"code":"UPSTREAM_ERROR"}})}.to_string();
                write!(socket,"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",payload.len()).unwrap();
            }
        });
        let rt=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let result=rt.block_on(cancel(&body));
        match last {
            200=>assert_eq!(result.unwrap()["ok"],true),
            404=>assert_eq!(result.unwrap()["cloud_cancelled"],false),
            _=>assert!(result.is_err()),
        }
        assert!(request.cancelled.load(Ordering::SeqCst));server.join().unwrap();
        }
    }
    #[test] fn image_credentials_only_go_to_the_configured_proxy(){
        let hub="https://cloud.example";
        assert_eq!(image_path("/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fa.jpg&sig=abc",hub).unwrap(),"/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fa.jpg&sig=abc");
        for url in ["https://evil.example/api/figures/proxy?x=1","//evil.example/api/figures/proxy?x=1","/api/admin/users?x=1","javascript:alert(1)","data:image/svg+xml,evil","https://u:p@cloud.example/api/figures/proxy?x=1","/api/figures/proxy?x=1#bad"] {assert!(image_path(url,hub).is_err(),"{url}");}
    }
    #[test] fn sse_parser_handles_split_utf8_crlf_and_ignores_keepalives_and_raw(){
        let wire=": ping\r\n\r\nevent: raw\r\ndata: unclassified\r\n\r\nevent: progress\r\ndata: {\"stage\":\"entities_identified\",\r\ndata: \"label\":\"已识别实体\"}\r\n\r\nevent: answer_delta\ndata: {\"content\":\"光合作用🌿\"}\n\nevent: complete\ndata: {\"answer\":\"最终答案\",\"sources\":[]}\n\n";
        for width in [1,2,7,13,wire.len()] {
            let mut decoder=SseDecoder::default();let mut events=Vec::new();
            for chunk in wire.as_bytes().chunks(width){events.extend(decoder.push(chunk).unwrap());}
            assert_eq!(events.len(),3);
            assert_eq!(events[0].1["label"],"已识别实体");assert_eq!(events[1].1["content"],"光合作用🌿");
            assert_eq!(events[2].0,"complete");assert_eq!(events[2].1["answer"],"最终答案");
        }
        let terminal=SseDecoder::default().push(b"event: complete\ndata: {\"answer\":\"done\"}\n\nevent: error\ndata: malformed trailing frame\n\n").unwrap();
        assert_eq!(terminal.len(),1);assert_eq!(terminal[0].1["answer"],"done");
    }
    #[test] fn sse_parser_rejects_malformed_and_oversized_data(){
        for malformed in [b"event: progress\ndata: nope\n\n".as_slice(),b"event: complete\ndata: []\n\n",b"event: progress\ndata: \xff\n\n"] {
            assert_eq!(SseDecoder::default().push(malformed).unwrap_err(),INVALID_STREAM);
        }
        assert_eq!(SseDecoder::default().push(&vec![b'x';MAX_SSE_FRAME+1]).unwrap_err(),INVALID_STREAM);
        let mut decoder=SseDecoder{total_bytes:MAX_SSE_BYTES,..Default::default()};
        assert_eq!(decoder.push(b"x").unwrap_err(),INVALID_STREAM);
    }
    fn local_stream(wire:&str,status:u16,content_type:&str)->(String,std::thread::JoinHandle<()>) {
        use std::io::{Read,Write};
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url=format!("http://{}/api/qa/chat/stream",listener.local_addr().unwrap());
        let (wire,content_type)=(wire.as_bytes().to_vec(),content_type.to_string());
        let worker=std::thread::spawn(move||{
            let (mut socket,_)=listener.accept().unwrap();socket.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
            let mut request=Vec::new();let mut bytes=[0;1024];
            while !request.windows(4).any(|w|w==b"\r\n\r\n"){let count=socket.read(&mut bytes).unwrap();assert!(count>0);request.extend_from_slice(&bytes[..count]);}
            assert!(String::from_utf8_lossy(&request).starts_with("POST /api/qa/chat/stream HTTP/1.1"));
            write!(socket,"HTTP/1.1 {status} Test\r\nContent-Type: {content_type}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n").unwrap();
            for chunk in wire.chunks(7) {
                if write!(socket,"{:x}\r\n",chunk.len()).and_then(|_|socket.write_all(chunk)).and_then(|_|socket.write_all(b"\r\n")).is_err(){return;}
            }
            let _=socket.write_all(b"0\r\n\r\n");
        });
        (url,worker)
    }
    #[test] fn http_stream_delivers_progress_and_deltas_then_authoritative_complete(){
        let wire="event: progress\r\ndata: {\"stage\":\"understanding_started\",\"label\":\"开始理解问题\",\"phase\":\"planning\"}\r\n\r\nevent: answer_delta\ndata: {\"content\":\"草稿\"}\n\nevent: evidence\ndata: {\"answer\":\"\",\"sources\":[{\"id\":\"S1\"}]}\n\nevent: complete\ndata: {\"answer\":\"最终答案 ![figure](/api/figures/proxy?x=1)\",\"sources\":[{\"id\":\"S1\"}],\"timing\":{\"total\":1}}\n\n";
        let (url,worker)=local_stream(wire,200,"text/event-stream; charset=utf-8");
        let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let mut emitted=Vec::new();let mut checks=0;
        let result=runtime.block_on(async {
            let res=reqwest::Client::builder().no_proxy().build().unwrap().post(url).send().await.unwrap();
            read_stream(res,|event,payload|{emitted.push((event.to_string(),payload));Ok(())},||{checks+=1;std::future::ready(Ok(()))}).await
        }).unwrap();
        worker.join().unwrap();assert!(checks>0);assert_eq!(emitted.len(),3);
        assert_eq!(emitted[0].0,"progress");assert_eq!(emitted[1].1["content"],"草稿");assert_eq!(emitted[2].0,"evidence");
        assert_eq!(result["answer"],"最终答案 ![figure](/api/figures/proxy?x=1)");assert_eq!(result["sources"][0]["id"],"S1");
    }
    #[test] fn http_stream_failure_modes_do_not_leak_upstream_messages(){
        for (wire,status,mime,expected) in [
            ("event: error\ndata: {\"code\":\"query_cancelled\",\"message\":\"secret stack\"}\n\n",200,"text/event-stream","QUERY_CANCELLED"),
            ("event: error\ndata: {\"code\":\"INVALID_CITATION\",\"message\":\"secret stack\"}\n\n",200,"text/event-stream","QA_INVALID_CITATION"),
            ("event: timeout\ndata: {\"message\":\"secret stack\"}\n\n",200,"text/event-stream","QA_TIMEOUT"),
            (": ping\n\n",200,"text/event-stream","QA_CONNECTION_CLOSED"),
            ("event: answer_delta\ndata: {\"content\":3}\n\n",200,"text/event-stream","QA_STREAM_INVALID"),
            ("event: complete\ndata: {\"sources\":[]}\n\n",200,"text/event-stream","QA_STREAM_INVALID"),
            ("{\"answer\":\"not SSE\"}",200,"application/json","QA_STREAM_INVALID"),
            ("{\"error\":{\"code\":\"query_cancelled\"}}",409,"application/json","QUERY_CANCELLED"),
            ("{\"error\":{\"message\":\"secret stack\"}}",401,"application/json","UNAUTHORIZED"),
            ("{\"error\":{\"message\":\"route absent\"}}",404,"application/json","QA_UNAVAILABLE"),
            ("{\"error\":{\"message\":\"route absent\"}}",405,"application/json","QA_UNAVAILABLE"),
        ] {
            let (url,worker)=local_stream(wire,status,mime);
            let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
            let result=runtime.block_on(async{
                let res=reqwest::Client::builder().no_proxy().build().unwrap().post(url).send().await.unwrap();
                read_stream(res,|_,_|panic!("failure must not emit content"),||std::future::ready(Ok(()))).await
            }).unwrap_err();
            worker.join().unwrap();assert!(result.starts_with(expected),"{result}");assert!(!result.contains("secret"));
        }
    }
    #[test] fn switched_account_never_receives_stream_content(){
        let (url,worker)=local_stream("event: answer_delta\ndata: {\"content\":\"private\"}\n\nevent: complete\ndata: {\"answer\":\"private\"}\n\n",200,"text/event-stream");
        let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let result=runtime.block_on(async{
            let res=reqwest::Client::builder().no_proxy().build().unwrap().post(url).send().await.unwrap();
            read_stream(res,|_,_|panic!("invalidated account must not receive content"),||std::future::ready(Err("UNAUTHORIZED: changed".into()))).await
        }).unwrap_err();
        worker.join().unwrap();assert!(result.starts_with("UNAUTHORIZED"));
    }
    fn cancel_headers(socket:&mut std::net::TcpStream)->String {
        use std::io::Read;
        socket.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let mut bytes=Vec::new();let mut buffer=[0;1024];
        while !bytes.windows(4).any(|w|w==b"\r\n\r\n") {let n=socket.read(&mut buffer).unwrap();assert!(n>0);bytes.extend_from_slice(&buffer[..n]);}
        String::from_utf8(bytes).unwrap()
    }
    #[test] fn logout_cancels_registered_queries_using_original_credentials_and_blocks_new_queries(){
        use std::io::Write;
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hub=format!("http://{}",listener.local_addr().unwrap());
        let body=json!({"requestId":uuid::Uuid::new_v4().to_string()});let key=request_id(&body).unwrap();
        let waiting=json!({"requestId":uuid::Uuid::new_v4().to_string()});
        control("begin",&body).unwrap();control("begin",&waiting).unwrap();
        let request=REQUESTS.lock().unwrap().get(&key).unwrap().clone();
        let waiting_request=REQUESTS.lock().unwrap().get(&request_id(&waiting).unwrap()).unwrap().clone();
        *request.session.lock().unwrap()=Some((hub,"initiating-account-only".into()));
        let server=std::thread::spawn(move||{
            let (mut socket,_)=listener.accept().unwrap();let headers=cancel_headers(&mut socket);
            assert!(headers.starts_with(&format!("POST /api/qa/cancel/{key} HTTP/1.1")));
            assert!(headers.to_ascii_lowercase().contains("authorization: bearer initiating-account-only"));
            write!(socket,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{{\"ok\":true}}").unwrap();
        });
        let rt=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let guard=rt.block_on(prepare_logout());
        assert!(request.cancelled.load(Ordering::SeqCst));assert!(waiting_request.cancelled.load(Ordering::SeqCst));
        assert!(REQUESTS.lock().unwrap().is_empty());assert!(control("begin",&waiting).is_err());
        drop(guard);control("begin",&waiting).unwrap();rt.block_on(cancel(&waiting)).unwrap();
        server.join().unwrap();
    }
    #[test] fn logout_cancellation_has_a_total_deadline_when_cloud_stalls(){
        use std::io::Read;
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hub=format!("http://{}",listener.local_addr().unwrap());
        let body=json!({"requestId":uuid::Uuid::new_v4().to_string()});let key=request_id(&body).unwrap();
        control("begin",&body).unwrap();let request=REQUESTS.lock().unwrap().get(&key).unwrap().clone();
        *request.session.lock().unwrap()=Some((hub,"deadline-account".into()));
        let server=std::thread::spawn(move||{
            let (mut socket,_)=listener.accept().unwrap();cancel_headers(&mut socket);
            let mut byte=[0];assert_eq!(socket.read(&mut byte).unwrap(),0,"Deadline must drop the cancellation connection");
        });
        let rt=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let started=Instant::now();
        let guard=rt.block_on(async {
            let guard=prepare_logout_with_timeout(Duration::from_millis(150)).await;
            tokio::time::sleep(Duration::from_millis(100)).await;guard
        });
        assert!(started.elapsed()<Duration::from_secs(2));assert!(request.cancelled.load(Ordering::SeqCst));
        assert!(REQUESTS.lock().unwrap().is_empty());drop(guard);server.join().unwrap();
    }
    #[test] fn logout_also_cancels_a_stop_request_already_waiting_for_cloud(){
        use std::io::Write;
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hub=format!("http://{}",listener.local_addr().unwrap());
        let body=json!({"requestId":uuid::Uuid::new_v4().to_string()});let key=request_id(&body).unwrap();
        control("begin",&body).unwrap();let request=REQUESTS.lock().unwrap().get(&key).unwrap().clone();
        *request.session.lock().unwrap()=Some((hub,"stop-then-logout-account".into()));
        let arrived=Arc::new(AtomicBool::new(false));let ready=arrived.clone();let server_key=key.clone();
        let server=std::thread::spawn(move||{
            let (mut first,_)=listener.accept().unwrap();let first_headers=cancel_headers(&mut first);ready.store(true,Ordering::SeqCst);
            let (mut second,_)=listener.accept().unwrap();let second_headers=cancel_headers(&mut second);
            for headers in [first_headers,second_headers] {
                assert!(headers.starts_with(&format!("POST /api/qa/cancel/{server_key} HTTP/1.1")));
                assert!(headers.to_ascii_lowercase().contains("authorization: bearer stop-then-logout-account"));
            }
            for socket in [&mut second,&mut first] {write!(socket,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{{\"ok\":true}}").unwrap();}
        });
        let rt=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(async {
            let stop=tokio::spawn(async move {cancel(&body).await});
            tokio::time::timeout(Duration::from_secs(2),async {while !arrived.load(Ordering::SeqCst){tokio::time::sleep(Duration::from_millis(5)).await;}}).await.unwrap();
            assert!(REQUESTS.lock().unwrap().contains_key(&key));
            let guard=prepare_logout().await;assert!(REQUESTS.lock().unwrap().is_empty());
            stop.await.unwrap().unwrap();drop(guard);
        });
        server.join().unwrap();
    }
    #[test] fn cancellation_interrupts_open_http_stream_and_cancels_original_cloud_request(){
        use std::io::{Read,Write};
        fn headers(socket:&mut std::net::TcpStream)->String {
            socket.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
            let mut bytes=Vec::new();let mut chunk=[0;1024];
            while !bytes.windows(4).any(|w|w==b"\r\n\r\n") {let count=socket.read(&mut chunk).unwrap();assert!(count>0);bytes.extend_from_slice(&chunk[..count]);}
            String::from_utf8(bytes).unwrap()
        }
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let hub=format!("http://{}",listener.local_addr().unwrap());
        let body=json!({"requestId":uuid::Uuid::new_v4().to_string()});let key=request_id(&body).unwrap();
        control("begin",&body).unwrap();
        let request=REQUESTS.lock().unwrap().get(&key).unwrap().clone();
        *request.session.lock().unwrap()=Some((hub.clone(),"original-stream-test-token".into()));
        let (ready,wait_ready)=std::sync::mpsc::channel();let server_key=key.clone();
        let server=std::thread::spawn(move||{
            let (mut stream,_)=listener.accept().unwrap();headers(&mut stream);
            let progress="event: progress\ndata: {\"stage\":\"related_search_started\"}\n\n";
            write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n{:x}\r\n{progress}\r\n",progress.len()).unwrap();
            ready.send(()).unwrap();
            let (mut cancellation,_)=listener.accept().unwrap();let request=headers(&mut cancellation);
            assert!(request.starts_with(&format!("POST /api/qa/cancel/{server_key} HTTP/1.1")));
            assert!(request.to_ascii_lowercase().contains("authorization: bearer original-stream-test-token"));
            write!(cancellation,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{{\"ok\":true}}").unwrap();
            std::thread::sleep(Duration::from_millis(150));
        });
        let cancel_worker=std::thread::spawn(move||{
            wait_ready.recv_timeout(Duration::from_secs(5)).unwrap();
            let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
            assert_eq!(runtime.block_on(cancel(&body)).unwrap()["ok"],true);
        });
        let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let result=runtime.block_on(async{
            let res=reqwest::Client::builder().no_proxy().build().unwrap().post(format!("{hub}/api/qa/chat/stream")).send().await.unwrap();
            tokio::time::timeout(Duration::from_secs(2),cancelable(read_stream(res,|event,_|{assert_eq!(event,"progress");Ok(())},||std::future::ready(Ok(()))),request.cancelled.clone())).await.unwrap()
        }).unwrap_err();
        cancel_worker.join().unwrap();server.join().unwrap();assert_eq!(result,"QUERY_CANCELLED");
        assert!(!REQUESTS.lock().unwrap().contains_key(&key));
    }
}
