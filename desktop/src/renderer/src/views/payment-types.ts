export type PaymentChannel = 'wechat' | 'alipay';
export interface PaymentOrder {
  out_trade_no: string;
  channel: PaymentChannel;
  total_amount: string;
  subject: string;
  body?: string;
  status: 'WAIT_PAY' | 'PAID' | 'CLOSED';
  pay_url?: string;
  code_url?: string;
  created_at: string;
  expires_at: string;
  trade_no?: string | null;
  paid_at?: string | null;
  refunded_amount?: string | null;
  expired: boolean;
  last_checked_at?: string;
}
export interface PaymentIntent {
  id: string;
  channel: PaymentChannel;
  total_amount: string;
  subject: string;
  created_at: string;
  reason: string;
  out_trade_no?: string;
}
export interface PaymentHistory { orders: PaymentOrder[]; unresolved?: PaymentIntent[] }
