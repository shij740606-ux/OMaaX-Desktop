import {lstat,realpath,readdir,mkdir,rename,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';

export function localExecutionGuard(exec) {
  if(!['bash','pwsh'].includes(exec.name))return;
  const command=String(exec.arguments?.command||'');
  // Catch direct deletion; this is not a sandbox for arbitrary program behavior.
  if(/(?:^|[\s;&|])(rm|rmdir|del|erase|remove-item|rd)(?:\s|$)|\.(?:unlink|rmtree|remove|delete)\s*\(/i.test(command))return '本地删除请使用 omaax_local_delete：先展示路径、范围和大小并确认，再移入可恢复目录；分析和依赖配置可继续。';
}

async function snapshot(target) {
 const rows=[];let bytes=0,files=0;
 async function walk(p){
  const s=await lstat(p);if(s.isSymbolicLink())throw Error('删除范围含链接，请先单独处理链接，未删除任何文件');
  if(!s.isDirectory()&&!s.isFile())throw Error('不支持删除特殊文件');
  if(rows.length>=100000)throw Error('删除范围过大，请缩小范围');
  rows.push([p,s.size,s.mtimeMs,s.ino,s.isDirectory()]);
  if(s.isDirectory())for(const n of (await readdir(p)).sort())await walk(path.join(p,n));
  else{bytes+=s.size;files++;}
 }
 await walk(target);
 return {bytes,files,digest:createHash('sha256').update(JSON.stringify(rows)).digest('hex')};
}

export function createLocalDeleteTool(approval) {
 return {
  name:'omaax_local_delete',
  description:'Move one explicitly requested local file or directory into recoverable trash after showing its absolute path, recursive scope and byte size and obtaining user confirmation. Never use shell or scripts to delete inputs. This tool cannot delete server files.',
  parameters:{type:'object',properties:{path:{type:'string'},reason:{type:'string'}},required:['path','reason'],additionalProperties:false},
  output:{schema:{type:'object',properties:{path:{type:'string'},trashPath:{type:'string'},bytes:{type:'number'}},required:['path','trashPath','bytes'],additionalProperties:false},render:(_args,value)=>[{type:'text',text:`已移入可恢复目录：${value.trashPath}\n原路径：${value.path}\n大小：${value.bytes} 字节`}]},
  async execute(args,exec){
   if(!args||typeof args.path!=='string'||!path.isAbsolute(args.path)||typeof args.reason!=='string'||!args.reason.trim())throw Error('请提供删除原因和本地绝对路径');
   const requested=path.resolve(args.path),target=await realpath(requested);
   if(target.toLowerCase()!==requested.toLowerCase()||target===path.parse(target).root)throw Error('不能删除根目录或通过链接删除');
   const before=await snapshot(target);exec.signal?.throwIfAborted();
   const result=await approval?.request({agent:exec.agent,toolName:'omaax_local_delete',callId:exec.callId,signal:exec.signal,
    reason:`删除路径：${target}\n范围：该文件或整个目录及其全部子项，共 ${before.files} 个文件\n大小：${before.bytes} 字节\n原因：${args.reason}\n确认后移入同级可恢复目录，默认保留其他输入文件。`});
   if(result!=='allowed-once')throw Error('未确认本地删除，文件保持不变');
   exec.signal?.throwIfAborted();
   if((await realpath(requested))!==target||(await snapshot(target)).digest!==before.digest)throw Error('删除范围已变化，请重新核对，文件保持不变');
   const trash=path.join(path.dirname(target),'.omaax-trash-'+randomUUID());await mkdir(trash);
   await writeFile(path.join(trash,'restore.json'),JSON.stringify({originalPath:target,bytes:before.bytes,createdAt:new Date().toISOString()}),{flag:'wx'});
   const destination=path.join(trash,path.basename(target));await rename(target,destination);
   return {path:target,trashPath:destination,bytes:before.bytes};
  }
 };
}
