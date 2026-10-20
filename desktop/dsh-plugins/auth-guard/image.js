import {mkdir,realpath,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

const LIMIT=24*1024*1024;
async function bounded(response) {
  if(!response.ok)throw Error(`图像服务请求失败（HTTP ${response.status}），请检查配置或稍后重试`);
  const parts=[];let size=0;
  for await(const part of response.body){size+=part.length;if(size>LIMIT)throw Error('图像响应过大');parts.push(part);}
  return Buffer.concat(parts);
}
function http(value) {
  const url=new URL(value);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('图像地址无效');
  return url;
}
export function createImageTool(env=process.env,request=fetch) {
  return {
    name:'omaax_generate_image',
    description:'Generate one illustration using the image service configured in Settings. Supply prompt and optional size; returns a saved image and Markdown preview. No shell or file deletion. Do not invent scientific measurements.',
    parameters:{type:'object',properties:{prompt:{type:'string',minLength:1,maxLength:16000},size:{type:'string',pattern:'^[0-9]{3,4}x[0-9]{3,4}$'}},required:['prompt'],additionalProperties:false},
    output:{schema:{type:'object',properties:{path:{type:'string'},markdown:{type:'string'}},required:['path','markdown'],additionalProperties:false},render:(_args,value)=>[{type:'text',text:value.markdown+'\n保存位置：'+value.path}]},
    async execute(args,exec) {
      if(!args||typeof args.prompt!=='string'||!args.prompt.trim()||args.prompt.length>16000||Object.keys(args).some(k=>!['prompt','size'].includes(k))||args.size&&!/^\d{3,4}x\d{3,4}$/.test(args.size))throw Error('绘图参数无效');
      if(!env.BIODSH_IMAGE_BASE_URL||!env.BIODSH_IMAGE_MODEL||!env.BIODSH_IMAGE_API_KEY||!env.OMAAX_IMAGE_WORKSPACE)throw Error('请先在设置的工具与服务中配置图像服务');
      const url=http(env.BIODSH_IMAGE_BASE_URL.replace(/\/$/,'')+'/images/generations');
      const signal=AbortSignal.any([exec.signal,AbortSignal.timeout(180000)]);
      const response=await request(url,{method:'POST',redirect:'error',signal,headers:{'Content-Type':'application/json',Authorization:`Bearer ${env.BIODSH_IMAGE_API_KEY}`},body:JSON.stringify({model:env.BIODSH_IMAGE_MODEL,prompt:args.prompt,n:1,size:args.size||'1024x1024'})});
      let body;try{body=JSON.parse((await bounded(response)).toString('utf8'));}catch(error){if(error instanceof SyntaxError)throw Error('图像服务返回格式无效');throw error;}
      const item=body.data?.[0];
      let data;
      if(typeof item?.b64_json==='string')data=Buffer.from(item.b64_json,'base64');
      else if(typeof item?.url==='string')data=await bounded(await request(http(item.url),{signal,redirect:'error'}));
      else throw Error('图像服务未返回图片');
      if(data.length>LIMIT)throw Error('图片过大');
      const ext=data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))?'png':data[0]===255&&data[1]===216&&data[2]===255?'jpg':data.toString('ascii',0,4)==='RIFF'&&data.toString('ascii',8,12)==='WEBP'?'webp':null;
      if(!ext)throw Error('图像服务返回了不支持的图片格式');
      signal.throwIfAborted();
      const root=await realpath(env.OMAAX_IMAGE_WORKSPACE);
      // A fresh directory and exclusive creation cannot overwrite an existing user file.
      const dir=path.join(root,'image-'+randomUUID());await mkdir(dir);
      const file=path.join(dir,'illustration.'+ext);await writeFile(file,data,{flag:'wx'});
      const link=env.BIODSH_FILES_URL?env.BIODSH_FILES_URL+'/'+encodeURIComponent(file):'file:///'+file.replaceAll('\\','/');
      return {path:file,markdown:`![生成的插图](${link})`};
    }
  };
}
