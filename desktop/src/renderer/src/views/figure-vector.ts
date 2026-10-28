import {isVectorElement,newFigureId,replaceSvgElementText,sanitizeSvg,svgTextContent} from './figure-model';

const NS='http://www.w3.org/2000/svg',ID='data-vector-id',LOCK='data-vector-locked',WRAPPER='data-vector-wrapper';
export type VectorObject={
  id:string;parentId:string|null;tag:string;name:string;depth:number;text?:string;fill?:string;stroke?:string;
  strokeWidth?:number;fontFamily?:string;fontSize?:number;fontWeight?:string;fontStyle?:string;textAnchor?:string;
  letterSpacing?:number;opacity?:number;locked:boolean;hidden:boolean;transform:string;
};
export type VectorPatch={
  text?:string;fill?:string;stroke?:string;strokeWidth?:number;fontFamily?:string;fontSize?:number;
  fontWeight?:string|number;fontStyle?:string;textAnchor?:string;letterSpacing?:number;opacity?:number;
  hidden?:boolean;locked?:boolean;transform?:string;
};
function documentFor(svg:string){return new DOMParser().parseFromString(sanitizeSvg(svg),'image/svg+xml');}
function serialize(doc:Document){return sanitizeSvg(new XMLSerializer().serializeToString(doc.documentElement));}
function all(root:Element){return [root,...Array.from(root.querySelectorAll('*'))];}
function own(el:Element,name:string):string|undefined{
  const style=el.getAttribute('style')||'';
  for(const declaration of style.split(';').reverse()){
    const [key,value]=declaration.split(/:(.*)/s);
    if(key.trim()===name&&value!==undefined)return value.trim().replace(/\s*!important\s*$/i,'');
  }
  return el.getAttribute(name)??undefined;
}
function inherited(el:Element,name:string,fallback?:string):string|undefined{
  for(let node:Element|null=el;node;node=node.parentElement){const value=own(node,name);if(value!==undefined&&value!=='inherit')return value;}
  return fallback;
}
function numeric(value:string|undefined):number|undefined{
  const match=value?.trim().match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(px|pt|mm|cm|in)?$/i);
  if(!match)return undefined;
  const scale:Record<string,number>={px:1,pt:96/72,mm:96/25.4,cm:96/2.54,in:96},valueNumber=Number(match[1])*(scale[match[2]?.toLowerCase()]||1);
  return Number.isFinite(valueNumber)?valueNumber:undefined;
}
function isLocked(el:Element){for(let node:Element|null=el;node;node=node.parentElement)if(node.getAttribute(LOCK)==='true')return true;return false;}
function isHidden(el:Element){for(let node:Element|null=el;node;node=node.parentElement)if(own(node,'display')==='none'||['hidden','collapse'].includes(own(node,'visibility')||''))return true;return false;}
export function vectorObjects(svg:string):VectorObject[]{
  const root=documentFor(svg).documentElement;
  return Array.from(root.querySelectorAll('['+ID+']')).filter(isVectorElement).map(el=>{
    let parentId:string|null=null,depth=0;
    for(let parent=el.parentElement;parent&&parent!==root;parent=parent.parentElement)if(parent.hasAttribute(ID)){parentId??=parent.getAttribute(ID);depth++;}
    const tag=el.localName,text=['text','tspan','textPath'].includes(tag)?svgTextContent(el):undefined;
    const title=Array.from(el.children).find(child=>child.localName==='title')?.textContent;
    return {id:el.getAttribute(ID)!,parentId,tag,name:(title||text?.trim()||el.getAttribute('id')||tag).slice(0,160),depth,
      ...(text!==undefined?{text}:{}),fill:inherited(el,'fill','black'),stroke:inherited(el,'stroke','none'),
      strokeWidth:numeric(inherited(el,'stroke-width','1')),fontFamily:inherited(el,'font-family','serif'),
      fontSize:numeric(inherited(el,'font-size','16')),fontWeight:inherited(el,'font-weight','normal'),
      fontStyle:inherited(el,'font-style','normal'),textAnchor:inherited(el,'text-anchor','start'),letterSpacing:numeric(inherited(el,'letter-spacing','0'))??0,
      opacity:numeric(own(el,'opacity'))??1,locked:isLocked(el),hidden:isHidden(el),transform:el.getAttribute('transform')||''};
  });
}
function selection(doc:Document,ids:string[],topOnly=false){
  if(!Array.isArray(ids)||ids.length>100_000||ids.some(id=>typeof id!=='string'))throw Error('所选 SVG 元素无效');
  const wanted=new Set(ids),nodes=Array.from(doc.documentElement.querySelectorAll('['+ID+']')).filter(el=>wanted.has(el.getAttribute(ID)!)&&isVectorElement(el));
  if(nodes.length!==wanted.size)throw Error('所选 SVG 元素已变化，请重新选择');
  return topOnly?nodes.filter(el=>!nodes.some(parent=>parent!==el&&parent.contains(el))):nodes;
}
function editable(nodes:Element[],unlock=false){
  for(const node of nodes){
    if((isLocked(node)&&!(unlock&&node.getAttribute(LOCK)==='true'&&!isLocked(node.parentElement!)))||
      Array.from(node.querySelectorAll('['+LOCK+'="true"]')).some(child=>child!==node))throw Error('所选元素或其内容已锁定，请先解锁');
  }
}
function setProperty(el:Element,name:string,value:string|null){
  const parts=(el.getAttribute('style')||'').split(';').filter(part=>part.split(':',1)[0].trim()!==name&&part.trim());
  if(parts.length)el.setAttribute('style',parts.join(';'));else el.removeAttribute('style');
  if(value===null)el.removeAttribute(name);else el.setAttribute(name,value);
}
function finite(value:unknown,min:number,max:number):number{
  if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max)throw Error('SVG 属性数值超出范围');
  return value;
}
function color(value:unknown,doc:Document):string{
  if(typeof value!=='string'||value.length>200||/[<>;{}\\]/.test(value))throw Error('SVG 颜色无效');
  const reference=value.match(/^url\(#([\w:.-]+)\)$/);
  if(reference){if(!Array.from(doc.querySelectorAll('[id]')).some(el=>el.id===reference[1]))throw Error('SVG 颜色引用不存在');return value;}
  if(['none','currentColor','inherit','transparent'].includes(value)||CSS.supports('color',value)&&!/(?:var|url)\s*\(/i.test(value))return value;
  throw Error('SVG 颜色无效');
}
function transform(value:unknown):string{
  if(typeof value!=='string'||value.length>8000)throw Error('SVG 变换无效');
  let cursor=0;
  for(const match of value.matchAll(/(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)/g)){
    if(value.slice(cursor,match.index).trim())throw Error('SVG 变换无效');
    const args=match[2].trim().split(/[\s,]+/).filter(Boolean);
    if(args.some(arg=>!/^[-+]?(?:\d*\.?\d+)(?:e[-+]?\d+)?$/i.test(arg)||!Number.isFinite(Number(arg))||Math.abs(Number(arg))>100_000_000))throw Error('SVG 变换数值无效');
    const counts:Record<string,number[]>={matrix:[6],translate:[1,2],scale:[1,2],rotate:[1,3],skewX:[1],skewY:[1]};
    if(!counts[match[1]].includes(args.length))throw Error('SVG 变换参数无效');
    cursor=match.index!+match[0].length;
  }
  if(value.slice(cursor).trim())throw Error('SVG 变换无效');
  return value.trim();
}
function replaceText(el:Element,value:string){
  if(!['text','tspan','textPath'].includes(el.localName))throw Error('仅文字元素可修改文字');
  replaceSvgElementText(el,value);
}
export function editVector(svg:string,ids:string[],patch:VectorPatch):string{
  const doc=documentFor(svg),nodes=selection(doc,ids);
  const keys=new Set(['text','fill','stroke','strokeWidth','fontFamily','fontSize','fontWeight','fontStyle','textAnchor','letterSpacing','opacity','hidden','locked','transform']);
  if(!patch||typeof patch!=='object'||Object.keys(patch).some(key=>!keys.has(key)))throw Error('不支持此 SVG 编辑属性');
  const onlyLock=Object.keys(patch).every(key=>key==='locked');
  if(!onlyLock)editable(nodes,patch.locked===false);
  for(const node of nodes)for(const [key,value] of Object.entries(patch)){
    if(value===undefined)continue;
    if(key==='text'){if(typeof value!=='string'||value.length>100_000||value.includes('\0'))throw Error('SVG 文字无效');replaceText(node,value);}
    else if(key==='fill'||key==='stroke')setProperty(node,key,color(value,doc));
    else if(key==='strokeWidth')setProperty(node,'stroke-width',String(finite(value,0,10000)));
    else if(key==='fontSize')setProperty(node,'font-size',String(finite(value,.1,10000)));
    else if(key==='letterSpacing')setProperty(node,'letter-spacing',String(finite(value,-10000,10000)));
    else if(key==='opacity')setProperty(node,'opacity',String(finite(value,0,1)));
    else if(key==='fontFamily'){
      if(typeof value!=='string'||!value.trim()||value.length>300||/[<>;{}\\\u0000-\u001f]/.test(value)||/(?:url|var)\s*\(/i.test(value))throw Error('字体名称无效');
      setProperty(node,'font-family',value);
    }else if(key==='fontWeight'){
      if(!/^(normal|bold|bolder|lighter|[1-9]\d{0,2}|1000)$/.test(String(value)))throw Error('字体粗细无效');
      setProperty(node,'font-weight',String(value));
    }else if(key==='fontStyle'){
      if(!['normal','italic','oblique'].includes(String(value)))throw Error('字体样式无效');
      setProperty(node,'font-style',String(value));
    }else if(key==='textAnchor'){
      if(!['start','middle','end'].includes(String(value)))throw Error('文字对齐无效');
      setProperty(node,'text-anchor',String(value));
    }else if(key==='transform'){const next=transform(value);if(next)node.setAttribute('transform',next);else node.removeAttribute('transform');}
    else if(key==='locked'){if(typeof value!=='boolean')throw Error('锁定状态无效');if(value)node.setAttribute(LOCK,'true');else node.removeAttribute(LOCK);}
    else if(key==='hidden'){
      if(typeof value!=='boolean')throw Error('显示状态无效');
      setProperty(node,'display',value?'none':null);
      if(!value&&['hidden','collapse'].includes(own(node,'visibility')||''))setProperty(node,'visibility',null);
    }
  }
  return serialize(doc);
}
export function deleteVectorNodes(svg:string,ids:string[]):string{
  const doc=documentFor(svg),nodes=selection(doc,ids,true);editable(nodes);
  const removedIds=new Set(nodes.flatMap(all).map(node=>node.getAttribute('id')).filter(Boolean));
  for(const node of all(doc.documentElement).filter(node=>!nodes.some(selected=>selected.contains(node))))for(const attr of Array.from(node.attributes)){
    const references=[...(attr.localName==='href'&&attr.value.startsWith('#')?[attr.value.slice(1)]:[]),...Array.from(attr.value.matchAll(/url\(\s*['"]?#([\w:.-]+)['"]?\s*\)/gi),match=>match[1])];
    if(references.some(id=>removedIds.has(id)))throw Error('此元素仍被其他元素引用，请同时选择引用它的元素后删除');
  }
  nodes.forEach(node=>node.remove());return serialize(doc);
}
function remapClones(nodes:Element[],vector=true){
  const references=new Map<string,string>(),allNodes=nodes.flatMap(all),seen=new Set<string>();
  for(const node of allNodes){
    const old=node.getAttribute('id');
    if(old){const id=newFigureId('svg-copy');if(!references.has(old))references.set(old,id);node.setAttribute('id',id);}
    if(node.hasAttribute(ID)){
      if(vector&&isVectorElement(node)){let id=newFigureId('vector');while(seen.has(id))id=newFigureId('vector');seen.add(id);node.setAttribute(ID,id);}
      else node.removeAttribute(ID);
    }
  }
  for(const node of allNodes)for(const attribute of Array.from(node.attributes)){
    if(attribute.localName==='href'&&attribute.value.startsWith('#')){const id=references.get(attribute.value.slice(1));if(id)node.setAttribute(attribute.name,'#'+id);}
    else if(/url\(/i.test(attribute.value))node.setAttribute(attribute.name,attribute.value.replace(/url\(\s*['"]?#([\w:.-]+)['"]?\s*\)/gi,(whole,id)=>references.has(id)?'url(#'+references.get(id)+')':whole));
  }
}
export function duplicateVectorNodes(svg:string,ids:string[]):{svg:string;ids:string[]}{
  const doc=documentFor(svg),nodes=selection(doc,ids,true);editable(nodes);
  const clones=nodes.map(node=>node.cloneNode(true) as Element);remapClones(clones);
  nodes.forEach((node,i)=>node.parentNode!.insertBefore(clones[i],node.nextSibling));
  return {svg:serialize(doc),ids:clones.map(node=>node.getAttribute(ID)!)};
}
export function groupVectorNodes(svg:string,ids:string[]):{svg:string;id:string}{
  const doc=documentFor(svg),nodes=selection(doc,ids,true);editable(nodes);
  if(nodes.length<2)throw Error('请选择至少两个同级元素');
  const parent=nodes[0].parentNode!;
  if(nodes.some(node=>node.parentNode!==parent))throw Error('请先选择同一父级的元素，避免改变已有变换与裁剪');
  const group=doc.createElementNS(NS,'g'),id=newFigureId('vector');group.setAttribute(ID,id);
  parent.insertBefore(group,nodes[0]);nodes.forEach(node=>group.appendChild(node));
  return {svg:serialize(doc),id};
}
export function ungroupVectorNodes(svg:string,ids:string[]):{svg:string;ids:string[]}{
  const doc=documentFor(svg),nodes=selection(doc,ids,true);editable(nodes);const children:string[]=[];
  for(const node of nodes){
    if(!['g','a'].includes(node.localName))throw Error('只能取消 SVG 组合');
    // Keep the structural context: distributing opacity/filter/clip across children changes compositing.
    node.removeAttribute(ID);node.setAttribute(WRAPPER,'true');
    for(const child of Array.from(node.querySelectorAll('['+ID+']'))){
      let ancestor=child.parentElement;while(ancestor&&ancestor!==node&&!ancestor.hasAttribute(ID))ancestor=ancestor.parentElement;
      if(ancestor===node)children.push(child.getAttribute(ID)!);
    }
  }
  return {svg:serialize(doc),ids:children};
}
export function reorderVectorNodes(svg:string,ids:string[],action:'front'|'back'|'forward'|'backward'):string{
  if(!['front','back','forward','backward'].includes(action))throw Error('不支持此 SVG 图层顺序操作');
  const doc=documentFor(svg),nodes=selection(doc,ids,true);editable(nodes);
  if(!nodes.length)return serialize(doc);
  const parent=nodes[0].parentElement!;
  if(nodes.some(node=>node.parentElement!==parent))throw Error('只能调整同一父级元素的顺序，请分别选择');
  const selected=new Set(nodes),slots=Array.from(parent.childNodes);
  const candidates=Array.from(parent.children).filter(node=>node.hasAttribute(ID)||node.getAttribute(WRAPPER)==='true');
  let ordered=[...candidates];
  if(action==='front')ordered=[...candidates.filter(node=>!selected.has(node)),...candidates.filter(node=>selected.has(node))];
  else if(action==='back')ordered=[...candidates.filter(node=>selected.has(node)),...candidates.filter(node=>!selected.has(node))];
  else if(action==='forward'){
    for(let i=ordered.length-2;i>=0;i--)if(selected.has(ordered[i])&&!selected.has(ordered[i+1]))[ordered[i],ordered[i+1]]=[ordered[i+1],ordered[i]];
  }else{
    for(let i=1;i<ordered.length;i++)if(selected.has(ordered[i])&&!selected.has(ordered[i-1]))[ordered[i],ordered[i-1]]=[ordered[i-1],ordered[i]];
  }
  const candidateSet=new Set<Node>(candidates);let index=0;
  // Definitions, titles and text nodes retain their slots; only drawing siblings change order.
  parent.replaceChildren(...slots.map(node=>candidateSet.has(node)?ordered[index++]:node));
  return serialize(doc);
}
function referencedIds(nodes:Element[]):Set<string>{
  const result=new Set<string>();
  for(const node of nodes.flatMap(all))for(const attr of Array.from(node.attributes)){
    if(attr.localName==='href'&&attr.value.startsWith('#'))result.add(attr.value.slice(1));
    for(const match of attr.value.matchAll(/url\(\s*['"]?#([\w:.-]+)['"]?\s*\)/gi))result.add(match[1]);
  }
  return result;
}
function contextClone(source:Element,root:Element,target:Document):{top:Element;selected:Element}{
  const selected=target.importNode(source,true) as Element;let top=selected;
  for(let ancestor=source.parentElement;ancestor;ancestor=ancestor.parentElement){
    const wrapper=target.importNode(ancestor,false) as Element;
    wrapper.removeAttribute(ID);wrapper.removeAttribute('id');wrapper.removeAttribute(LOCK);wrapper.setAttribute(WRAPPER,'true');
    if(ancestor===root){
      const [x,y,width,height]=root.getAttribute('viewBox')!.split(/[ ,]+/).map(Number);
      wrapper.setAttribute('x',String(x));wrapper.setAttribute('y',String(y));wrapper.setAttribute('width',String(width));wrapper.setAttribute('height',String(height));
    }
    wrapper.appendChild(top);top=wrapper;if(ancestor===root)break;
  }
  return {top,selected};
}
export function pasteVectorNodes(targetSvg:string,sourceSvg:string,ids:string[]):{svg:string;ids:string[]}{
  const source=documentFor(sourceSvg),target=documentFor(targetSvg),nodes=selection(source,ids,true);editable(nodes);
  const contexts=nodes.map(node=>contextClone(node,source.documentElement,target)),clones=contexts.map(value=>value.top);
  const known=new Set(clones.flatMap(all).map(node=>node.getAttribute('id')).filter((id):id is string=>!!id));
  const sourceById=new Map(Array.from(source.querySelectorAll('[id]')).map(node=>[node.id,node]));
  const definitions:Element[]=[];
  for(const id of referencedIds(clones)){
    const pending=[id];
    while(pending.length){
      const dependency=pending.pop()!;if(known.has(dependency))continue;
      const original=sourceById.get(dependency);if(!original)throw Error('复制的 SVG 包含缺失引用');
      const clone=target.importNode(original,true) as Element;
      for(const item of all(clone)){item.removeAttribute(ID);const childId=item.getAttribute('id');if(childId)known.add(childId);}
      definitions.push(clone);pending.push(...referencedIds([clone]));
    }
  }
  remapClones([...definitions,...clones]);
  if(definitions.length){const defs=target.createElementNS(NS,'defs');definitions.forEach(node=>defs.appendChild(node));target.documentElement.insertBefore(defs,target.documentElement.firstChild);}
  clones.forEach(node=>target.documentElement.appendChild(node));
  return {svg:serialize(target),ids:contexts.map(value=>value.selected.getAttribute(ID)!)};
}
export function selectedVectorSvg(svg:string,ids:string[]):string{
  const source=documentFor(svg),nodes=selection(source,ids,true);
  if(!nodes.length)throw Error('请先选择要复制的图内元素');
  const empty=source.implementation.createDocument(NS,'svg',null);
  empty.documentElement.setAttribute('viewBox',source.documentElement.getAttribute('viewBox')!);
  return pasteVectorNodes(new XMLSerializer().serializeToString(empty.documentElement),svg,nodes.map(node=>node.getAttribute(ID)!)).svg;
}
