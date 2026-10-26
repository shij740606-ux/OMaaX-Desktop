import {test} from 'node:test';
import assert from 'node:assert/strict';
import {React,act,create,settle,text,fixture,assertEnglish} from './locale-test-utils.mjs';
import QRCode from 'qrcode';

const order = (change={}) => ({out_trade_no:'WX202610260001',channel:'wechat',total_amount:'88.88',subject:'Research payment',status:'WAIT_PAY',code_url:'weixin://wxpay/bizpayurl?pr=local_test_only',created_at:new Date().toISOString(),expires_at:new Date(Date.now()+7_200_000).toISOString(),expired:false,...change});
const intent = (change={}) => ({id:'intent-1',channel:'wechat',total_amount:'88.88',subject:'Research payment',created_at:new Date().toISOString(),reason:'UNKNOWN',...change});
const deferred = () => {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const content = node => text(node).replace(/\s+/g,' ').trim();
const button = (view,label) => view.root.findAllByType('button').find(node=>content(node)===label);
const click = async(view,label) => {const node=button(view,label);assert.ok(node,label);assert.ok(!node.props.disabled,label+' enabled');await act(async()=>{node.props.onClick();await settle();});};
const field = async(view,id,value) => act(async()=>{view.root.findByProps({id}).props.onChange({target:{value}});await settle();});

async function screen({history={orders:[],unresolved:[]},api,lang='zh',auth={},onCommit}={}) {
  const calls=[],timers=new Map(),events=new EventTarget();let sequence=0;
  const account={authenticated:true,hub:'https://example.invalid',user:{id:'account-one',username:'researcher',role:'user',authorizations:[]},...auth};
  const h=fixture({auth:account,api:{payment:async(action,body)=>{calls.push({action,body});if(api)return api(action,body);if(action==='list')return history;return history.orders.find(value=>value.out_trade_no===body.out_trade_no);}}});
  window.setTimeout=(fn,delay)=>{const id=++sequence;timers.set(id,{fn,delay});return id;};
  window.clearTimeout=id=>timers.delete(id);
  document.visibilityState='visible';document.addEventListener=events.addEventListener.bind(events);document.removeEventListener=events.removeEventListener.bind(events);
  const View=h.get('views/AccountView.tsx').default;let view;
  await act(async()=>{const wrapped=h.wrap(View,lang,{embedded:true});view=create(onCommit?React.createElement(React.Profiler,{id:'payment',onRender:()=>{if(view)onCommit(view);}},wrapped):wrapped);await settle();});
  return {h,view,View,calls,timers,account,
    async submit(){await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});},
    async update(){await act(async()=>{view.update(h.wrap(View,lang,{embedded:true}));await settle();});},
    async visibility(value){document.visibilityState=value;await act(async()=>{events.dispatchEvent(new Event('visibilitychange'));await settle();});},
    async timer(){const [id,item]=timers.entries().next().value??[];assert.ok(item,'scheduled timer');timers.delete(id);await act(async()=>{item.fn();await settle();});},
    async close(){await act(async()=>view.unmount());},
  };
}

test('payment amount validates exact decimal CNY bounds without floating point rounding',()=>{
  const h=fixture(),{paymentAmount,paymentSubject,paymentStatus,recoveryOrderNumber}=h.get('views/payment-data.ts');
  for(const [raw,value] of [['0.01','0.01'],['0001.2','1.20'],['100000000','100000000.00'],['88.88','88.88'],[' 5 ','5.00']])assert.equal(paymentAmount(raw),value);
  for(const value of ['0','0.001','1.234','100000000.01','1e6','-1','Infinity','NaN','1,000','.5','5.','1\n2'])assert.equal(paymentAmount(value),null,value);
  for(const value of ['',' ','a/b','a=b','a&b'])assert.equal(paymentSubject(value),false,value);
  assert.equal(paymentSubject('Analysis payment'),true);
  assert.equal(paymentStatus(order({expires_at:'2020-01-01T00:00:00Z'})),'付款入口已过期');
  assert.equal(paymentStatus(order({status:'PAID',expired:true})),'支付成功');
  assert.equal(recoveryOrderNumber('PAYMENT_RECOVERY: {"out_trade_no":"WX123","pay_url":"secret"}'),'WX123');
  assert.equal(recoveryOrderNumber('PAYMENT_RECOVERY: {"out_trade_no":"https://unsafe"}'),null);
});

