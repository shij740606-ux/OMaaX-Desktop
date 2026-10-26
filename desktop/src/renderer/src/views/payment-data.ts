import type {PaymentOrder} from './payment-types';

// Keep money as decimal text all the way to the payment service.
export function paymentAmount(value: string): string | null {
  const raw = value.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(raw) || raw.length > 32) return null;
  const [whole, fraction = ''] = raw.split('.');
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  if (cents < 1n || cents > 10_000_000_000n) return null;
  return `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`;
}
export function paymentSubject(value: string): boolean { return !!value.trim() && !/[\/=&]/.test(value); }
export function paymentExpired(order: PaymentOrder, now = Date.now()): boolean {
  return order.expired || !Number.isFinite(Date.parse(order.expires_at)) || now >= Date.parse(order.expires_at);
}
export function paymentStatus(order: PaymentOrder, now = Date.now()): string {
  if (order.status === 'PAID') return '支付成功';
  if (order.status === 'CLOSED') return '已关闭';
  return paymentExpired(order, now) ? '付款入口已过期' : '待支付';
}
export function paymentAuthError(error: unknown): boolean { return /UNAUTHORIZED|FORBIDDEN|PAYMENT_ACCOUNT_CHANGED/.test(String(error)); }
export function paymentFailure(error: unknown): string {
  const message = String(error);
  if (paymentAuthError(error)) return '登录状态已变化，请重新登录后查看支付结果。';
  if (/PAYMENT_RECOVERY_CONFLICT/.test(message)) return '这个订单号已关联其他付款请求，请让管理员核对本次请求对应的订单号。';
  if (/PAYMENT_CONFIRMATION/.test(message)) return '请先联系管理员确认没有生成订单，再解除待核对状态。';
  if (/PAYMENT_UNKNOWN|PAYMENT_RECOVERY/.test(message)) return '订单是否创建尚未确认，请联系管理员核对，避免重复付款。';
  if (/PAYMENT_INVALID/.test(message)) return '请检查金额和付款说明后再创建订单。';
  if (/PAYMENT_BUSY/.test(message)) return '正在处理上一项支付操作，请稍后再试。';
  if (/PAYMENT_PENDING/.test(message)) return '请先核对上一笔订单的付款结果，避免重复付款。';
  if (/PAYMENT_CORRUPT/.test(message)) return '本机支付记录暂时无法读取，请联系管理员核对，避免重复付款。';
  if (/PAYMENT_STORAGE/.test(message)) return '支付记录无法保存，请检查本机可用空间后重试。';
  if (/PAYMENT_PROTOCOL/.test(message)) return '订单信息暂时无法核对，请联系管理员确认，避免重复付款。';
  if (/PAYMENT_CREDENTIAL/.test(message)) return '付款入口暂时无法打开，订单已保留，请重试或联系管理员。';
  if (/PAYMENT_EXPIRED/.test(message)) return '付款入口已过期，请先查询旧订单的付款结果。';
  if (/PAYMENT_CLOSED/.test(message)) return '这笔订单已处理，请重新查询付款结果。';
  if (/PAYMENT_NOT_FOUND/.test(message)) return '未找到这笔订单，请刷新记录或联系管理员核对。';
  if (/PAYMENT_GATEWAY/.test(message)) return '支付服务未接受这笔订单，请检查付款信息或联系管理员。';
  return '暂时无法连接支付服务，订单记录已保留，请稍后查询。';
}
export function recoveryOrderNumber(error: unknown): string | null {
  const message = String(error);
  if (!message.includes('PAYMENT_RECOVERY')) return null;
  try {
    const data = JSON.parse(message.slice(message.indexOf('{')));
    return typeof data.out_trade_no === 'string' && /^(WX|PAY)[A-Za-z0-9_-]{1,80}$/.test(data.out_trade_no) ? data.out_trade_no : null;
  } catch { return null; }
}

/** One request at a time; hidden pages and expired payment entries never keep polling. */
export function watchPaymentOrder(options: {
  query: () => Promise<boolean>;
  expiresAt: number;
  visible: () => boolean;
  now?: () => number;
  schedule?: (callback: () => void, delay: number) => number;
  clear?: (timer: number) => void;
}) {
  const now = options.now ?? Date.now;
  const schedule = options.schedule ?? ((fn, delay) => window.setTimeout(fn, delay));
  const clear = options.clear ?? (timer => window.clearTimeout(timer));
  let stopped = false, running = false, failures = 0, timer: number | undefined;
  const unschedule = () => { if (timer !== undefined) { clear(timer); timer = undefined; } };
  const tick = async () => {
    unschedule();
    if (stopped || running || !options.visible() || !Number.isFinite(options.expiresAt) || now() >= options.expiresAt) return;
    running = true;
    let again = true;
    try { again = await options.query(); failures = 0; } catch { failures++; }
    finally {
      running = false;
      if (!again) stopped = true;
      if (!stopped && options.visible() && now() < options.expiresAt) timer = schedule(() => void tick(), Math.min(30_000, 3_000 * 2 ** Math.min(failures, 4)));
    }
  };
  void tick();
  return {refresh: () => void tick(), stop: () => { stopped = true; unschedule(); }};
}
