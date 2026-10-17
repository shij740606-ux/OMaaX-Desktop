# 账户与用量（Billing）交接文档

> 状态：纯前端 UI + mock 数据（无真实计费/支付后端）。适用于 Tauri 桌面打包。
> 最后更新：2026-10-17

## 1. 功能范围

新增「账户与用量」页面，包含：

- 顶部：本月 token 用量进度条（按用量变色：<70% 绿 / 70-90% 橙 / ≥90% 红）、调用次数、消耗金额、剩余额度、重置日期
- 中部：三档套餐卡片 —— 基础版 ¥39/月、专业版 ¥99/月（推荐）、旗舰版 ¥199/月（默认当前套餐为专业版）
- 底部：微信支付 / 支付宝支付按钮（当前为 mock 下单 + 二维码占位）
- 末部：积分（Token）记录列表

## 2. 涉及文件

### 新增
| 文件 | 说明 |
| --- | --- |
| `desktop/src/renderer/src/views/AccountView.tsx` | 页面组件（UI 全部复用现有设计系统 `card`/`btn`/`badge`/`t-*`） |
| `desktop/src/renderer/src/views/accountMock.ts` | 类型定义 + mock 数据 + `loadAccountOverview()`，**接后端时唯一需要替换的数据入口** |
| `desktop/src/renderer/account-preview.html` | 浏览器独立预览入口（不加载 App/store，不依赖 `window.biodsh`） |
| `desktop/src/renderer/src/account-preview.tsx` | 预览入口脚本（仅渲染 `<AccountView />` + styles.css，跟随系统深浅色） |
| `desktop/vite.web.config.ts` | 独立 Vite 配置（`npm run dev:web` 用；dev 下 `/` 会重定向到预览页，避免误加载真实 `index.html`） |

### 修改
| 文件 | 说明 |
| --- | --- |
| `desktop/src/renderer/src/store.tsx` | `Tab` 类型新增 `'account'` |
| `desktop/src/renderer/src/App.tsx` | `tab === 'account'` 时渲染 `AccountView` |
| `desktop/src/renderer/src/components/Sidebar.tsx` | 新增侧边栏入口「账户与用量」（CreditCard 图标） |
| `desktop/src/renderer/src/i18n.en.ts` | 新增英文词条（源码中文为 key） |
| `desktop/package.json` | 新增 `dev:web` 脚本 |
| `.gitignore` | 忽略 `desktop/dist-web/` |

## 3. 数据模型（`accountMock.ts`）

```ts
export type PlanId = 'basic' | 'pro' | 'flagship';

interface AccountPlan {
  id: PlanId;
  name: string;        // 中文文案，渲染时经 t() 查询英文
  tagline: string;
  priceCny: number;    // 元/月
  quotaLabel: string;
  features: string[];
  recommended?: boolean;
}

interface AccountUsage {
  usedTokens: number;
  quotaTokens: number;
  callCount: number;
  costCny: number;
  resetAt: string;     // 如 '2026-11-01'
}

interface TokenRecord {
  id: string;
  date: string;
  title: string;       // 中文文案，渲染时经 t()
  delta: number;       // 正数为增加，负数为消耗
}

interface AccountOverview {
  planId: PlanId;      // 当前套餐
  usage: AccountUsage;
  plans: AccountPlan[];
  records: TokenRecord[];
}
```

## 4. 需要接入的真实接口

建议沿用现有 `window.biodsh` IPC 桥暴露以下方法（命名可调整，但请保持两套运行时一致）：

| 方法 | 签名（建议） | 说明 |
| --- | --- | --- |
| `accountOverview()` | `() => Promise<AccountOverview>` | 拉取当前用户套餐 + 本月用量 + 套餐列表 + 近期记录。**替换 `loadAccountOverview()` 的调用点** |
| `createPaymentOrder(input)` | `(input: { planId: PlanId; channel: 'wechat' \| 'alipay'; period: 'monthly' }) => Promise<{ orderId: string; amountCny: number; qrCodeUrl: string; expiresAt: string }>` | 创建支付订单，返回二维码内容/链接 |
| `checkPaymentStatus(orderId)` | `(orderId: string) => Promise<'pending' \| 'paid' \| 'expired' \| 'failed'>` | 轮询支付状态；`paid` 后刷新 `accountOverview()` |
| `cancelPaymentOrder(orderId)` | `(orderId: string) => Promise<void>` | 取消未支付订单（用户在 mock 二维码面板点「取消支付」时调用） |
| `tokenRecords(page?)` | `(page?: number) => Promise<TokenRecord[]>` | 可选：记录列表分页（当前随 overview 一次性返回） |