test('polling is single-flight, backs off, pauses hidden, and stops on expiry or final status',async()=>{
  const {watchPaymentOrder}=fixture().get('views/payment-data.ts');
  let now=0,visible=true,count=0;const timers=new Map();let next=0,pending=deferred();
  const watcher=watchPaymentOrder({query:()=>{count++;return pending.promise;},expiresAt:50_000,visible:()=>visible,now:()=>now,schedule:(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;},clear:id=>timers.delete(id)});
  watcher.refresh();watcher.refresh();assert.equal(count,1);
  pending.resolve(true);await settle();assert.equal([...timers.values()][0].delay,3000);
  visible=false;watcher.refresh();assert.equal(count,1);assert.equal(timers.size,0);
  pending=deferred();visible=true;watcher.refresh();assert.equal(count,2);pending.reject(Error('offline'));await settle();assert.equal([...timers.values()][0].delay,6000);
  pending=deferred();watcher.refresh();pending.resolve(false);await settle();assert.equal(timers.size,0);watcher.refresh();assert.equal(count,3);
  watcher.stop();
  now=60_000;watchPaymentOrder({query:async()=>{count++;return true;},expiresAt:50_000,visible:()=>true,now:()=>now});assert.equal(count,3);
});

test('opening payment page never creates an order; explicit submit passes decimal text once even on double click',async()=>{
  const pending=deferred();let saved=[];
  const x=await screen({api:async(action,body)=>{if(action==='list')return {orders:saved};if(action==='create')return pending.promise;if(action==='query')return saved[0];}});
  try{
    assert.deepEqual(x.calls.map(call=>call.action),['list']);assert.doesNotMatch(text(x.view.toJSON()),/39|99|199|当前套餐|积分（Token）/);
    await field(x.view,'payment-amount','00088.8');await field(x.view,'payment-subject',' Research payment ');await field(x.view,'payment-body',' Note ');
    const submit=x.view.root.findByType('form').props.onSubmit;
    await act(async()=>{submit({preventDefault(){}});submit({preventDefault(){}});await settle();});
    assert.equal(x.calls.filter(call=>call.action==='create').length,1);
    assert.deepEqual(x.calls.find(call=>call.action==='create').body,{channel:'wechat',total_amount:'88.80',subject:'Research payment',body:'Note'});
    saved=[order({total_amount:'88.80'})];await act(async()=>{pending.resolve(saved[0]);await settle();});
    assert.match(text(x.view.toJSON()),/WX202610260001/);assert.equal(button(x.view,'创建支付订单 ¥88.80').props.disabled,true);
    assert.equal(x.calls.filter(call=>call.action==='open').length,0);
  }finally{await x.close();}
});

test('Alipay checkout opens through the native order action, retries without a second order and never renders a QR image',async()=>{
  const saved=order({out_trade_no:'PAY202610260001',channel:'alipay',code_url:undefined,pay_url:'https://openapi.alipay.com/gateway.do?mock=yes'});let opens=0;
  const x=await screen({api:async(action)=>{if(action==='list')return {orders:opens?[saved]:[]};if(action==='create')return saved;if(action==='query')return saved;if(action==='open'){opens++;if(opens===1)throw Error('PAYMENT_CREDENTIAL');}}});
  try{
    await field(x.view,'payment-amount','88.88');await field(x.view,'payment-subject','Research payment');await click(x.view,'支付宝 在浏览器中完成付款');await x.submit();
    assert.equal(x.calls.filter(call=>call.action==='create').length,1);assert.deepEqual(x.calls.find(call=>call.action==='open').body,{out_trade_no:saved.out_trade_no});
    assert.equal(x.view.root.findAllByType('img').length,0);await click(x.view,'打开支付宝收银台');assert.equal(opens,2);assert.equal(x.calls.filter(call=>call.action==='create').length,1);
  }finally{await x.close();}
});

