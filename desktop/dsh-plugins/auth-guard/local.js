import {lstat,realpath,readdir,mkdir,rename,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';

export function localExecutionGuard(exec) {
  // Local scripts are user-authorized; this hook is not a script sandbox.
  return;
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
  description:'Move one explicitly requested local file or directory into recoverable trash and record its absolute path, recursive scope and byte size. This tool cannot delete server files.',
  parameters:{type:'object',properties:{path:{type:'string'},reason:{type:'string'}},required:['path','reason'],additionalProperties:false},
  output:{schema:{type:'object',properties:{path:{type:'string'},trashPath:{type:'string'},bytes:{type:'number'}},required:['path','trashPath','bytes'],additionalProperties:false},render:(_args,value)=>[{type:'text',text:`已移入可恢复目录：${value.trashPath}\n原路径：${value.path}\n大小：${value.bytes} 字节`}]},
  async execute(args,exec){
   if(!args||typeof args.path!=='string'||!path.isAbsolute(args.path)||typeof args.reason!=='string'||!args.reason.trim())throw Error('请提供删除原因和本地绝对路径');
   const requested=path.resolve(args.path),target=await realpath(requested);
   if(target.toLowerCase()!==requested.toLowerCase()||target===path.parse(target).root)throw Error('不能删除根目录或通过链接删除');
   const before=await snapshot(target);exec.signal?.throwIfAborted();
   exec.signal?.throwIfAborted();
   if((await realpath(requested))!==target||(await snapshot(target)).digest!==before.digest)throw Error('删除范围已变化，请重新核对，文件保持不变');
   const trash=path.join(path.dirname(target),'.omaax-trash-'+randomUUID());await mkdir(trash);
   await writeFile(path.join(trash,'restore.json'),JSON.stringify({originalPath:target,bytes:before.bytes,createdAt:new Date().toISOString()}),{flag:'wx'});
   const destination=path.join(trash,path.basename(target));await rename(target,destination);
   return {path:target,trashPath:destination,bytes:before.bytes};
  }
 };
}
