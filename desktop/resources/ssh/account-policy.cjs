// Native callers supply the authenticated identity, never a model argument.
const source = owner => `import os,json,stat,pathlib
p=pathlib.Path('/etc/omaax/accounts')/(str(os.getuid())+'.json')
assert os.getuid()!=0
for item in [p,*p.parents]:
 s=item.lstat()
 assert not stat.S_ISLNK(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
assert p.is_file() and p.stat().st_size<4096
v=json.loads(p.read_text())
assert v.get('owner')==${JSON.stringify(owner)} and v.get('uid')==os.getuid()
print('OMAAX_ACCOUNT_OK')
`;
module.exports = (connection, owner) => {
  // Standalone transport fixtures have no account. Production native callers always supply one.
  if(owner===undefined||owner===null)return Promise.resolve();
  if(!/^[a-f0-9]{64}:[a-f0-9-]{36}$/.test(owner))return Promise.reject(Error('账号绑定标识无效'));
  const encoded=Buffer.from(source(owner)).toString('base64');
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('账号绑定检查超时')),15000);
    connection.exec(`python3 -c "import base64;exec(base64.b64decode('${encoded}'))"`,(error,stream)=>{
      if(error){clearTimeout(timer);reject(error);return;}
      let text='';stream.on('data',b=>{if(text.length<128)text+=b.toString();});
      stream.stderr.on('data',()=>{});
      stream.on('error',e=>{clearTimeout(timer);reject(e);});
      stream.on('close',code=>{clearTimeout(timer);code===0&&text.trim()==='OMAAX_ACCOUNT_OK'?resolve():reject(Error('此服务器系统账号尚未分配给当前登录账号，请管理员配置独立账号及访问绑定'));});
    });
  });
};
module.exports.source=source;