test('switching a restored WeChat order never paints the previous order QR with the new amount',async()=>{
  const previous=QRCode.toDataURL,secondQr=deferred(),frames=[];
  const first=order({code_url:'weixin://wxpay/bizpayurl?pr=first'});
  const second=order({out_trade_no:'WX202610260002',total_amount:'12.34',code_url:'weixin://wxpay/bizpayurl?pr=second'});
  QRCode.toDataURL=async value=>value.endsWith('first')?'data:image/png;base64,first-order-code':secondQr.promise;
  let x;
  try{
    x=await screen({history:{orders:[first,second],unresolved:[]},onCommit:view=>frames.push(JSON.stringify(view.toJSON()))});
    assert.equal(x.view.root.findByType('img').props.src,'data:image/png;base64,first-order-code');
    frames.length=0;
    await act(async()=>{x.view.root.findByProps({'aria-label':'查看订单 '+second.out_trade_no}).props.onClick();await settle();});
    assert.ok(frames.length>0);assert.ok(frames.every(frame=>!frame.includes('first-order-code')),'No committed frame may show another order QR');
    assert.equal(x.view.root.findAllByType('img').length,0);
    await act(async()=>{secondQr.resolve('data:image/png;base64,second-order-code');await settle();});
    assert.equal(x.view.root.findByType('img').props.src,'data:image/png;base64,second-order-code');
  }finally{if(x)await x.close();QRCode.toDataURL=previous;}
});

test('restored orders query server status; cancellation waits for an in-flight query and renders PAID when payment won',async()=>{
  const pending=deferred(),saved=order();
  const x=await screen({api:async action=>{if(action==='list')return {orders:[saved]};if(action==='query')return pending.promise;if(action==='cancel')return {...saved,status:'PAID',paid_at:new Date().toISOString()};}});
  try{
    assert.equal(x.calls.filter(call=>call.action==='query').length,1);await click(x.view,'关闭订单');assert.equal(x.calls.filter(call=>call.action==='cancel').length,0);
    await act(async()=>{pending.resolve(saved);await settle();});assert.equal(x.calls.filter(call=>call.action==='cancel').length,1);
    assert.match(text(x.view.toJSON()),/支付成功/);assert.doesNotMatch(text(x.view.toJSON()),/订单已关闭|Token到账|已开通/);assert.equal(x.timers.size,0);
  }finally{await x.close();}
});

test('unknown creation is not retried and requires verified order recovery before another payment',async()=>{
  let unknown=false;
  const x=await screen({api:async action=>{if(action==='list')return {orders:[],unresolved:unknown?[intent()]:[]};if(action==='create'){unknown=true;throw Error('PAYMENT_UNKNOWN');}if(action==='resolve')return order({status:'PAID'});}});
  try{
    await field(x.view,'payment-amount','88.88');await field(x.view,'payment-subject','Research payment');await x.submit();
    assert.match(text(x.view.toJSON()),/订单是否创建尚未确认/);assert.equal(button(x.view,'创建支付订单 ¥88.88').props.disabled,true);
    await x.submit();assert.equal(x.calls.filter(call=>call.action==='create').length,1);
    const input=x.view.root.findByProps({'aria-label':'管理员提供的商户订单号'});
    await act(async()=>{input.props.onChange({target:{value:'WX202610260001'}});await settle();});await click(x.view,'核对并找回订单');
    assert.deepEqual(x.calls.find(call=>call.action==='resolve').body,{id:'intent-1',out_trade_no:'WX202610260001'});
    assert.match(text(x.view.toJSON()),/支付成功/);assert.doesNotMatch(text(x.view.toJSON()),/待核对的付款请求/);
  }finally{await x.close();}
});

test('an administrator-confirmed missing order can be cleared only by explicit checkbox and action, without auto-creating',async()=>{
  const x=await screen({api:async action=>action==='list'?{orders:[],unresolved:[intent()]}:{ok:true}});
  try{
    assert.equal(button(x.view,'解除待核对状态').props.disabled,true);
    await act(async()=>{x.view.root.findByProps({type:'checkbox'}).props.onChange({target:{checked:true}});await settle();});await click(x.view,'解除待核对状态');
    assert.deepEqual(x.calls.find(call=>call.action==='resolve_not_created').body,{id:'intent-1',confirmation:'support_confirmed_no_order'});
    assert.equal(x.calls.filter(call=>call.action==='create').length,0);assert.doesNotMatch(text(x.view.toJSON()),/待核对的付款请求/);
  }finally{await x.close();}
});

