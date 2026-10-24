// Format structured server status messages without translating paths, sample names or logs.
type Translate = (source:string, vars?:Record<string,string|number>)=>string;
export function serverDisplayMessage(t:Translate, value:string):string {
  if(t('服务器')==='服务器')return value;
  const prefix=/^(Error:\s*)([\s\S]+)$/.exec(value);
  if(prefix)return prefix[1]+serverDisplayMessage(t,prefix[2]);
  const missing=/^缺少：(.*)$/.exec(value);
  if(missing)return t('缺少：{items}',{items:missing[1].split('、').map(item=>t(item)).join(', ')});
  // These fields are workflow metadata and nested software diagnostics, not user names.
  const patterns:[RegExp,string,string[]][]=[
    [/^(.+)尚不能运行：([\s\S]+)。请先检查并配置所选步骤。$/,'{step}尚不能运行：{detail}。请先检查并配置所选步骤。',['step','detail']],
    [/^(.+)的软件检查未完成：([\s\S]+)；请重新检查，原有环境未更改。$/,'{step}的软件检查未完成：{detail}；请重新检查，原有环境未更改。',['step','detail']],
    [/^(.+)安装后检查未通过：([\s\S]+)$/,'{step}安装后检查未通过：{detail}',['step','detail']],
    [/^R 环境检查未完成：([\s\S]+)$/,'R 环境检查未完成：{detail}',['detail']],
    [/^环境检查未完成：([\s\S]+)$/,'环境检查未完成：{detail}',['detail']],
  ];
  for(const [pattern,key,fields]of patterns){const match=pattern.exec(value);if(match)return t(key,Object.fromEntries(fields.map((field,index)=>[field,field==='detail'?serverDisplayMessage(t,match[index+1]):t(match[index+1])])));}
  return t(value);
}
