import {useCallback, useEffect, useRef, useState} from 'react';
import {ArrowUpRight, Check, CheckCircle2, Copy, CreditCard, History, Loader2, QrCode, RefreshCw, ShieldCheck, XCircle} from 'lucide-react';
import QRCode from 'qrcode';
import {useT} from '../i18n';
import {useAuth} from '../AuthGate';
import {paymentAmount, paymentSubject, paymentExpired, paymentStatus, paymentFailure, paymentAuthError, recoveryOrderNumber, watchPaymentOrder} from './payment-data';
import type {PaymentChannel, PaymentOrder, PaymentHistory, PaymentIntent} from './payment-types';
import './AccountView.css';

const call = async <T,>(action: string, body: unknown = {}): Promise<T> => await window.biodsh.payment(action, body) as T;

export default function AccountView({embedded = false}: {embedded?: boolean} = {}) {
  const auth = useAuth();
  // Remount all payment state when the signed-in account changes.
  const owner = `${auth.hub}:${auth.user?.id ?? auth.user?.username ?? ''}:${!!auth.localAccess}`;
  return <PaymentPanel key={owner} embedded={embedded} enabled={!!auth.user && !auth.localAccess}/>;
}

function PaymentPanel({embedded, enabled}: {embedded: boolean; enabled: boolean}) {
  const {t, lang} = useT();
  const [orders, setOrders] = useState<PaymentOrder[]>([]);
  const [unresolved, setUnresolved] = useState<PaymentIntent[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [channel, setChannel] = useState<PaymentChannel>('wechat');
  const [amount, setAmount] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState('');
  const [checking, setChecking] = useState(false);
  const [authExpired, setAuthExpired] = useState(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  const [recoveryNo, setRecoveryNo] = useState<string | null>(null);
  const [recoveryFields, setRecoveryFields] = useState<Record<string, string>>({});
  const [notCreatedConfirmed, setNotCreatedConfirmed] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState('');
  const alive = useRef(true);
  const authStopped = useRef(false);
  const mutation = useRef(false);
  const listPending = useRef(false);
  const queries = useRef(new Map<string, Promise<PaymentOrder>>());
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const mergeOrder = useCallback((order: PaymentOrder) => {
    setOrders(previous => [order, ...previous.filter(item => item.out_trade_no !== order.out_trade_no)]);
  }, []);
  const showError = useCallback((failure: unknown) => {
    if (!alive.current) return;
    setError(paymentFailure(failure));
    if (paymentAuthError(failure)) { authStopped.current = true; setAuthExpired(true); }
  }, []);
  const load = useCallback(async () => {
    if (!enabled || listPending.current || mutation.current) return;
    listPending.current = true; setLoading(true); setError('');
    try {
      await Promise.allSettled([...queries.current.values()]);
      if (!alive.current || authStopped.current) return;
      const history = await call<PaymentHistory>('list');
      if (!alive.current) return;
      setOrders(history.orders); setUnresolved(history.unresolved ?? []); setLoaded(true);
      setRecoveryNo(current => current && history.orders.some(order => order.out_trade_no === current) ? null : current);
      setSelectedId(current => history.orders.some(item => item.out_trade_no === current) ? current : (history.orders.find(item => item.status === 'WAIT_PAY') ?? history.orders[0])?.out_trade_no ?? '');
    } catch (failure) { showError(failure); }
    finally { listPending.current = false; if (alive.current) setLoading(false); }
  }, [enabled, showError]);
  useEffect(() => { if (enabled) void load(); else setLoading(false); }, [enabled, load]);

  const selected = orders.find(item => item.out_trade_no === selectedId);
  const queryOrder = useCallback((id: string): Promise<PaymentOrder> => {
    const existing = queries.current.get(id);
    if (existing) return existing;
    // Native payment operations share one lock, including queries for different orders.
    const previousQueries = [...queries.current.values()];
    const promise = Promise.allSettled(previousQueries).then(() => {
      if (!alive.current || authStopped.current || mutation.current || listPending.current || selectedRef.current !== id) throw Error('PAYMENT_QUERY_SUPERSEDED');
      return call<PaymentOrder>('query', {out_trade_no: id});
    }).then(order => {
      if (alive.current && !authStopped.current) { mergeOrder(order); if (selectedRef.current === id) { setError(''); setNow(Date.now()); } }
      return order;
    }).catch(failure => { if (!String(failure).includes('PAYMENT_QUERY_SUPERSEDED') && (selectedRef.current === id || paymentAuthError(failure))) showError(failure); throw failure; })
      .finally(() => { queries.current.delete(id); });
    queries.current.set(id, promise);
    return promise;
  }, [mergeOrder, showError]);

  useEffect(() => {
    if (!enabled || authExpired || loading || busy || !selected || selected.status !== 'WAIT_PAY' || paymentExpired(selected)) return;
    const orderId = selected.out_trade_no;
    const watcher = watchPaymentOrder({
      expiresAt: Date.parse(selected.expires_at), visible: () => document.visibilityState !== 'hidden',
      query: async () => {
        try { const order = await queryOrder(orderId); return order.status === 'WAIT_PAY' && !paymentExpired(order); }
        catch (failure) { if (paymentAuthError(failure)) return false; throw failure; }
      },
    });
    const onVisible = () => { if (document.visibilityState !== 'hidden') { setNow(Date.now()); watcher.refresh(); } };
    document.addEventListener?.('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => { watcher.stop(); document.removeEventListener?.('visibilitychange', onVisible); window.removeEventListener('focus', onVisible); };
  }, [enabled, authExpired, loading, busy, selected?.out_trade_no, selected?.status, selected?.expires_at, selected?.expired, queryOrder]);
  useEffect(() => {
    if (!selected || selected.status !== 'WAIT_PAY' || paymentExpired(selected)) return;
    const timer = setInterval(() => { if (document.visibilityState !== 'hidden') setNow(Date.now()); if (paymentExpired(selected)) clearInterval(timer); }, 1000);
    return () => clearInterval(timer);
  }, [selected?.out_trade_no, selected?.status, selected?.expires_at, selected?.expired]);

  const refreshSelected = async () => {
    if (!selected || authExpired || checking || loading || mutation.current) return;
    setChecking(true);
    try { await queryOrder(selected.out_trade_no); } catch { /* The shared query displays a safe error. */ }
    finally { if (alive.current) setChecking(false); }
  };
  const openCashier = async (id: string) => { await call('open', {out_trade_no: id}); };
  const createOrder = async () => {
    const normalized = paymentAmount(amount);
    if (mutation.current || checking || !enabled || authExpired || !loaded || loading || unresolved.length || recoveryNo || orders.some(item => item.status === 'WAIT_PAY' && !paymentExpired(item))) return;
    if (!normalized || !paymentSubject(subject)) { setError('请填写有效的金额和付款说明。'); return; }
    mutation.current = true; setBusy('create'); setError('');
    try {
      const queried = await Promise.allSettled([...queries.current.values()]);
      if (!alive.current || authStopped.current) return;
      if (queried.some(result => result.status === 'fulfilled' && result.value.status === 'PAID' && orders.some(order => order.out_trade_no === result.value.out_trade_no && order.status === 'WAIT_PAY'))) throw Error('PAYMENT_PENDING');
      const order = await call<PaymentOrder>('create', {channel, total_amount: normalized, subject: subject.trim(), ...(body.trim() ? {body: body.trim()} : {})});
      if (!alive.current) return;
      mergeOrder(order); setSelectedId(order.out_trade_no);
      if (order.channel === 'alipay') await openCashier(order.out_trade_no);
    } catch (failure) {
      showError(failure);
      if (alive.current && !paymentAuthError(failure)) {
        if (/PAYMENT_UNKNOWN|PAYMENT_RECOVERY/.test(String(failure))) { setRecoveryNo(recoveryOrderNumber(failure)); setLoaded(false); }
        // Reconcile native records after a failed create, including an old order found paid.
        try {
          const history = await call<PaymentHistory>('list');
          if (alive.current) {
            setOrders(history.orders); setUnresolved(history.unresolved ?? []);
            const recovered = history.orders.find(order => order.out_trade_no === recoveryOrderNumber(failure));
            if (recovered) { setRecoveryNo(null); setSelectedId(recovered.out_trade_no); setLoaded(true); setError(''); }
          }
        } catch { /* Keep the original error and any unknown outcome warning. */ }
      }
    } finally { mutation.current = false; if (alive.current) setBusy(''); }
  };
  const cancelOrder = async () => {
    if (!selected || selected.status !== 'WAIT_PAY' || mutation.current || authExpired) return;
    const id = selected.out_trade_no;
    mutation.current = true; setBusy('cancel'); setError('');
    try {
      await Promise.allSettled([...queries.current.values()]);
      if (!alive.current || authStopped.current) return;
      // A concurrent payment may win. Only render the server's returned status.
      const order = await call<PaymentOrder>('cancel', {out_trade_no: id});
      if (alive.current) mergeOrder(order);
    } catch (failure) { if (selectedRef.current === id) showError(failure); }
    finally { mutation.current = false; if (alive.current) setBusy(''); }
  };
  const reopen = async () => {
    if (!selected || mutation.current || authExpired) return;
    mutation.current = true; setBusy('open'); setError('');
    try { await Promise.allSettled([...queries.current.values()]); if (alive.current && !authStopped.current) await openCashier(selected.out_trade_no); } catch (failure) { showError(failure); }
    finally { mutation.current = false; if (alive.current) setBusy(''); }
  };
  const resolveOrder = async (intent: PaymentIntent) => {
    const out_trade_no = (recoveryFields[intent.id] || intent.out_trade_no || recoveryNo || '').trim();
    if (!out_trade_no || mutation.current || authExpired) return;
    mutation.current = true; setBusy('resolve'); setError('');
    try {
      await Promise.allSettled([...queries.current.values()]);
      if (!alive.current || authStopped.current) return;
      const order = await call<PaymentOrder>('resolve', {id: intent.id, out_trade_no});
      if (alive.current) {
        mergeOrder(order); setSelectedId(order.out_trade_no); setUnresolved(values => values.filter(item => item.id !== intent.id));
        setRecoveryNo(null); setLoaded(true);
      }
    } catch (failure) { showError(failure); }
    finally { mutation.current = false; if (alive.current) setBusy(''); }
  };
  const resolveNotCreated = async (intent: PaymentIntent) => {
    if (!notCreatedConfirmed[intent.id] || mutation.current || authExpired) return;
    mutation.current = true; setBusy('resolve'); setError('');
    try {
      await Promise.allSettled([...queries.current.values()]);
      if (!alive.current || authStopped.current) return;
      await call('resolve_not_created', {id: intent.id, confirmation: 'support_confirmed_no_order'});
      if (alive.current) { setUnresolved(values => values.filter(item => item.id !== intent.id)); setLoaded(true); setRecoveryNo(null); }
    } catch (failure) { showError(failure); }
    finally { mutation.current = false; if (alive.current) setBusy(''); }
  };
  const copyOrder = async (value: string) => {
    try { await navigator.clipboard.writeText(value); if (alive.current) setCopied(value); }
    catch { if (alive.current) setError('复制失败，请选中订单号复制。'); }
  };

  const normalizedAmount = paymentAmount(amount);
  const pending = orders.find(item => item.status === 'WAIT_PAY' && !paymentExpired(item, now));
  const expired = selected ? paymentExpired(selected, now) : false;
  const remaining = selected ? Math.max(0, Math.ceil((Date.parse(selected.expires_at) - now) / 60_000)) : 0;
  const disabled = !enabled || authExpired || !loaded || loading || checking || !!busy || unresolved.length > 0 || !!recoveryNo || !!pending;
  const date = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString(lang === 'en' ? 'en-US' : 'zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';

  return <div className={'payment-page' + (embedded ? ' is-embedded' : '')}>
    <header className="payment-heading"><div className="payment-eyebrow">OMaaX / {t('支付')}</div><h2>{t('付款与订单')}</h2><p>{t('填写付款信息，选择支付方式，在这里查看付款结果。')}</p></header>
    {!enabled && <p className="payment-notice" role="status">{t('请登录云端账号后使用支付功能。')}</p>}
    {error && <div className="payment-error" role="alert">{t(error)}{!loaded && !busy && <button className="btn btn-ghost" onClick={() => void load()} disabled={loading || authExpired}>{t('重新读取订单')}</button>}</div>}
    {recoveryNo && <div className="payment-notice"><strong>{t('请保留这笔订单号')}</strong><code>{recoveryNo}</code><button className="btn btn-ghost" onClick={() => void copyOrder(recoveryNo)}>{t(copied === recoveryNo ? '已复制' : '复制订单号')}</button></div>}
    {unresolved.map(intent => <section className="payment-recovery" key={intent.id}>
      <h3>{t('待核对的付款请求')}</h3><p>{t('订单是否创建尚未确认，请联系管理员核对，避免重复付款。')}</p>
      <div className="payment-recovery-meta"><strong>{intent.subject}</strong><span>¥{intent.total_amount}</span><span>{date(intent.created_at)}</span></div>
      <label>{t('管理员提供的商户订单号')}<input className="field" aria-label={t('管理员提供的商户订单号')} value={recoveryFields[intent.id] ?? intent.out_trade_no ?? recoveryNo ?? ''} placeholder="WX… / PAY…" onChange={event => setRecoveryFields(values => ({...values, [intent.id]: event.target.value}))} disabled={!!busy || authExpired}/></label>
      <button className="btn btn-tint" disabled={!!busy || authExpired || !(recoveryFields[intent.id] || intent.out_trade_no || recoveryNo)?.trim()} onClick={() => void resolveOrder(intent)}>{busy === 'resolve' ? t('正在核对…') : t('核对并找回订单')}</button>
      <details className="payment-recovery-confirm"><summary>{t('管理员确认没有生成订单')}</summary><p>{t('仅在管理员已核实没有生成订单时解除。未经核实可能造成重复付款。')}</p><label><input type="checkbox" checked={!!notCreatedConfirmed[intent.id]} disabled={!!busy || authExpired} onChange={event => setNotCreatedConfirmed(values => ({...values, [intent.id]: event.target.checked}))}/>{t('管理员已核实未生成订单')}</label><button type="button" className="btn btn-ghost" disabled={!!busy || authExpired || !notCreatedConfirmed[intent.id]} onClick={() => void resolveNotCreated(intent)}>{t('解除待核对状态')}</button></details>
    </section>)}
    <div className="payment-columns">
      <form className="payment-form payment-card" onSubmit={event => {event.preventDefault(); void createOrder();}}>
        <div className="payment-section-heading"><span className="payment-step">1</span><h3>{t('付款信息')}</h3></div>
        <label htmlFor="payment-amount">{t('付款金额')}<div className="payment-amount-input"><span aria-hidden="true">¥</span><input id="payment-amount" inputMode="decimal" autoComplete="off" placeholder="0.00" value={amount} maxLength={32} onChange={event => setAmount(event.target.value)} disabled={disabled} aria-invalid={!!amount.trim() && !normalizedAmount} aria-describedby="payment-amount-help"/><span className="payment-unit">CNY</span></div><small id="payment-amount-help">{t(amount.trim() && !normalizedAmount ? '请输入 0.01 至 100000000 元，最多保留两位小数。' : '金额单位为元，请按已确认的付款要求填写。')}</small></label>
        <label htmlFor="payment-subject">{t('付款说明')}<input id="payment-subject" className="field" value={subject} placeholder={t('填写此次付款的用途')} onChange={event => setSubject(event.target.value)} disabled={disabled} aria-invalid={!!subject.trim() && !paymentSubject(subject)}/>{!!subject.trim() && !paymentSubject(subject) && <small>{t('付款说明不能包含 /、= 或 &。')}</small>}</label>
        <label htmlFor="payment-body">{t('备注（可选）')}<textarea id="payment-body" className="field" rows={2} value={body} placeholder={t('补充对账所需的信息')} onChange={event => setBody(event.target.value)} disabled={disabled}/></label>
        <div className="payment-section-heading payment-method-heading"><span className="payment-step">2</span><h3>{t('选择支付方式')}</h3></div>
        <div className="payment-methods" role="group" aria-label={t('支付方式')}>
          {(['wechat','alipay'] as const).map(value => <button type="button" aria-pressed={channel === value} key={value} className={'payment-method ' + value + (channel === value ? ' is-selected' : '')} onClick={() => setChannel(value)} disabled={disabled}><span className="payment-channel-dot" aria-hidden="true"/><span><strong>{t(value === 'wechat' ? '微信支付' : '支付宝')}</strong><small>{t(value === 'wechat' ? '使用微信扫码付款' : '在浏览器中完成付款')}</small></span>{channel === value && <Check size={17}/>}</button>)}
        </div>
        <button className="btn btn-primary payment-create" type="submit" disabled={disabled || !normalizedAmount || !paymentSubject(subject)}>{busy === 'create' ? <><Loader2 size={17} className="spin"/>{t('正在创建订单…')}</> : t('创建支付订单 ¥{amount}', {amount: normalizedAmount ?? '0.00'})}</button>
        {pending && <p className="payment-form-tip">{t('还有待支付订单，请先查看付款结果或关闭旧订单。')}<button type="button" className="btn btn-ghost" onClick={() => setSelectedId(pending.out_trade_no)}>{t('查看待付订单')}</button></p>}
      </form>
      <section className="payment-card payment-cashier" aria-label={t('付款结果')}>
        <div className="payment-section-heading"><span className="payment-step">3</span><h3>{t('付款结果')}</h3>{selected && <span className={'payment-status status-' + selected.status.toLowerCase()}>{t(paymentStatus(selected, now))}</span>}</div>
        {loading && !loaded ? <div className="payment-empty"><Loader2 size={30} className="spin"/><p>{t('正在读取订单…')}</p></div> : !selected ? <div className="payment-empty"><span className="payment-empty-icon"><CreditCard size={31}/></span><h4>{t('准备好后，在左侧创建订单')}</h4><p>{t('微信付款码或支付宝付款入口会显示在这里。')}</p></div> : <>
          <div className="payment-due"><small>{t(selected.status === 'PAID' ? '已付款金额' : '订单金额')}</small><strong><span>¥</span>{selected.total_amount}</strong><p>{selected.subject}</p></div>
          {selected.status === 'PAID' ? <div className="payment-result success" role="status"><CheckCircle2 size={42}/><h4>{t('支付成功')}</h4><p>{t('付款结果已确认。')}</p></div> : selected.status === 'CLOSED' ? <div className="payment-result closed" role="status"><XCircle size={40}/><h4>{t('订单已关闭')}</h4><p>{t('如需退款详情，请联系管理员核对。')}</p></div> : expired ? <div className="payment-result expired" role="status"><History size={38}/><h4>{t('付款入口已过期')}</h4><p>{t('原订单仍可查询。若已付款，请先核对结果，避免重复付款。')}</p></div> : selected.channel === 'wechat' ? <WechatCode key={selected.out_trade_no} order={selected}/> : <div className="payment-alipay-entry"><span className="payment-alipay-symbol">A</span><h4>{t('前往支付宝完成付款')}</h4><p>{t('付款后回到此页，系统会自动查询结果。')}</p><button type="button" className="btn btn-primary" disabled={!!busy || authExpired} onClick={() => void reopen()}><ArrowUpRight size={16}/>{t(busy === 'open' ? '正在打开…' : '打开支付宝收银台')}</button></div>}
          {selected.status === 'WAIT_PAY' && !expired && <p className="payment-countdown">{t('付款入口约 {minutes} 分钟后过期', {minutes: remaining})}</p>}
          <dl className="payment-order-details"><div><dt>{t('支付方式')}</dt><dd>{t(selected.channel === 'wechat' ? '微信支付' : '支付宝')}</dd></div><div><dt>{t('商户订单号')}</dt><dd><code>{selected.out_trade_no}</code><button type="button" className="payment-copy" aria-label={t('复制订单号')} title={t(copied === selected.out_trade_no ? '已复制' : '复制订单号')} onClick={() => void copyOrder(selected.out_trade_no)}>{copied === selected.out_trade_no ? <Check size={14}/> : <Copy size={14}/>}</button></dd></div><div><dt>{t('创建时间')}</dt><dd>{date(selected.created_at)}</dd></div>{selected.paid_at && <div><dt>{t('付款时间')}</dt><dd>{date(selected.paid_at)}</dd></div>}{selected.trade_no && <div><dt>{t('交易流水号')}</dt><dd><code>{selected.trade_no}</code></dd></div>}{selected.refunded_amount && paymentAmount(selected.refunded_amount) && <div><dt>{t('累计退款金额')}</dt><dd>¥{selected.refunded_amount}</dd></div>}</dl>
          <div className="payment-order-actions"><button type="button" className="btn btn-tint" disabled={checking || !!busy || authExpired} onClick={() => void refreshSelected()}>{checking ? <Loader2 size={15} className="spin"/> : <RefreshCw size={15}/>} {t(checking ? '正在查询…' : '查询付款结果')}</button>{selected.status === 'WAIT_PAY' && <button type="button" className="btn btn-ghost" disabled={!!busy || checking || authExpired} onClick={() => void cancelOrder()}>{t(busy === 'cancel' ? '正在关闭…' : '关闭订单')}</button>}</div>
        </>}
        <p className="payment-assurance"><ShieldCheck size={15}/>{t('付款结果以支付服务确认的信息为准。')}</p>
      </section>
    </div>
    <section className="payment-card payment-history"><div className="payment-history-heading"><div><h3>{t('本机订单记录')}</h3><p>{t('仅显示当前账号在这台电脑保存的订单。')}</p></div><button type="button" className="btn btn-ghost" disabled={loading || !!busy || !enabled || authExpired} onClick={() => void load()}><RefreshCw size={15} className={loading ? 'spin' : ''}/>{t('刷新订单')}</button></div>
      {!orders.length ? <p className="payment-history-empty">{t('暂无订单记录')}</p> : <div className="payment-history-list">{orders.map(order => <button type="button" className={'payment-history-row' + (selectedId === order.out_trade_no ? ' is-selected' : '')} key={order.out_trade_no} onClick={() => { setSelectedId(order.out_trade_no); setError(''); }} disabled={!!busy} aria-label={t('查看订单 {id}', {id: order.out_trade_no})} aria-pressed={selectedId === order.out_trade_no}><span className={'payment-history-channel ' + order.channel}><CreditCard size={18}/></span><span className="payment-history-title"><strong>{order.subject}</strong><small>{order.out_trade_no} · {date(order.created_at)}</small></span><strong className="payment-history-amount">¥{order.total_amount}</strong><span className={'payment-status status-' + order.status.toLowerCase()}>{t(paymentStatus(order, now))}</span></button>)}</div>}
    </section>
  </div>;
}

function WechatCode({order}: {order: PaymentOrder}) {
  const {t} = useT();
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true; setUrl(''); setFailed(false);
    if (!order.code_url || !/^weixin:\/\/wxpay\/bizpayurl\?/.test(order.code_url)) { setFailed(true); return; }
    void QRCode.toDataURL(order.code_url, {width: 256, margin: 3, errorCorrectionLevel: 'M', color: {dark:'#111827',light:'#ffffff'}})
      .then(value => { if (active) setUrl(value); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [order.out_trade_no, order.code_url, retry]);
  if (!order.code_url) return <div className="payment-qr-failure"><QrCode size={38}/><p>{t('这笔订单的付款入口无法恢复，请查询结果或关闭后重新创建。')}</p></div>;
  return <div className="payment-wechat-entry">{failed ? <div className="payment-qr-failure"><QrCode size={38}/><p>{t('付款码暂时无法显示，订单已保留。')}</p><button className="btn btn-tint" type="button" onClick={() => setRetry(value => value + 1)}>{t('重新显示付款码')}</button></div> : url ? <img src={url} alt={t('微信付款二维码')} width={216} height={216}/> : <div className="payment-qr-loading"><Loader2 size={28} className="spin"/></div>}<p><span className="payment-channel-dot"/>{t('打开微信，扫一扫完成付款')}</p></div>;
}