test('a receipt recovered on reload clears the recovery-only lock without creating another order',async()=>{
  let lists=0;const saved=order({status:'PAID'});
  const x=await screen({api:async action=>{
    if(action==='list'){lists++;if(lists===2)throw Error('PAYMENT_STORAGE');return {orders:lists>=3?[saved]:[]};}
    if(action==='create')throw Error('PAYMENT_RECOVERY: {"out_trade_no":"WX202610260001","channel":"wechat","total_amount":"88.88"}');
  }});
  try{
    await field(x.view,'payment-amount','88.88');await field(x.view,'payment-subject','Research payment');await x.submit();
    assert.equal(button(x.view,'创建支付订单 ¥88.88').props.disabled,true);assert.match(text(x.view.toJSON()),/请保留这笔订单号/);
    await click(x.view,'重新读取订单');assert.match(text(x.view.toJSON()),/支付成功/);assert.doesNotMatch(text(x.view.toJSON()),/请保留这笔订单号/);assert.equal(button(x.view,'创建支付订单 ¥88.88').props.disabled,false);
    assert.equal(x.calls.filter(call=>call.action==='create').length,1);
  }finally{await x.close();}
});

test('expired entry is not marked closed, does not poll, and permits an explicit status query',async()=>{
  const saved=order({expires_at:new Date(Date.now()-5000).toISOString()});
  const x=await screen({history:{orders:[saved]}});
  try{
    assert.equal(x.calls.filter(call=>call.action==='query').length,0);assert.equal(x.timers.size,0);
    assert.match(text(x.view.toJSON()),/付款入口已过期/);assert.doesNotMatch(text(x.view.toJSON()),/订单已关闭/);assert.equal(x.view.root.findAllByType('img').length,0);
    await click(x.view,'查询付款结果');assert.equal(x.calls.filter(call=>call.action==='query').length,1);assert.equal(x.timers.size,0);
  }finally{await x.close();}
});

test('page visibility pauses automatic status requests and restoring visibility checks once immediately',async()=>{
  const saved=order();const x=await screen({history:{orders:[saved]}});
  try{
    assert.equal(x.calls.filter(call=>call.action==='query').length,1);await x.visibility('hidden');await x.timer();assert.equal(x.calls.filter(call=>call.action==='query').length,1);assert.equal(x.timers.size,0);
    await x.visibility('visible');assert.equal(x.calls.filter(call=>call.action==='query').length,2);
  }finally{await x.close();assert.equal(x.timers.size,0);}
});

test('401 stops polling and prevents further payments until the user signs in again',async()=>{
  const saved=order();const x=await screen({api:async action=>{if(action==='list')return {orders:[saved]};if(action==='query')throw Error('UNAUTHORIZED');}});
  try{
    assert.match(text(x.view.toJSON()),/请重新登录后查看支付结果/);assert.equal(x.timers.size,0);
    assert.equal(button(x.view,'查询付款结果').props.disabled,true);assert.equal(button(x.view,'关闭订单').props.disabled,true);await x.visibility('hidden');await x.visibility('visible');assert.equal(x.calls.filter(call=>call.action==='query').length,1);
  }finally{await x.close();}
});

test('changing selection serializes native queries and a later 401 keeps payments locked',async()=>{
  const first=order(),second=order({out_trade_no:'WX202610260002'});const pending=deferred();
  const x=await screen({api:async(action,body)=>{if(action==='list')return {orders:[first,second]};if(action==='query'){if(body.out_trade_no===first.out_trade_no)return pending.promise;throw Error('UNAUTHORIZED');}}});
  try{
    await act(async()=>{x.view.root.findByProps({'aria-label':'查看订单 WX202610260002'}).props.onClick();await settle();});
    assert.equal(x.calls.filter(call=>call.action==='query').length,1,'new selection waits for the previous native query');
    await act(async()=>{pending.resolve({...first,status:'PAID'});await settle();});
    assert.match(text(x.view.toJSON()),/登录状态已变化/);assert.equal(button(x.view,'查询付款结果').props.disabled,true);assert.equal(x.timers.size,0);
  }finally{await x.close();}
});