### 新增一个 IPC 方法需要改 4 处

1. `desktop/src/renderer/src/tauri-bridge.ts` —— 加 `invoke('account_overview', ...)` 等方法（Tauri 是实际运行时要用的）
2. `desktop/src-tauri/src/lib.rs` —— 写 `#[tauri::command]` 并注册进 `generate_handler![]`（约 1153 行处）
3. `desktop/src/preload/index.ts` —— Electron 兜底版本（`ipcRenderer.invoke('account:overview')`）
4. `desktop/src/main/index.ts` —— `registerIpc()` 中加 `ipcMain.handle('account:overview', ...)`

共用类型建议放 `desktop/src/shared/types.ts`（renderer 与 main 均可引用）。

### 前端接入点

`AccountView.tsx` 只依赖 `loadAccountOverview()`：

- 把 `accountMock.ts` 中的实现换成 `window.biodsh.accountOverview()`（或写一层适配把云端字段映射为 `AccountOverview`），页面无需改动；
- 支付部分当前在 `placeOrder()` 内 `setTimeout` 模拟下单，接入时改为调用 `createPaymentOrder()`，并用返回的 `qrCodeUrl` 渲染真实二维码，再轮询 `checkPaymentStatus()`。

## 5. 后端接入注意事项

1. **金额以服务端为准**：前端不传金额，只传 `planId`/`channel`/`period`，服务端按套餐定价计算。
2. **支付轮询**：建议 2 秒间隔、5 分钟超时（对齐二维码有效期）；超时或 `expired` 后提示重新下单；组件卸载时要清理定时器。
3. **幂等与唯一性**：订单号全局唯一；状态查询与支付回调需幂等；同一用户短时间内重复点击支付应复用未过期订单或返回明确提示。
4. **敏感信息**：不要在前端日志/错误上报中打印密钥、二维码原始链接、订单 token。
5. **离线模式**：`settings.mode === 'offline'`（设置页可切换）时不应发起任何联网请求，账户页应禁用支付入口并展示本地提示（可结合 `Sidebar.tsx` 的 `offline` 判断方式）。
6. **用户体系**：当前应用已有 `platformSession` / `platformLogin`（平台账号体系，见 `store.tsx`、`src/main/platform-auth.ts`）。账户/计费是与平台账号绑定还是独立账号体系，需产品确认；若复用，需在未登录时引导登录。
7. **结算与扣减**：token 扣减、套餐生效、账单结算全部在服务端完成，前端仅展示；套餐升级/续费生效时间需定义清楚（建议立即生效 + 差额处理规则）。
8. **微信/支付宝**：需要企业商户资质与服务端签名，二维码由服务端调起统一下单接口生成（Native 支付），桌面端仅展示与轮询。
9. **文案与本地化**：mock 阶段的服务端文案（套餐名、tagline、features、记录标题）是中文并走 `t()` 渲染；接云端后建议服务端返回**结构化字段/枚举 + 参数**，由前端本地化，避免服务端直接返回中文句子。
10. **套餐列表**：`plans` 目前随 overview 返回。若运营需要频繁调整，可拆为独立接口 `accountPlans()` 并按需缓存。

## 6. 开发与验证

```bash
cd desktop
npm run typecheck            # 类型检查
npm run dev:web              # 浏览器预览 AccountView（无需 Tauri/Rust；自动打开 http://localhost:5180/）
npm run build                # Tauri 生产构建（需 Rust 工具链）
```

- `dev:web` 下访问 `/` 或 `/account-preview.html` 均进入隔离预览页；该配置不会影响 `npm run dev`（Tauri 开发，端口 5173）与 Electron 构建。
- 页面 UI 需同时检查浅色/深色（预览页跟随系统主题；桌面端跟随「设置 → 外观」）。
