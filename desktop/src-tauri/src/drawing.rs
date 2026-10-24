use serde_json::{json,Value};
use tauri::{AppHandle};
use tauri_plugin_dialog::DialogExt;
use std::{fs,io::Write,path::Path,time::Duration};
#[path="drawing_molecular.rs"] mod molecular;
#[path="drawing_figure.rs"] mod figure;

fn text<'a>(body:&'a Value,key:&str,max:usize)->Result<&'a str,String>{
    let s=body[key].as_str().ok_or("绘图内容无效")?;
    if s.len()>max{return Err("绘图内容过大".into());}Ok(s)
}
fn png(body:&Value)->Result<Vec<u8>,String>{
    let (bytes,ext)=crate::cloud_qa::image_bytes(text(body,"png",17_000_000)?)?;
    if ext!="png"||!bytes.starts_with(b"\x89PNG\r\n\x1a\n"){return Err("图片格式无效".into());}Ok(bytes)
}
fn new_file(path:&Path,data:&[u8])->Result<(),String>{
    let mut f=fs::OpenOptions::new().write(true).create_new(true).open(path).map_err(|_|"无法新建绘图文件")?;
    f.write_all(data).map_err(|_|"文件保存未完成".into())
}
fn save_project(project:&str,document:&str,svg:&str,png:&[u8])->Result<Value,String>{
    let target=Path::new(project).join(format!("OMaaX-绘图-{}",uuid::Uuid::new_v4()));
    fs::create_dir(&target).map_err(|_|"无法在项目中新建绘图文件夹")?;
    new_file(&target.join("drawing.omaax.json"),document.as_bytes())?;
    new_file(&target.join("drawing.svg"),svg.as_bytes())?;
    new_file(&target.join("drawing.png"),png)?;
    Ok(json!({"path":target.to_string_lossy()}))
}
fn save_molecular_project(project:&str,document:&str,image:&[u8],svg:Option<&str>)->Result<Value,String>{
    if svg.is_some_and(|s|s.len()>2_000_000){return Err("相互作用图过大".into());}
    let target=Path::new(project).join(format!("OMaaX-分子绘图-{}",uuid::Uuid::new_v4()));
    fs::create_dir(&target).map_err(|_|"无法创建结果文件夹")?;
    new_file(&target.join("structure.omaax.json"),document.as_bytes())?;
    new_file(&target.join("structure.png"),image)?;
    if let Some(svg)=svg{new_file(&target.join("interactions.svg"),svg.as_bytes())?;}
    Ok(json!({"path":target.to_string_lossy()}))
}
pub fn run(app:&AppHandle,paths:&crate::paths::AppPaths,action:&str,body:Value)->Result<Value,String>{
    if action.starts_with("figure_"){return figure::run(app,paths,action,body);}
    let english=body["lang"].as_str()==Some("en");
    match action {
        "interactions"=>molecular::run(app,paths,&body),
        "save_molecule"=>{
            let project=text(&body,"project",8192)?;crate::files::validate_local_path(project)?;
            if !Path::new(project).is_dir(){return Err("请选择有效的本地项目文件夹".into());}
            let document=text(&body,"document",20_000_000)?;
            let _:Value=serde_json::from_str(document).map_err(|_|"结构文件无效")?;
            let image=png(&body)?;
            save_molecular_project(project,document,&image,body["svg"].as_str())
        },
        "generate"|"molecule_style"=>{
            let owner=crate::hub_auth::require(&paths.root,"")?.id;
            let prompt=text(&body,"prompt",16_000)?;
            if prompt.trim().is_empty(){return Err("请填写绘图要求".into());}
            let context=text(&body,"context",160_000)?;
            let (base,key)=crate::company_llm::connection(app,paths)?;
            let system="你是 OMaaX 科研绘图助手，制作技术路线、研究框架和机制示意图。仅返回 JSON，不要代码围栏。结构：{title:string,nodes:[{id:string,text:string,x:number,y:number,width:number,height:number,rotation:number,asset?:string,group?:string,color:string,shape:'rectangle'|'ellipse'|'diamond'}],edges:[{from:string,to:string,label:string}] }。最多40节点、80连线。宽高40..1000，默认240x152；坐标0..4000；旋转-180..180度；充分留白。asset只能取用户上下文素材目录里的id，用合适的生物素材替代普通方框。group相同的节点为一组；nodes顺序是从底层到顶层。根据当前图和对话修改，保留未要求更改的内容、节点ID和素材。color为6位十六进制。不虚构数据或把假设写成已证实结论。";
            let system=if action=="molecule_style" {"你是 OMaaX 分子结构显示助手。仅返回 JSON {style: cartoon或stick或line,color:六位十六进制颜色,radius:2到12的数值,labels:布尔,surface:布尔,rotateX:绕屏幕横轴旋转的角度,rotateY:绕纵轴旋转的角度,zoom:缩放倍率}。旋转-180到180度，默认0；缩放0.5到2，默认1。根据当前设置和用户要求调整显示，保留未要求修改的设置。不得生成或修改原子坐标、相互作用、计算结果。"}else{system};
            let client=reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(Duration::from_secs(240)).build().map_err(|_|"模型连接失败")?;
            let response=client.post(format!("{base}/chat/completions")).bearer_auth(key).json(&json!({"model":"omaax","messages":[{"role":"system","content":system},{"role":"user","content":format!("当前图与修改记录：\n{context}\n本次要求：\n{prompt}")}],"max_tokens":8000,"stream":false})).send().map_err(|_|"绘图服务连接中断，请重试")?;
            if !response.status().is_success(){return Err("模型服务暂不可用，请检查账号服务状态后重试".into());}
            let v:Value=response.json().map_err(|_|"模型回复不完整，请重试")?;
            if v["choices"][0]["finish_reason"]=="length"{return Err("图内容过多，请减少节点或分成多张图".into());}
            if crate::hub_auth::require(&paths.root,"")?.id!=owner{return Err("登录已变化，请重新生成".into());}
            Ok(json!({"text":v["choices"][0]["message"]["content"].as_str().ok_or("未收到绘图内容")?}))
        },
        "open"=>{
            let Some(file)=app.dialog().file().add_filter(if english{"OMaaX Drawing"}else{"OMaaX 绘图"},&["json"]).blocking_pick_file() else{return Ok(Value::Null);};
            let path=file.to_string();crate::files::validate_local_path(&path)?;
            let file=fs::File::open(path).map_err(|_|"无法打开绘图文件")?;
            use std::io::Read;
            let mut data=String::new();file.take(20_000_001).read_to_string(&mut data).map_err(|_|"绘图文件无法读取")?;
            if data.len()>20_000_000{return Err("绘图文件过大".into());}
            serde_json::from_str(&data).map_err(|_|"不是有效的绘图文件".into())
        },
        "export"=>{
            let format=text(&body,"format",8)?;
            let data=match format{"svg"=>text(&body,"svg",20_000_000)?.as_bytes().to_vec(),"png"=>png(&body)?,_=>return Err("不支持此导出格式".into())};
            let name=if english{"Research-drawing"}else{"科研绘图"};
            let Some(file)=app.dialog().file().set_file_name(format!("{name}.{format}")).add_filter(if english{"Images"}else{"图片"},&[format]).blocking_save_file() else{return Ok(Value::Null);};
            let target=file.to_string();
            let parent=Path::new(&target).parent().ok_or("保存位置无效")?;crate::files::validate_local_path(&parent.to_string_lossy())?;
            fs::write(&target,data).map_err(|_|"图片保存失败")?;Ok(json!({"path":target}))
        },
        "save"=>{
            let project=text(&body,"project",8192)?;crate::files::validate_local_path(project)?;
            if !Path::new(project).is_dir(){return Err("请选择有效的本地项目文件夹".into());}
            let document=text(&body,"document",2_000_000)?;
            let _:Value=serde_json::from_str(document).map_err(|_|"绘图文件无效")?;
            let svg=text(&body,"svg",20_000_000)?;let png=png(&body)?;
            // Every save is a new version; existing project files are never overwritten.
            save_project(project,document,svg,&png)
        },
        _=>Err("绘图操作不支持".into())
    }
}

