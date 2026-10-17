export type PlanId = 'basic' | 'pro' | 'flagship';

export interface AccountPlan {
  id: PlanId;
  name: string;
  tagline: string;
  priceCny: number;
  quotaLabel: string;
  features: string[];
  recommended?: boolean;
}

export interface AccountUsage {
  usedTokens: number;
  quotaTokens: number;
  callCount: number;
  costCny: number;
  resetAt: string;
}

export interface TokenRecord {
  id: string;
  date: string;
  title: string;
  delta: number;
}

export interface AccountOverview {
  planId: PlanId;
  usage: AccountUsage;
  plans: AccountPlan[];
  records: TokenRecord[];
}

const PLANS: AccountPlan[] = [
  {
    id: 'basic',
    name: '基础版',
    tagline: '适合轻度使用，基础额度',
    priceCny: 39,
    quotaLabel: '每月 200 万 tokens',
    features: ['每月 200 万 token 额度', '基础模型（DeepSeek-V3）', '标准排队', '邮件支持'],
  },
  {
    id: 'pro',
    name: '专业版',
    tagline: '适合日常研发，推荐套餐',
    priceCny: 99,
    quotaLabel: '每月 800 万 tokens',
    features: ['每月 800 万 token 额度', '全部模型（含推理模型）', '优先进队', '工作日技术支持'],
    recommended: true,
  },
  {
    id: 'flagship',
    name: '旗舰版',
    tagline: '高并发重度使用，最高额度',
    priceCny: 199,
    quotaLabel: '每月 3000 万 tokens',
    features: ['每月 3000 万 token 额度', '全部模型（含推理模型）', '最高优先级算力保障', '专属客户经理'],
  },
];

const OVERVIEW: AccountOverview = {
  planId: 'pro',
  usage: { usedTokens: 3248530, quotaTokens: 5000000, callCount: 186, costCny: 12.6, resetAt: '2026-11-01' },
  plans: PLANS,
  records: [
    { id: 'r1', date: '2026-10-15', title: '对话消耗 · 单细胞分析', delta: -412300 },
    { id: 'r2', date: '2026-10-12', title: '对话消耗 · 文献调研', delta: -156800 },
    { id: 'r3', date: '2026-10-08', title: '充值 · 500 万 token', delta: 5000000 },
    { id: 'r4', date: '2026-10-01', title: '新用户赠送', delta: 500000 },
  ],
};

export async function loadAccountOverview(): Promise<AccountOverview> {
  await new Promise((resolve) => setTimeout(resolve, 350));
  return OVERVIEW;
}
