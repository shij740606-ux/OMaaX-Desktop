// Local fixture only: implements a cancellable delayed MCP tool and records each dispatch.
import {createInterface} from 'node:readline';
import {appendFileSync,readFileSync} from 'node:fs';
const {events,ignoreCancellation=false,tools=['wait_probe']}=JSON.parse(readFileSync(process.argv[2],'utf8'));
const pending=new Map();
const record=(type,detail={})=>appendFileSync(events,JSON.stringify({type,at:Date.now(),pid:process.pid,...detail})+'\n');
const reply=(id,result)=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\n');
record('started');
const input=createInterface({input:process.stdin});
input.on('line',line=>{
  const request=JSON.parse(line);
  record('received',{method:request.method,id:request.id});
  if(request.method==='initialize')reply(request.id,{protocolVersion:request.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'isolated-timeout-fixture',version:'1'}});
  else if(request.method==='tools/list')reply(request.id,{tools:tools.map(name=>({name,description:'Wait once for an isolated local timeout test.',inputSchema:{type:'object',properties:{delayMs:{type:'integer',minimum:1},timeoutSeconds:{type:'integer',minimum:0},action:{type:'string'},caseId:{type:'string'},ignoreCancellation:{type:'boolean'}},required:['delayMs']}}))});
  else if(request.method==='tools/call'){
    const delayMs=request.params.arguments.delayMs;
    record('dispatched',{id:request.id,delayMs,name:request.params.name,args:request.params.arguments});
    const timer=setTimeout(()=>{pending.delete(request.id);record('completed',{id:request.id,caseId:request.params.arguments.caseId});reply(request.id,{content:[{type:'text',text:JSON.stringify({ok:true,delayMs})}]});},delayMs);
    pending.set(request.id,{timer,ignoreCancellation:ignoreCancellation||request.params.arguments.ignoreCancellation,caseId:request.params.arguments.caseId});
  }else if(request.method==='notifications/cancelled'){
    const id=request.params.requestId;
    const call=pending.get(id),ignored=!!call?.ignoreCancellation;
    if(!ignored){clearTimeout(call?.timer);pending.delete(id);}record('cancelled',{id,caseId:call?.caseId,reason:request.params.reason,ignored});
  }else if(request.method==='ping')reply(request.id,{});
  else if(request.id!==undefined)process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,error:{code:-32601,message:'Method not found'}})+'\n');
});
input.on('close',()=>{for(const call of pending.values())clearTimeout(call.timer);record('stdin_closed');process.exit(0);});