#[cfg(test)] mod tests{
 use super::*;
 #[test] fn molecular_projects_preserve_view_and_inputs_in_separate_versions(){
  let root=std::env::temp_dir().join(format!("omaax-molecule-save-{}",uuid::Uuid::new_v4()));fs::create_dir(&root).unwrap();
  let document=r#"{"format":"omaax-molecule","version":1,"view":[0,1,2,3,4,5,6,7],"protein":{"text":"ORIGINAL_COORDINATES"}}"#;
  let a=save_molecular_project(root.to_str().unwrap(),document,b"image-a",Some("<svg/>")).unwrap();
  let b=save_molecular_project(root.to_str().unwrap(),document,b"image-b",None).unwrap();assert_ne!(a["path"],b["path"]);
  for (value,image) in [(&a,b"image-a"),(&b,b"image-b")]{let dir=std::path::PathBuf::from(value["path"].as_str().unwrap());assert_eq!(fs::read_to_string(dir.join("structure.omaax.json")).unwrap(),document);assert_eq!(fs::read(dir.join("structure.png")).unwrap(),image);}
  assert!(Path::new(a["path"].as_str().unwrap()).join("interactions.svg").exists());assert!(!Path::new(b["path"].as_str().unwrap()).join("interactions.svg").exists());fs::remove_dir_all(root).unwrap();
 }
 #[test] fn saving_never_overwrites_existing_files(){
  let path=std::env::temp_dir().join(format!("omaax-drawing-test-{}",uuid::Uuid::new_v4()));
  new_file(&path,b"first").unwrap();assert!(new_file(&path,b"second").is_err());assert_eq!(fs::read(&path).unwrap(),b"first");fs::remove_file(path).unwrap();
 }
 #[test] fn project_versions_roundtrip_without_overwriting(){
  let root=std::env::temp_dir().join(format!("omaax-drawing-project-{}",uuid::Uuid::new_v4()));fs::create_dir(&root).unwrap();
  let document=r#"{"format":"omaax-drawing","version":1,"diagram":{"title":"Test","nodes":[],"edges":[]}}"#;
  let a=save_project(root.to_str().unwrap(),document,"<svg/>",b"png").unwrap();
  let b=save_project(root.to_str().unwrap(),document,"<svg/>",b"png").unwrap();assert_ne!(a["path"],b["path"]);
  for result in [a,b]{let dir=std::path::PathBuf::from(result["path"].as_str().unwrap());assert_eq!(fs::read_to_string(dir.join("drawing.omaax.json")).unwrap(),document);assert_eq!(fs::read(dir.join("drawing.png")).unwrap(),b"png");assert_eq!(fs::read(dir.join("drawing.svg")).unwrap(),b"<svg/>");fs::remove_dir_all(dir).unwrap();}fs::remove_dir(root).unwrap();
 }
 #[test] fn reject_invalid_export_and_oversized_text(){
  assert!(png(&json!({"png":"data:image/png;base64,eA=="})).is_err());
  assert!(text(&json!({"value":"1234"}),"value",3).is_err());
 }
}
