"""Actual payment UI with local-only synthetic IPC; never creates a cloud order."""
import base64
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41748').rstrip('/')
OUT = ROOT / 'validation-local/payment-browser'
OUT.mkdir(parents=True, exist_ok=True)
BROWSER = os.environ.get('OMAAX_QA_BROWSER', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')

FIXTURE = r"""() => {
  const clone=value=>structuredClone(value);
  const r=window.__payment={orders:[],unresolved:[],calls:[],next:1,nextPaid:false,createMode:'normal',cancelPaid:false};
  r.make=(channel,id,amount='88.80',subject='Research service',expired=false)=>({
    out_trade_no:id,channel,total_amount:amount,subject,status:'WAIT_PAY',
    ...(channel==='wechat'?{code_url:'weixin://wxpay/bizpayurl?pr=qa-fixture-'+id}:{pay_url:'https://openapi.alipay.com/gateway.do?method=alipay.trade.page.pay&sign=LOCAL_FIXTURE'}),
    created_at:new Date(Date.now()-(expired?7300000:0)).toISOString(),expires_at:new Date(Date.now()+(expired?-100000:7200000)).toISOString(),
    trade_no:null,paid_at:null,refunded_amount:null,expired
  });
  r.paid=order=>({...order,status:'PAID',trade_no:'QA-TRANSACTION-'+order.out_trade_no,paid_at:new Date().toISOString(),last_checked_at:new Date().toISOString()});
  r.put=order=>{r.orders=[clone(order),...r.orders.filter(o=>o.out_trade_no!==order.out_trade_no)];return clone(order);};
  window.biodsh.payment=async(action,body={})=>{
    r.calls.push({action,body:clone(body),at:Date.now()});
    if(action==='list')return clone({orders:r.orders,unresolved:r.unresolved});
    if(action==='create'){
      if(r.createMode==='unknown'){
        r.unresolved.push({id:'intent-'+r.next++,channel:body.channel,total_amount:body.total_amount,subject:body.subject,created_at:new Date().toISOString(),reason:'UNKNOWN'});
        r.createMode='normal';throw Error('PAYMENT_UNKNOWN');
      }
      const id=(body.channel==='wechat'?'WX':'PAY')+'QATEST'+r.next++;
      return r.put(r.make(body.channel,id,body.total_amount,body.subject));
    }
    if(action==='resolve'){
      const intent=r.unresolved.find(i=>i.id===body.id);if(!intent)throw Error('PAYMENT_NOT_FOUND');
      const order=r.paid(r.make(intent.channel,body.out_trade_no,intent.total_amount,intent.subject));delete order.code_url;delete order.pay_url;
      r.unresolved=r.unresolved.filter(i=>i.id!==body.id);return r.put(order);
    }
    if(action==='resolve_not_created'){
      if(body.confirmation!=='support_confirmed_no_order')throw Error('PAYMENT_CONFIRMATION');
      r.unresolved=r.unresolved.filter(i=>i.id!==body.id);return {ok:true};
    }
    const order=r.orders.find(o=>o.out_trade_no===body.out_trade_no);if(!order)throw Error('PAYMENT_NOT_FOUND');
    if(action==='query'){return r.nextPaid?r.put(r.paid(order)):clone(order);}
    if(action==='cancel'){return r.put(r.cancelPaid?r.paid(order):{...order,status:'CLOSED',last_checked_at:new Date().toISOString()});}
    if(action==='open')return {ok:true};
    throw Error('unexpected payment action');
  };
}"""

def guarded_page(browser, errors, external, width=1440):
    page = browser.new_page(viewport={'width':width, 'height':1050})
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(request):
        if request.request.url.startswith((BASE+'/', BASE.replace('http:', 'ws:')+'/', 'data:', 'blob:')):
            request.continue_()
        else:
            external.append(request.request.url)
            request.abort()
    page.route('**/*', route)
    return page

def open_fixture(page, role='qa'):
    page.goto(BASE+'/workflow-preview.html?auth='+role+'&cloud=1')
    page.wait_for_load_state('networkidle')
    page.evaluate(FIXTURE)

def show_payments(page):
    page.get_by_role('button', name='设置', exact=True).click()
    expect(page.get_by_role('heading', name='付款与订单', exact=True)).to_be_visible()
    expect(page.get_by_text('暂无订单记录', exact=True)).to_be_visible()

def create(page, channel='wechat', amount='88.8', subject='Research service'):
    page.locator('#payment-amount').fill(amount)
    page.locator('#payment-subject').fill(subject)
    page.locator('.payment-method.'+channel).click()
    page.locator('.payment-create').click()

def call_count(page, action):
    return page.evaluate('(a)=>window.__payment.calls.filter(c=>c.action===a).length', action)

def main():
    checks, errors, external = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=BROWSER, headless=True)
        page = guarded_page(browser, errors, external)
        try:
            open_fixture(page)
            show_payments(page)
            page.screenshot(path=str(OUT/'empty-zh.png'))
            (OUT/'initial-dom.html').write_text(page.content(), encoding='utf-8')
            expect(page.locator('.omaax-settings-nav [role=tab]').first).to_contain_text('账号与支付')
            expect(page.locator('.omaax-settings-nav [role=tab]').first).to_have_attribute('aria-selected','true')
            text=page.locator('#settings-pane-account').inner_text()
            for fake in ['基础版','专业版','旗舰版','本月用量','积分（Token）记录','剩余额度','数据不代表实际账单']:
                assert fake not in text, fake
            expect(page.get_by_role('heading',name='模型使用情况',exact=True)).to_be_visible()
            checks.append('Settings first item is account and payments; actual account and local usage remain; fake plans and balances removed')

            create(page)
            qr=page.get_by_alt_text('微信付款二维码',exact=True)
            expect(qr).to_be_visible()
            src=qr.get_attribute('src')
            assert src.startswith('data:image/png;base64,')
            png=base64.b64decode(src.split(',',1)[1]);assert png.startswith(b'\x89PNG\r\n\x1a\n')
            (OUT/'wechat-fixture-qr.png').write_bytes(png)
            from PIL import Image
            image=Image.open(OUT/'wechat-fixture-qr.png').convert('RGB')
            assert image.width==256 and image.height==256 and len(image.getcolors(100000))>1
            decoded=page.evaluate("""async()=>{
              if(!('BarcodeDetector' in window))return null;
              const detector=new BarcodeDetector({formats:['qr_code']});const values=await detector.detect(document.querySelector('.payment-wechat-entry img'));
              return values[0]?.rawValue??null;
            }""")
            if decoded is not None: assert decoded=='weixin://wxpay/bizpayurl?pr=qa-fixture-WXQATEST1'
            assert call_count(page,'create')==1
            request=page.evaluate("window.__payment.calls.find(c=>c.action==='create').body")
            assert request['total_amount']=='88.80' and request['channel']=='wechat' and 'user_id' not in request
            expect(page.locator('.payment-create')).to_be_disabled()
            page.screenshot(path=str(OUT/'wechat-zh.png'))
            page.evaluate('window.__payment.nextPaid=true')
            expect(page.locator('.payment-cashier .payment-result')).to_contain_text('支付成功',timeout=7000)
            expect(qr).to_have_count(0)
            checks.append('WeChat renders a real local PNG QR; one normalized create; server PAID replaces code and enables new orders'+(' (QR decoded)' if decoded else ' (PNG structure verified)'))

            page.evaluate('window.__payment.nextPaid=false')
            create(page,'alipay','12.30','Alipay research payment')
            page.wait_for_function("window.__payment.calls.some(c=>c.action==='open')")
            expect(page.get_by_role('button',name='打开支付宝收银台',exact=True)).to_be_visible()
            expect(page.get_by_alt_text('微信付款二维码',exact=True)).to_have_count(0)
            assert page.url.startswith(BASE+'/') and len(browser.contexts)==1 and len(page.context.pages)==1
            before=call_count(page,'open')
            page.get_by_role('button',name='打开支付宝收银台',exact=True).click()
            page.wait_for_function('(n)=>window.__payment.calls.filter(c=>c.action===\'open\').length>n',arg=before)
            page.get_by_role('button',name='关闭订单',exact=True).click()
            expect(page.locator('.payment-cashier .payment-result')).to_contain_text('订单已关闭')
            checks.append('Alipay uses native open action without renderer navigation; explicit close renders server CLOSED')

            create(page,'wechat','20','Polling pause test')
            expect(page.get_by_alt_text('微信付款二维码',exact=True)).to_be_visible()
            page.get_by_role('tab',name=re.compile('界面偏好')).click()
            stopped=call_count(page,'query')
            page.wait_for_timeout(3300)
            assert call_count(page,'query')==stopped
            page.get_by_role('tab',name=re.compile('账号与支付')).click()
            expect(page.get_by_alt_text('微信付款二维码',exact=True)).to_be_visible()
            page.wait_for_function('(n)=>window.__payment.calls.filter(c=>c.action===\'query\').length>n',arg=stopped)
            page.evaluate('window.__payment.cancelPaid=true')
            page.get_by_role('button',name='关闭订单',exact=True).click()
            expect(page.locator('.payment-cashier .payment-result')).to_contain_text('支付成功')
            page.evaluate('window.__payment.cancelPaid=false')
            checks.append('Leaving payment pane stops polling; return reloads saved order; payment winning cancellation stays PAID')

            page.evaluate("window.__payment.put(window.__payment.make('wechat','WXEXPIRED','30.00','Expired payment',true))")
            page.get_by_role('button',name='刷新订单',exact=True).click()
            page.get_by_role('button',name='查看订单 WXEXPIRED',exact=True).click()
            expect(page.locator('.payment-cashier .payment-result')).to_contain_text('付款入口已过期')
            expect(page.get_by_alt_text('微信付款二维码',exact=True)).to_have_count(0)
            before=call_count(page,'query')
            page.wait_for_timeout(3200)
            assert call_count(page,'query')==before
            assert page.evaluate("window.__payment.orders.find(o=>o.out_trade_no==='WXEXPIRED').status")=='WAIT_PAY'
            page.evaluate('window.__payment.nextPaid=true')
            page.get_by_role('button',name='查询付款结果',exact=True).click()
            expect(page.locator('.payment-cashier .payment-result')).to_contain_text('支付成功')
            page.evaluate('window.__payment.nextPaid=false')
            checks.append('Expired credential stops QR and automatic polling without inventing CLOSED; manual query can confirm PAID')

            page.evaluate("window.__payment.createMode='unknown'")
            create(page,'wechat','40','Unknown response')
            expect(page.get_by_role('heading',name='待核对的付款请求',exact=True)).to_be_visible()
            expect(page.locator('.payment-create')).to_be_disabled()
            created_before=call_count(page,'create')
            page.get_by_label('管理员提供的商户订单号',exact=True).fill('WXRECOVERED')
            page.get_by_role('button',name='核对并找回订单',exact=True).click()
            expect(page.locator('.payment-cashier .payment-result')).to_contain_text('支付成功')
            expect(page.get_by_role('heading',name='待核对的付款请求',exact=True)).to_have_count(0)
            assert call_count(page,'create')==created_before
            page.evaluate("window.__payment.createMode='unknown'")
            create(page,'wechat','50','No server order')
            expect(page.get_by_role('heading',name='待核对的付款请求',exact=True)).to_be_visible()
            created_before=call_count(page,'create')
            page.get_by_text('管理员确认没有生成订单',exact=True).click()
            expect(page.get_by_role('button',name='解除待核对状态',exact=True)).to_be_disabled()
            page.get_by_label('管理员已核实未生成订单',exact=True).check()
            page.get_by_role('button',name='解除待核对状态',exact=True).click()
            expect(page.get_by_role('heading',name='待核对的付款请求',exact=True)).to_have_count(0)
            expect(page.locator('.payment-create')).to_be_enabled()
            assert call_count(page,'create')==created_before
            checks.append('Unknown creation blocks duplicate orders; administrator order recovery and explicit no-order clearance never create another order')

            page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('en');}")
            expect(page.get_by_role('heading',name='Payments & orders',exact=True)).to_be_visible()
            for selector in ['.payment-page','.omaax-settings-nav']:
                assert not re.search('[\u4e00-\u9fff]', page.locator(selector).inner_text()), selector
            for theme in ['light','dark']:
                page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
                for width in [1440,900,768]:
                    page.set_viewport_size({'width':width,'height':1050})
                    page.locator('.payment-heading').scroll_into_view_if_needed()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(theme,width,'document')
                    overflow=page.locator('.payment-page').evaluate('(el)=>({scroll:el.scrollWidth,client:el.clientWidth})')
                    assert overflow['scroll']<=overflow['client']+1,(theme,width,overflow)
                    page.screenshot(path=str(OUT/f'payments-en-{theme}-{width}.png'))
            checks.append('English payment controls fully translate; light and dark layouts have no horizontal overflow at 1440/900/768')
            assert not errors, errors
            assert not external, external
        except Exception:
            page.screenshot(path=str(OUT/'failure.png'))
            (OUT/'failure.html').write_text(page.content(),encoding='utf-8')
            raise
        finally:
            (OUT/'result.json').write_text(json.dumps({'checks':checks,'errors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8')
            browser.close()
    print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))

if __name__=='__main__':
    main()