test('rapid order selection skips superseded queued queries and does not write the wrong cashier',async()=>{
  const first=order(),second=order({out_trade_no:'WX202610260002'}),third=order({out_trade_no:'WX202610260003',subject:'Third payment'});const pending=deferred();
  const x=await screen({api:async(action,body)=>{if(action==='list')return {orders:[first,second,third]};if(action==='query')return body.out_trade_no===first.out_trade_no?pending.promise:third;}});
  try{
    for(const id of [second.out_trade_no,third.out_trade_no])await act(async()=>{x.view.root.findByProps({'aria-label':'查看订单 '+id}).props.onClick();await settle();});
    assert.equal(x.calls.filter(call=>call.action==='query').length,1);
    await act(async()=>{pending.resolve(first);await settle();});
    assert.deepEqual(x.calls.filter(call=>call.action==='query').map(call=>call.body.out_trade_no),[first.out_trade_no,third.out_trade_no]);
    const cashier=x.view.root.findByProps({'aria-label':'付款结果'});assert.match(text(cashier),/Third payment/);assert.doesNotMatch(text(cashier),/WX202610260002/);
  }finally{await x.close();}
});

test('closing a newly selected order waits for the previous order query and skips its own queued query',async()=>{
  const first=order(),second=order({out_trade_no:'WX202610260002'});const pending=deferred();
  const x=await screen({api:async(action)=>{if(action==='list')return {orders:[first,second]};if(action==='query')return pending.promise;if(action==='cancel')return {...second,status:'CLOSED'};}});
  try{
    await act(async()=>{x.view.root.findByProps({'aria-label':'查看订单 '+second.out_trade_no}).props.onClick();await settle();});
    await click(x.view,'关闭订单');assert.equal(x.calls.filter(call=>call.action==='cancel').length,0);
    await act(async()=>{pending.resolve(first);await settle();});
    assert.deepEqual(x.calls.filter(call=>call.action==='query').map(call=>call.body.out_trade_no),[first.out_trade_no]);
    assert.deepEqual(x.calls.find(call=>call.action==='cancel').body,{out_trade_no:second.out_trade_no});assert.match(text(x.view.root.findByProps({'aria-label':'付款结果'})),/订单已关闭/);
  }finally{await x.close();}
});

test('late results from an old account cannot enter the new account page; local access cannot create or read payments',async()=>{
  const pending=deferred();let lists=0;
  const x=await screen({api:async action=>{if(action==='list')return ++lists===1?pending.promise:{orders:[]};}});
  try{
    x.account.user={...x.account.user,id:'account-two'};await x.update();await act(async()=>{pending.resolve({orders:[order({subject:'Private old-account order'})]});await settle();});
    assert.doesNotMatch(text(x.view.toJSON()),/Private old-account order|WX202610260001/);assert.equal(lists,2);
  }finally{await x.close();}
  const local=await screen({auth:{localAccess:true}});
  try{assert.deepEqual(local.calls,[]);assert.match(text(local.view.toJSON()),/请登录云端账号/);assert.equal(button(local.view,'创建支付订单 ¥0.00').props.disabled,true);}finally{await local.close();}
});

test('English payment pages and safe error messages translate all app-authored content',async()=>{
  for(const history of [{orders:[]},{orders:[order({status:'PAID',trade_no:'123',paid_at:new Date().toISOString()})]},{orders:[],unresolved:[intent()]}]){
    const x=await screen({history,lang:'en'});try{assertEnglish(x.view);}finally{await x.close();}
  }
  const h=fixture(),{paymentFailure}=h.get('views/payment-data.ts'),{translate}=h.get('i18n.ts');
  for(const code of ['UNAUTHORIZED','FORBIDDEN','PAYMENT_ACCOUNT_CHANGED','PAYMENT_UNKNOWN','PAYMENT_RECOVERY','PAYMENT_INVALID','PAYMENT_BUSY','PAYMENT_PENDING','PAYMENT_CORRUPT','PAYMENT_STORAGE','PAYMENT_PROTOCOL','PAYMENT_CREDENTIAL','PAYMENT_EXPIRED','PAYMENT_CLOSED','PAYMENT_NOT_FOUND','PAYMENT_GATEWAY','PAYMENT_RECOVERY_CONFLICT','PAYMENT_CONFIRMATION','unknown'])assert.doesNotMatch(translate('en',paymentFailure(code)),/[\p{Script=Han}]/u,code);
});
