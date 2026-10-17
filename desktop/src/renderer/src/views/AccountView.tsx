import { useCallback, useEffect, useState } from 'react';
import { Check, CreditCard, Crown, Loader2, QrCode, RefreshCw, Rocket, Wallet, Zap } from 'lucide-react';
import { useT } from '../i18n';
import { loadAccountOverview, type AccountOverview, type PlanId } from './accountMock';

const fmtTokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n));
const fmtMoney = (n: number) => `¥${n.toFixed(2)}`;
const PAY_COLORS = { wechat: '#07C160', alipay: '#1677FF' } as const;
const PLAN_ICONS: Record<PlanId, typeof Zap> = { basic: Zap, pro: Rocket, flagship: Crown };

type Channel = keyof typeof PAY_COLORS;

export default function AccountView() {
  const { t } = useT();
  const [data, setData] = useState<AccountOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<PlanId | null>(null);
  const [paying, setPaying] = useState<Channel | null>(null);
  const [order, setOrder] = useState<{ channel: Channel; id: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try { setData(await loadAccountOverview()); setOrder(null); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const placeOrder = (channel: Channel) => {
    setPaying(channel);
    window.setTimeout(() => {
      setPaying(null);
      setOrder({ channel, id: `MO${Date.now().toString().slice(-10)}` });
    }, 600);
  };

  const header = (
    <header className="drag h-[52px] flex items-center justify-between pl-5 hairline-b" style={{ background: 'var(--bg)' }}>
      <span className="t-title2">{t('账户与用量')}</span>
    </header>
  );

  if (!data) {
    return (
      <div className="h-full flex flex-col">
        {header}
        <div className="flex-1 flex items-center justify-center">
          {error !== null ? (
            <div className="flex flex-col items-center gap-3">
              <div className="t-body">{t('加载失败：{msg}', { msg: error.slice(0, 80) })}</div>
              <button className="btn btn-primary" onClick={() => void load()}>{t('重试')}</button>
            </div>
          ) : (
            <Loader2 size={22} className="spin" style={{ color: 'var(--accent)' }} />
          )}
        </div>
      </div>
    );
  }

  const current = data.plans.find((p) => p.id === data.planId) ?? null;
  const selected = data.plans.find((p) => p.id === (selectedId ?? data.planId)) ?? null;
  const percent = Math.min(100, (data.usage.usedTokens / data.usage.quotaTokens) * 100);
  const barColor = percent >= 90 ? 'var(--red)' : percent >= 70 ? 'var(--orange)' : 'var(--green)';
  const rest = Math.max(0, data.usage.quotaTokens - data.usage.usedTokens);

  return (
    <div className="h-full flex flex-col">
      {header}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-[840px] mx-auto px-8 py-8 flex flex-col gap-6">
          <div role="status" className="card p-4 t-body">{t('此页为套餐与用量演示，数据不代表实际账单，支付功能暂未开放。')}</div>
          <section className="card p-5 rise">
            <div className="flex items-center justify-between gap-2 mb-4">
              <div className="flex items-center gap-2">
                <span className="t-headline">{t('本月用量')}</span>
                {current && <span className="badge badge-blue">{t(current.name)}</span>}
              </div>
              <button className="btn btn-ghost !px-2" title={t('刷新')} onClick={() => void load()}>
                {loading ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
              </button>
            </div>
            <div className="flex items-baseline justify-between mb-2">
              <div className="flex items-baseline gap-1">
                <span className="t-title2 t-mono">{fmtTokens(data.usage.usedTokens)}</span>
                <span className="t-caption">/ {fmtTokens(data.usage.quotaTokens)} tokens</span>
              </div>
              <span className="t-caption" style={{ color: barColor }}>{t('已用 {percent}', { percent: `${percent.toFixed(1)}%` })}</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--fill-2)' }}>
              <div className="h-full rounded-full" style={{ width: `${percent}%`, background: barColor, transition: 'width .3s ease' }} />
            </div>
            <div className="flex items-center justify-between mt-2.5">
              <span className="t-caption">{t('额度重置：{date}', { date: data.usage.resetAt })}</span>
              <span className="t-caption">{t('剩余 {rest} tokens', { rest: fmtTokens(rest) })}</span>
            </div>
            <div className="grid gap-3 mt-4" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
              {[
                { label: t('本月调用次数'), value: String(data.usage.callCount) },
                { label: t('本月消耗'), value: fmtMoney(data.usage.costCny) },
                { label: t('剩余额度'), value: fmtTokens(rest) },
              ].map((s) => (
                <div key={s.label} className="rounded-xl px-3 py-2.5" style={{ background: 'var(--surface-2)' }}>
                  <div className="t-caption">{s.label}</div>
                  <div className="t-headline t-mono mt-0.5">{s.value}</div>
                </div>
              ))}
            </div>
          </section>

          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
            {data.plans.map((p) => {
              const Icon = PLAN_ICONS[p.id];
              const isCurrent = p.id === data.planId;
              const isSelected = p.id === (selectedId ?? data.planId);
              return (
                <section key={p.id} className="card p-5 rise flex flex-col gap-3" style={{ border: `1.5px solid ${isSelected ? 'var(--accent)' : 'transparent'}` }}>
                  <div className="flex items-center gap-2">
                    <Icon size={16} style={{ color: 'var(--accent)' }} />
                    <span className="t-headline">{t(p.name)}</span>
                    {isCurrent
                      ? <span className="badge badge-green ml-auto">{t('当前套餐')}</span>
                      : p.recommended ? <span className="badge badge-blue ml-auto">{t('推荐')}</span> : null}
                  </div>
                  <div className="t-caption" style={{ minHeight: 28 }}>{t(p.tagline)}</div>
                  <div className="flex items-baseline gap-1">
                    <span className="t-title1 t-mono">¥{p.priceCny}</span>
                    <span className="t-caption">{t('/月')}</span>
                  </div>
                  <div className="hairline-t pt-3 flex flex-col gap-1.5">
                    {p.features.map((f) => (
                      <div key={f} className="flex items-center gap-1.5">
                        <Check size={13} style={{ color: 'var(--green)' }} />
                        <span className="t-body">{t(f)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-auto pt-1">
                    {isCurrent ? (
                      <button className="btn btn-fill w-full" disabled>{t('当前套餐')}</button>
                    ) : (
                      <button
                        className={isSelected ? 'btn btn-tint w-full' : 'btn btn-primary w-full'}
                        onClick={() => { setSelectedId(p.id); setOrder(null); }}
                      >
                        {isSelected ? t('已选择') : t('选择 {name}', { name: t(p.name) })}
                      </button>
                    )}
                  </div>
                </section>
              );
            })}
          </div>

          {selected && (
            <section className="card p-5 rise">
              <div className="t-headline mb-3">{t('升级或续费')}</div>
              <div className="flex items-center justify-between rounded-xl px-4 py-3" style={{ background: 'var(--surface-2)' }}>
                <div>
                  <div className="t-body" style={{ fontWeight: 600 }}>{t(selected.name)}{selected.id === data.planId ? ` · ${t('续费')}` : ` · ${t('升级')}`}</div>
                  <div className="t-caption">{t(selected.quotaLabel)}</div>
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="t-title2 t-mono">¥{selected.priceCny}</span>
                  <span className="t-caption">{t('/月')}</span>
                </div>
              </div>
              <div className="t-caption mt-3 mb-2">{t('支付方式')}</div>
              <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
                <button
                  className="btn btn-lg w-full"
                  style={{ background: PAY_COLORS.wechat, color: '#fff' }}
                  disabled={paying !== null}
                  onClick={() => placeOrder('wechat')}
                >
                  {paying === 'wechat' ? <Loader2 size={15} className="spin" /> : <Wallet size={15} />}
                  {paying === 'wechat' ? t('正在创建订单…') : t('微信支付')}
                </button>
                <button
                  className="btn btn-lg w-full"
                  style={{ background: PAY_COLORS.alipay, color: '#fff' }}
                  disabled={paying !== null}
                  onClick={() => placeOrder('alipay')}
                >
                  {paying === 'alipay' ? <Loader2 size={15} className="spin" /> : <CreditCard size={15} />}
                  {paying === 'alipay' ? t('正在创建订单…') : t('支付宝')}
                </button>
              </div>
              {order && (
                <div className="mt-4 rounded-xl p-4 flex flex-col items-center gap-1.5 rise" style={{ border: '1px dashed var(--border)' }}>
                  <QrCode size={72} strokeWidth={1.2} style={{ color: 'var(--text-2)' }} />
                  <div className="t-body" style={{ fontWeight: 600 }}>{t(order.channel === 'wechat' ? '微信支付' : '支付宝')} · {fmtMoney(selected.priceCny)}</div>
                  <div className="t-caption">{t('订单号 {id}', { id: order.id })}</div>
                  <div className="t-caption text-center" style={{ color: 'var(--text-3)' }}>{t('支付服务接入云端后，这里会显示收款二维码。当前为演示数据。')}</div>
                  <button className="btn btn-ghost mt-1" onClick={() => setOrder(null)}>{t('取消支付')}</button>
                </div>
              )}
            </section>
          )}

          <section className="card p-5 rise">
            <div className="t-headline mb-2">{t('积分（Token）记录')}</div>
            {data.records.length === 0 ? (
              <div className="t-caption py-3">{t('暂无记录')}</div>
            ) : data.records.map((r, i) => (
              <div key={r.id} className={`flex items-center gap-3 py-2.5 ${i > 0 ? 'hairline-t' : ''}`}>
                <span className="t-caption t-mono" style={{ color: 'var(--text-3)' }}>{r.date}</span>
                <span className="t-body flex-1 truncate">{t(r.title)}</span>
                <span className="t-mono" style={{ color: r.delta >= 0 ? 'var(--green)' : 'var(--text-2)' }}>
                  {r.delta >= 0 ? '+' : '−'}{fmtTokens(Math.abs(r.delta))}
                </span>
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
