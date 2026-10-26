//! Native company payments. The server owns order identity and payment truth.
//! No bearer tokens or payment-provider secrets are persisted in order records.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs::{self, File, OpenOptions}, io::{Read, Write}, path::{Path, PathBuf}, time::{Duration, SystemTime, UNIX_EPOCH}};

const INVALID: &str = "PAYMENT_INVALID: 请检查金额、支付方式和订单标题";
const STORAGE: &str = "PAYMENT_STORAGE: 支付记录暂时无法保存，请检查可用空间";
const CORRUPT: &str = "PAYMENT_CORRUPT: 支付记录无法读取，原记录已保留，请联系管理员";
const PROTOCOL: &str = "PAYMENT_PROTOCOL: 订单信息校验未通过，请联系管理员核实";
const ACCOUNT_CHANGED: &str = "PAYMENT_ACCOUNT_CHANGED: 登录已变化，请重新打开支付页面";
const MAX_RECORD: u64 = 131_072;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PaymentOrder {
    out_trade_no: String,
    channel: String,
    total_amount: String,
    subject: String,
    #[serde(skip_serializing_if = "Option::is_none")] body: Option<String>,
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")] pay_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] code_url: Option<String>,
    created_at: String,
    expires_at: String,
    trade_no: Option<String>,
    paid_at: Option<String>,
    refunded_amount: Option<String>,
    expired: bool,
    #[serde(skip_serializing_if = "Option::is_none")] last_checked_at: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StoredOrder { version: u8, intent_id: String, expires_unix: u64, order: PaymentOrder }
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateRequest { channel: String, total_amount: String, subject: String, body: Option<String> }
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent { version: u8, id: String, created_unix: u64, created_at: String, request: CreateRequest }

fn now() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() }
fn iso(seconds: u64) -> String {
    let z = (seconds / 86400) as i64 + 719468; let era = z.div_euclid(146097); let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365; let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1; let month = if mp < 10 { mp + 3 } else { mp - 9 }; let year = if month <= 2 { y + 1 } else { y };
    let rem = seconds % 86400;
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.000Z", rem / 3600, rem % 3600 / 60, rem % 60)
}
// Integer cents are used only for validation/comparison; wire amounts remain strings.
fn cents(input: &str, allow_zero: bool) -> Result<u64, String> {
    if input.is_empty() || input.len() > 32 { return Err(INVALID.into()); }
    let mut parts = input.split('.'); let whole = parts.next().unwrap(); let fraction = parts.next();
    if parts.next().is_some() || whole.is_empty() || !whole.bytes().all(|b| b.is_ascii_digit())
        || fraction.is_some_and(|f| f.is_empty() || f.len() > 2 || !f.bytes().all(|b| b.is_ascii_digit())) { return Err(INVALID.into()); }
    let whole = whole.parse::<u64>().map_err(|_| INVALID)?;
    let fraction = fraction.map(|s| s.parse::<u64>().unwrap() * if s.len() == 1 { 10 } else { 1 }).unwrap_or(0);
    let amount = whole.checked_mul(100).and_then(|n| n.checked_add(fraction)).ok_or(INVALID)?;
    if amount > 10_000_000_000 || (!allow_zero && amount == 0) { return Err(INVALID.into()); }
    Ok(amount)
}
fn canonical_amount(input: &str) -> Result<String, String> {
    let value = cents(input, false)?; Ok(format!("{}.{:02}", value / 100, value % 100))
}
fn bounded(text: &str, max: usize, required: bool) -> bool {
    text.chars().count() <= max && !text.chars().any(char::is_control) && (!required || !text.trim().is_empty())
}
fn validate_request(request: &CreateRequest) -> Result<(), String> {
    if !matches!(request.channel.as_str(), "wechat"|"alipay") || !bounded(&request.subject, 256, true)
        || request.subject.contains(['/', '=', '&']) || request.body.as_ref().is_some_and(|b| !bounded(b, 2048, false)) { return Err(INVALID.into()); }
    cents(&request.total_amount, false)?; Ok(())
}
fn valid_order_id(id: &str, channel: &str) -> bool {
    id.len() >= 4 && id.len() <= 64 && id.bytes().all(|b| b.is_ascii_alphanumeric())
        && id.starts_with(if channel == "wechat" { "WX" } else { "PAY" })
}
fn valid_alipay_url(text: &str) -> bool {
    if text.len() > 32_768 { return false; }
    reqwest::Url::parse(text).is_ok_and(|url| url.scheme() == "https" && url.host_str() == Some("openapi.alipay.com")
        && url.port().is_none_or(|p| p == 443) && url.path() == "/gateway.do" && url.fragment().is_none()
        && url.username().is_empty() && url.password().is_none()
        && url.query_pairs().any(|(k, v)| k == "method" && v == "alipay.trade.page.pay"))
}
fn valid_wechat_url(text: &str) -> bool {
    if text.len() > 8192 { return false; }
    reqwest::Url::parse(text).is_ok_and(|url| url.scheme() == "weixin" && url.host_str() == Some("wxpay")
        && url.path() == "/bizpayurl" && url.port().is_none() && url.fragment().is_none()
        && url.username().is_empty() && url.password().is_none()
        && url.query_pairs().any(|(k, v)| k == "pr" && !v.is_empty()))
}
fn validate_order(record: &StoredOrder) -> Result<(), String> {
    let order = &record.order;
    validate_request(&CreateRequest { channel: order.channel.clone(), total_amount: order.total_amount.clone(), subject: order.subject.clone(), body: order.body.clone() })?;
    if record.version != 1 || uuid::Uuid::parse_str(&record.intent_id).is_err() || !valid_order_id(&order.out_trade_no, &order.channel) || !matches!(order.status.as_str(), "WAIT_PAY"|"PAID"|"CLOSED")
        || order.expires_at != iso(record.expires_unix) || !bounded(&order.created_at, 40, true)
        || order.pay_url.as_ref().is_some_and(|url| order.channel != "alipay" || !valid_alipay_url(url))
        || order.code_url.as_ref().is_some_and(|url| order.channel != "wechat" || !valid_wechat_url(url))
        || order.trade_no.as_ref().is_some_and(|s| !bounded(s, 128, false))
        || order.paid_at.as_ref().is_some_and(|s| !bounded(s, 64, false))
        || order.last_checked_at.as_ref().is_some_and(|s| !bounded(s, 64, false)) { return Err(PROTOCOL.into()); }
    if let Some(amount) = &order.refunded_amount { if cents(amount, true)? > cents(&order.total_amount, false)? { return Err(PROTOCOL.into()); } }
    Ok(())
}
fn reject_link(path: &Path) -> Result<(), String> {
    let meta = fs::symlink_metadata(path).map_err(|_| STORAGE)?;
    #[cfg(windows)] let linked = { use std::os::windows::fs::MetadataExt; meta.file_attributes() & 0x400 != 0 };
    #[cfg(not(windows))] let linked = meta.file_type().is_symlink();
    if linked || meta.file_type().is_symlink() { return Err(STORAGE.into()); } Ok(())
}
struct Store { directory: PathBuf, _lock: File }
impl Store {
    fn open(root: &Path) -> Result<Self, String> {
        if !root.is_absolute() || !root.is_dir() { return Err(STORAGE.into()); }
        for parent in root.ancestors() { reject_link(parent)?; }
        let directory = root.join("payment-orders");
        match fs::create_dir(&directory) { Ok(()) => {}, Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}, Err(_) => return Err(STORAGE.into()) }
        reject_link(&directory)?;
        if !directory.is_dir() || directory.canonicalize().map_err(|_| STORAGE)?.parent() != Some(root.canonicalize().map_err(|_| STORAGE)?.as_path()) { return Err(STORAGE.into()); }
        let lock_path = directory.join("store.lock");
        if lock_path.exists() { reject_link(&lock_path)?; }
        let lock = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(lock_path).map_err(|_| STORAGE)?;
        // OS locks are process-wide and release on crash; no stale lock deletion is needed.
        lock.try_lock().map_err(|_| "PAYMENT_BUSY: 支付操作正在处理中，请稍后重试")?;
        Ok(Self { directory, _lock: lock })
    }
    fn path(&self, name: &str) -> Result<PathBuf, String> {
        if name.is_empty() || !name.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-'|b'.')) { return Err(STORAGE.into()); }
        reject_link(&self.directory)?;
        let path = self.directory.join(name);
        match fs::symlink_metadata(&path) { Ok(meta) => { reject_link(&path)?; if !meta.is_file() { return Err(CORRUPT.into()); } }, Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}, Err(_) => return Err(CORRUPT.into()) }
        Ok(path)
    }
    fn read<T: serde::de::DeserializeOwned>(&self, name: &str) -> Result<Option<T>, String> {
        let path = self.path(name)?;
        let file = match File::open(path) { Ok(file) => file, Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None), Err(_) => return Err(CORRUPT.into()) };
        let mut bytes = Vec::new(); file.take(MAX_RECORD + 1).read_to_end(&mut bytes).map_err(|_| CORRUPT)?;
        if bytes.len() as u64 > MAX_RECORD { return Err(CORRUPT.into()); }
        serde_json::from_slice(&bytes).map(Some).map_err(|_| CORRUPT.into())
    }
    fn write(&self, name: &str, value: &impl Serialize) -> Result<(), String> {
        let path = self.path(name)?; let temp = self.path(&format!("{}.tmp", uuid::Uuid::new_v4()))?;
        let bytes = serde_json::to_vec(value).map_err(|_| STORAGE)?;
        if bytes.len() as u64 > MAX_RECORD { return Err(STORAGE.into()); }
        let result = (|| -> Result<(), String> {
            let mut file = OpenOptions::new().write(true).create_new(true).open(&temp).map_err(|_| STORAGE)?;
            file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|_| STORAGE)?; drop(file);
            self.path(name)?; fs::rename(&temp, &path).map_err(|_| STORAGE)?;
            #[cfg(unix)] File::open(&self.directory).and_then(|f| f.sync_all()).map_err(|_| STORAGE)?;
            Ok(())
        })();
        if result.is_err() { let _ = fs::remove_file(temp); } result
    }
    fn order(&self, id: &str) -> Result<StoredOrder, String> {
        let record: StoredOrder = self.read(&format!("{id}.order.json"))?.ok_or("PAYMENT_NOT_FOUND: 未找到本机订单记录")?;
        validate_order(&record).map_err(|_| CORRUPT)?;
        if record.order.out_trade_no != id { return Err(CORRUPT.into()); } Ok(record)
    }
    fn save_order(&self, record: &StoredOrder) -> Result<(), String> {
        validate_order(record)?; self.write(&format!("{}.order.json", record.order.out_trade_no), record)
    }
    fn names(&self, suffix: &str) -> Result<Vec<String>, String> {
        let mut names = Vec::new();
        for entry in fs::read_dir(&self.directory).map_err(|_| STORAGE)? {
            let entry = entry.map_err(|_| STORAGE)?; let name = entry.file_name().to_string_lossy().to_string();
            if name.ends_with(suffix) { self.path(&name)?; names.push(name); }
            if names.len() > 10_000 { return Err("PAYMENT_STORAGE: 支付记录过多，请联系管理员".into()); }
        }
        Ok(names)
    }
    fn finish(&self, id: &str, outcome: &str) -> Result<(), String> { self.write(&format!("{id}.done.json"), &json!({"outcome":outcome})) }
    fn pending(&self) -> Result<Vec<Intent>, String> {
        let mut pending = Vec::new();
        for name in self.names(".intent.json")? {
            let intent: Intent = self.read(&name)?.ok_or(CORRUPT)?;
            if intent.version != 1 || uuid::Uuid::parse_str(&intent.id).is_err() || name != format!("{}.intent.json", intent.id)
                || intent.created_at != iso(intent.created_unix) { return Err(CORRUPT.into()); }
            validate_request(&intent.request).map_err(|_| CORRUPT)?;
            if let Some(done) = self.read::<Value>(&format!("{}.done.json", intent.id))? {
                if !matches!(done["outcome"].as_str(), Some("saved"|"rejected"|"cleared")) { return Err(CORRUPT.into()); }
                if done["outcome"] == "cleared" && (done["confirmation"] != "support_confirmed_no_order" || !done["confirmed_at"].as_str().is_some_and(|s| bounded(s, 40, true))) { return Err(CORRUPT.into()); }
                continue;
            }
            if let Some(receipt) = self.read::<StoredOrder>(&format!("{}.receipt.json", intent.id))? {
                validate_order(&receipt).map_err(|_| CORRUPT)?;
                if receipt.intent_id != intent.id || receipt.order.channel != intent.request.channel || cents(&receipt.order.total_amount, false)? != cents(&intent.request.total_amount, false)? { return Err(CORRUPT.into()); }
                let order_path = self.path(&format!("{}.order.json", receipt.order.out_trade_no))?;
                if !order_path.exists() { self.save_order(&receipt)?; }
                else { let existing = self.order(&receipt.order.out_trade_no)?;
                    if existing.intent_id != intent.id || existing.order.channel != receipt.order.channel || existing.order.total_amount != receipt.order.total_amount { return Err(CORRUPT.into()); }
                }
                self.finish(&intent.id, "saved")?;
            } else { pending.push(intent); }
        }
        Ok(pending)
    }
    fn orders(&self) -> Result<Vec<PaymentOrder>, String> {
        let mut orders = Vec::new();
        for name in self.names(".order.json")? {
            let id = name.strip_suffix(".order.json").ok_or(CORRUPT)?;
            let mut record = self.order(id)?;
            record.order.expired |= now() >= record.expires_unix;
            orders.push(record.order);
        }
        orders.sort_by(|a, b| b.created_at.cmp(&a.created_at).then_with(|| b.out_trade_no.cmp(&a.out_trade_no)));
        Ok(orders)
    }
}
struct Api<'a> { hub: &'a str, token: &'a str }
struct Reply { status: u16, body: Value }
impl Api<'_> {
    fn request(&self, method: &str, route: &str, body: Option<&Value>) -> Result<Reply, String> {
        let hub = crate::hub_auth::normalize(self.hub)?;
        let client = reqwest::blocking::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).retry(reqwest::retry::never())
            .connect_timeout(Duration::from_secs(10)).timeout(Duration::from_secs(35)).build().map_err(|_| "PAYMENT_NETWORK: 支付服务连接失败")?;
        let url = format!("{hub}/api/payment/{route}");
        let request = if method == "GET" { client.get(url) } else { client.post(url) };
        let mut request = request.bearer_auth(self.token);
        if let Some(body) = body { request = request.json(body); }
        let response = request.send().map_err(|_| "PAYMENT_NETWORK: 支付服务连接中断，请稍后查询订单")?;
        let status = response.status().as_u16(); let mut bytes = Vec::new();
        response.take(MAX_RECORD + 1).read_to_end(&mut bytes).map_err(|_| "PAYMENT_NETWORK: 订单回复不完整，请稍后查询")?;
        if bytes.len() as u64 > MAX_RECORD { return Err(PROTOCOL.into()); }
        let body = serde_json::from_slice(&bytes).map_err(|_| PROTOCOL)?;
        Ok(Reply { status, body })
    }
}
fn response_error(reply: &Reply) -> String {
    match reply.status {
        401 => "UNAUTHORIZED: 登录已过期，请重新登录",
        403 => "FORBIDDEN: 当前账号暂时无法使用支付服务",
        422 => INVALID,
        404 => "PAYMENT_NOT_FOUND: 服务端未找到订单，请联系管理员核实",
        400 => "PAYMENT_GATEWAY: 支付渠道未能受理，请稍后重试或联系管理员",
        _ => "PAYMENT_NETWORK: 支付服务暂不可用，请稍后查询订单",
    }.into()
}
fn optional_string(value: &Value, field: &str, max: usize) -> Result<Option<String>, String> {
    match value.get(field) { None | Some(Value::Null) => Ok(None), Some(Value::String(s)) if bounded(s, max, false) => Ok(Some(s.clone())), _ => Err(PROTOCOL.into()) }
}
fn refunded_amount(value: &Value) -> Result<Option<String>, String> {
    let text = match value.get("refunded_amount") {
        None | Some(Value::Null) => return Ok(None),
        Some(Value::String(s)) => s.clone(),
        // The API documents null/0 for no refund. Decimal JSON numbers are
        // converted to their decimal spelling, never multiplied as floats.
        Some(Value::Number(n)) => n.to_string(),
        _ => return Err(PROTOCOL.into()),
    };
    let value = cents(&text, true).map_err(|_| PROTOCOL)?;
    Ok(Some(format!("{}.{:02}", value / 100, value % 100)))
}
fn query_response(mut record: StoredOrder, value: &Value) -> Result<StoredOrder, String> {
    let id = value["out_trade_no"].as_str().ok_or(PROTOCOL)?;
    let amount = value["total_amount"].as_str().ok_or(PROTOCOL)?;
    let status = value["status"].as_str().ok_or(PROTOCOL)?;
    if value["ok"] != true || id != record.order.out_trade_no || cents(amount, false).map_err(|_| PROTOCOL)? != cents(&record.order.total_amount, false)?
        || !matches!(status, "WAIT_PAY"|"PAID"|"CLOSED") { return Err(PROTOCOL.into()); }
    record.order.status = status.into();
    record.order.trade_no = optional_string(value, "trade_no", 128)?;
    record.order.paid_at = optional_string(value, "paid_at", 64)?;
    record.order.refunded_amount = refunded_amount(value)?;
    record.order.expired = value["expired"].as_bool().ok_or(PROTOCOL)? || now() >= record.expires_unix;
    record.order.last_checked_at = Some(iso(now()));
    validate_order(&record).map_err(|_| PROTOCOL)?; Ok(record)
}
fn query(api: &Api<'_>, record: StoredOrder) -> Result<StoredOrder, String> {
    let reply = api.request("GET", &format!("{}/order/{}", record.order.channel, record.order.out_trade_no), None)?;
    if reply.status != 200 { return Err(response_error(&reply)); }
    query_response(record, &reply.body)
}
fn recovery_error(order: &PaymentOrder) -> String {
    format!("PAYMENT_RECOVERY: {}", json!({"message":"服务端已生成订单，本机保存未完成。请保留订单号并联系管理员核实，勿重复下单。", "out_trade_no":order.out_trade_no,"channel":order.channel,"total_amount":order.total_amount}))
}
fn initial_order(intent: &Intent, id: &str) -> StoredOrder {
    let expires = intent.created_unix.saturating_add(7200);
    StoredOrder { version: 1, intent_id: intent.id.clone(), expires_unix: expires, order: PaymentOrder {
        out_trade_no: id.into(), channel: intent.request.channel.clone(), total_amount: intent.request.total_amount.clone(),
        subject: intent.request.subject.clone(), body: intent.request.body.clone(), status: "WAIT_PAY".into(), pay_url: None, code_url: None,
        created_at: intent.created_at.clone(), expires_at: iso(expires), trade_no: None, paid_at: None, refunded_amount: None,
        expired: now() >= expires, last_checked_at: None,
    } }
}
fn run_inner(root: &Path, api: &Api<'_>, action: &str, body: Value, check_owner: impl Fn() -> Result<(), String>) -> Result<Value, String> {
    check_owner()?;
    if !matches!(action, "list"|"create"|"query"|"cancel"|"open"|"resolve"|"resolve_not_created") { return Err(INVALID.into()); }
    let store = Store::open(root)?;
    let pending = store.pending()?;
    if action == "list" {
        let orders = store.orders()?;
        let unresolved: Vec<Value> = pending.iter().map(|i| json!({"id":i.id,"channel":i.request.channel,"total_amount":i.request.total_amount,"subject":i.request.subject,"created_at":i.created_at,"reason":"UNKNOWN"})).collect();
        check_owner()?; return Ok(json!({"orders":orders,"unresolved":unresolved}));
    }
    if action == "create" {
        let mut request: CreateRequest = serde_json::from_value(body).map_err(|_| INVALID)?;
        validate_request(&request)?; request.total_amount = canonical_amount(&request.total_amount)?; request.subject = request.subject.trim().into();
        if !pending.is_empty() { return Err("PAYMENT_UNKNOWN: 上次下单结果尚未确认，请先核实，避免重复支付".into()); }
        for order in store.orders()?.iter().filter(|o| o.status == "WAIT_PAY") {
            if !order.expired { return Err("PAYMENT_PENDING: 已有待支付订单，请继续支付或先关闭该订单".into()); }
            // Expiry is not closure. Reconcile before replacing an old credential.
            check_owner()?;
            let refreshed = query(api, store.order(&order.out_trade_no)?)?;
            store.save_order(&refreshed)?; check_owner()?;
            if refreshed.order.status == "PAID" || (refreshed.order.status == "WAIT_PAY" && !refreshed.order.expired) {
                return Err("PAYMENT_PENDING: 原订单状态已更新，请先核对订单记录，避免重复付款".into());
            }
        }
        let created = now();
        let intent = Intent { version: 1, id: uuid::Uuid::new_v4().to_string(), created_unix: created, created_at: iso(created), request };
        store.write(&format!("{}.intent.json", intent.id), &intent)?;
        if let Err(error) = check_owner() { store.finish(&intent.id, "rejected")?; return Err(error); }
        let mut payload = json!({"total_amount":intent.request.total_amount,"subject":intent.request.subject});
        if let Some(body) = &intent.request.body { payload["body"] = json!(body); }
        let reply = api.request("POST", &format!("{}/precreate", intent.request.channel), Some(&payload))
            .map_err(|_| "PAYMENT_UNKNOWN: 下单结果尚未确认，请先核实，避免重复下单")?;
        if reply.status != 200 || reply.body["ok"] != true {
            // Only documented explicit rejections prove that no order was created.
            if matches!(reply.status, 400|401|403|422) && reply.body["ok"] == false { store.finish(&intent.id, "rejected")?; return Err(response_error(&reply)); }
            return Err("PAYMENT_UNKNOWN: 下单结果尚未确认，请先核实，避免重复下单".into());
        }
        let id = reply.body["out_trade_no"].as_str().filter(|id| valid_order_id(id, &intent.request.channel))
            .ok_or("PAYMENT_UNKNOWN: 服务端订单号未能确认，请联系管理员核实，勿重复下单")?;
        let mut record = initial_order(&intent, id);
        let mut credential_valid = if intent.request.channel == "alipay" {
            record.order.pay_url = reply.body["pay_url"].as_str().filter(|url| valid_alipay_url(url)).map(str::to_owned); record.order.pay_url.is_some()
        } else {
            record.order.code_url = reply.body["code_url"].as_str().filter(|url| valid_wechat_url(url)).map(str::to_owned); record.order.code_url.is_some()
        };
        if reply.body["expires_in"].as_u64() != Some(7200) {
            record.order.pay_url = None; record.order.code_url = None; credential_valid = false;
        }
        // Save under the initiating account even if logout happened while awaiting HTTP.
        // First preserve the receipt, so a crash before the order file is recoverable.
        store.write(&format!("{}.receipt.json", intent.id), &record).map_err(|_| recovery_error(&record.order))?;
        if store.path(&format!("{id}.order.json")).map_err(|_| recovery_error(&record.order))?.exists() { return Err(PROTOCOL.into()); }
        store.save_order(&record).map_err(|_| recovery_error(&record.order))?;
        store.finish(&intent.id, "saved").map_err(|_| recovery_error(&record.order))?;
        check_owner()?;
        if !credential_valid {
            return Err(format!("PAYMENT_CREDENTIAL: 支付入口未通过校验，订单已保留，请刷新记录查询或关闭订单。订单号：{id}"));
        }
        return serde_json::to_value(record.order).map_err(|_| PROTOCOL.into());
    }
    if action == "resolve_not_created" {
        if body["confirmation"] != "support_confirmed_no_order" { return Err("PAYMENT_CONFIRMATION: 请先由管理员核实未生成订单，再确认解除".into()); }
        let id = body["id"].as_str().ok_or(INVALID)?;
        let intent = pending.iter().find(|i| i.id == id).ok_or("PAYMENT_NOT_FOUND: 该支付请求已处理，请刷新记录")?;
        check_owner()?;
        store.write(&format!("{}.done.json", intent.id), &json!({"outcome":"cleared","confirmation":"support_confirmed_no_order","confirmed_at":iso(now())}))?;
        check_owner()?; return Ok(json!({"ok":true,"id":id}));
    }
    if action == "resolve" {
        let id = body["id"].as_str().ok_or(INVALID)?;
        let intent = pending.iter().find(|i| i.id == id).ok_or("PAYMENT_NOT_FOUND: 未找到需要核实的支付请求")?;
        let order_id = body["out_trade_no"].as_str().filter(|id| valid_order_id(id, &intent.request.channel)).ok_or(INVALID)?;
        if store.path(&format!("{order_id}.order.json"))?.exists() {
            return Err("PAYMENT_RECOVERY_CONFLICT: 该订单已在本机记录，请核实本次请求对应的订单号".into());
        }
        for name in store.names(".receipt.json")? {
            let receipt: StoredOrder = store.read(&name)?.ok_or(CORRUPT)?;
            if receipt.order.out_trade_no == order_id { return Err("PAYMENT_RECOVERY_CONFLICT: 该订单已关联其他支付请求，请联系管理员核实".into()); }
        }
        check_owner()?;
        let record = query(api, initial_order(intent, order_id))?;
        store.write(&format!("{}.receipt.json", intent.id), &record).map_err(|_| recovery_error(&record.order))?;
        // A recovered record has no reusable payment credential; query/cancel still work.
        store.save_order(&record).map_err(|_| recovery_error(&record.order))?;
        store.finish(&intent.id, "saved").map_err(|_| recovery_error(&record.order))?;
        check_owner()?; return serde_json::to_value(record.order).map_err(|_| PROTOCOL.into());
    }
    let id = body["out_trade_no"].as_str().filter(|id| id.len() <= 64 && id.bytes().all(|b| b.is_ascii_alphanumeric())).ok_or(INVALID)?;
    let mut record = store.order(id)?;
    check_owner()?;
    if action == "open" {
        if record.order.channel != "alipay" { return Err(INVALID.into()); }
        if record.order.status != "WAIT_PAY" { return Err("PAYMENT_CLOSED: 该订单已处理，请刷新订单状态".into()); }
        if record.order.expired || now() >= record.expires_unix { return Err("PAYMENT_EXPIRED: 支付入口已过期，请先查询订单结果".into()); }
        let url = record.order.pay_url.filter(|url| valid_alipay_url(url)).ok_or("PAYMENT_CREDENTIAL: 支付入口不可用，请查询订单结果")?;
        return Ok(json!({"url":url}));
    }
    if action == "cancel" {
        let reply = api.request("POST", &format!("{}/order/{}/cancel", record.order.channel, record.order.out_trade_no), None)?;
        if reply.status == 409 { record = query(api, record)?; }
        else if reply.status == 200 && reply.body["ok"] == true && reply.body["status"] == "CLOSED" {
            record.order.status = "CLOSED".into(); record.order.last_checked_at = Some(iso(now())); record.order.expired |= now() >= record.expires_unix;
        } else if reply.status == 200 { return Err(PROTOCOL.into()); }
        else { return Err(response_error(&reply)); }
    } else { record = query(api, record)?; }
    store.save_order(&record)?;
    check_owner()?;
    serde_json::to_value(record.order).map_err(|_| PROTOCOL.into())
}
pub fn run(root: &Path, action: &str, body: Value) -> Result<Value, String> {
    let (hub, token) = crate::hub_auth::payment_session(root)?;
    let result = run_inner(root, &Api { hub: &hub, token: &token }, action, body, || {
        let current = crate::hub_auth::payment_session(root).map_err(|_| ACCOUNT_CHANGED)?;
        if current.0 != hub || current.1 != token { return Err(ACCOUNT_CHANGED.into()); } Ok(())
    });
    if let Ok(user) = crate::hub_auth::require(root, "") { crate::hub_auth::audit(root, &user, &format!("payment_{action}")); }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{BufRead, BufReader}, net::TcpListener, sync::{Arc, Mutex}, thread};
    struct Temp(PathBuf);
    impl Temp { fn new() -> Self { let p = std::env::temp_dir().join(format!("omaax-payment-{}", uuid::Uuid::new_v4())); fs::create_dir(&p).unwrap(); Self(p) } }
    impl Drop for Temp { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
    struct Step { method: &'static str, path: &'static str, status: u16, value: Option<Value>, before_reply: Option<Box<dyn FnOnce() + Send>> }
    fn step(method: &'static str, path: &'static str, status: u16, value: Value) -> Step { Step { method, path, status, value: Some(value), before_reply: None } }
    type Captured = Arc<Mutex<Vec<(String, Value)>>>;
    fn server(steps: Vec<Step>) -> (String, Captured, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap(); let address = format!("http://{}", listener.local_addr().unwrap());
        let captured = Arc::new(Mutex::new(Vec::new())); let seen = captured.clone();
        let worker = thread::spawn(move || {
            for step in steps {
                let start = std::time::Instant::now();
                let mut stream = loop { match listener.accept() { Ok((s, _)) => break s, Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(start.elapsed() < Duration::from_secs(8), "local payment request did not arrive"); thread::sleep(Duration::from_millis(10));
                }, Err(e) => panic!("{e}") } };
                stream.set_nonblocking(false).unwrap(); stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut reader = BufReader::new(stream.try_clone().unwrap()); let mut line = String::new();
                reader.read_line(&mut line).unwrap(); assert_eq!(line.trim(), format!("{} {} HTTP/1.1", step.method, step.path));
                let mut length = 0; let mut auth = String::new();
                loop { line.clear(); reader.read_line(&mut line).unwrap(); if line == "\r\n" { break; }
                    if let Some(n) = line.to_ascii_lowercase().strip_prefix("content-length:") { length = n.trim().parse::<usize>().unwrap(); }
                    if line.to_ascii_lowercase().starts_with("authorization:") { auth = line.trim().into(); }
                }
                assert!(auth.ends_with("Bearer local-test-token"));
                let mut bytes = vec![0; length]; reader.read_exact(&mut bytes).unwrap();
                let body = if bytes.is_empty() { Value::Null } else { serde_json::from_slice(&bytes).unwrap() };
                seen.lock().unwrap().push((step.path.into(), body));
                if let Some(before_reply) = step.before_reply { before_reply(); }
                if let Some(value) = step.value {
                    let bytes = serde_json::to_vec(&value).unwrap();
                    write!(stream, "HTTP/1.1 {} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", step.status, bytes.len()).unwrap();
                    stream.write_all(&bytes).unwrap(); stream.flush().unwrap();
                }
            }
        });
        (address, captured, worker)
    }
    fn create_body(channel: &str) -> Value { json!({"channel":channel,"total_amount":"88.8","subject":"Research service","body":"Example"}) }
    fn created(channel: &str, id: &str) -> Value {
        if channel == "wechat" { json!({"ok":true,"out_trade_no":id,"code_url":"weixin://wxpay/bizpayurl?pr=sample","expires_in":7200}) }
        else { json!({"ok":true,"out_trade_no":id,"pay_url":"https://openapi.alipay.com/gateway.do?app_id=test&method=alipay.trade.page.pay&sign=example","expires_in":7200}) }
    }
    fn state(id: &str, status: &str, amount: &str) -> Value { json!({"ok":true,"out_trade_no":id,"status":status,"total_amount":amount,"trade_no":if status=="PAID" {Some("gateway-transaction")} else {None},"paid_at":if status=="PAID" {Some("2026-10-26T10:00:00+08:00")} else {None},"refunded_amount":null,"expired":false}) }
    fn call(root: &Path, hub: &str, action: &str, body: Value) -> Result<Value, String> { run_inner(root, &Api { hub, token:"local-test-token" }, action, body, || Ok(())) }
    fn seed_intent(store: &Store, channel: &str) -> Intent {
        let time = now(); let mut request: CreateRequest = serde_json::from_value(create_body(channel)).unwrap(); request.total_amount = "88.80".into();
        let intent = Intent { version:1, id:uuid::Uuid::new_v4().to_string(), created_unix:time, created_at:iso(time), request };
        store.write(&format!("{}.intent.json", intent.id), &intent).unwrap(); intent
    }
    #[test]
    fn payment_amounts_and_request_contract_are_exact() {
        for (amount, expected) in [("0.01", 1), ("88.8", 8880), ("0001.02", 102), ("100000000", 10_000_000_000)] { assert_eq!(cents(amount, false).unwrap(), expected); }
        for amount in ["0", "-1", "+1", "1.001", "1e2", "1.", ".01", " 1", "100000000.01", "99999999999999999999"] { assert!(cents(amount, false).is_err(), "{amount}"); }
        assert_eq!(canonical_amount("0001.2").unwrap(), "1.20"); assert_eq!(cents("0", true).unwrap(), 0);
        let mut value = create_body("wechat"); value["user_id"] = json!("forged"); assert!(serde_json::from_value::<CreateRequest>(value).is_err());
        for subject in ["", "  ", "a/b", "a=b", "a&b", "a\nb"] { let mut request: CreateRequest = serde_json::from_value(create_body("wechat")).unwrap(); request.subject = subject.into(); assert!(validate_request(&request).is_err()); }
    }
    #[test]
    fn payment_credentials_reject_redirects_scripts_and_lookalike_hosts() {
        let good = "https://openapi.alipay.com/gateway.do?method=alipay.trade.page.pay&sign=1";
        assert!(valid_alipay_url(good));
        for url in ["javascript:alert(1)", "http://openapi.alipay.com/gateway.do?method=alipay.trade.page.pay", "https://openapi.alipay.com.evil.test/gateway.do?method=alipay.trade.page.pay", "https://openapi.alipay.com@evil.test/gateway.do", "https://user@openapi.alipay.com/gateway.do?method=alipay.trade.page.pay", "https://openapi.alipay.com:444/gateway.do?method=alipay.trade.page.pay", "https://openapi.alipay.com/gateway.do?method=wrong", "https://openapi.alipay.com/else?method=alipay.trade.page.pay"] { assert!(!valid_alipay_url(url), "{url}"); }
        assert!(valid_wechat_url("weixin://wxpay/bizpayurl?pr=abc"));
        for url in ["https://wxpay/bizpayurl?pr=abc", "weixin://evil/bizpayurl?pr=abc", "weixin://wxpay/other?pr=abc", "weixin://wxpay/bizpayurl?pr="] { assert!(!valid_wechat_url(url)); }
    }
    #[test]
    fn payment_http_create_query_and_reopen_preserve_server_identity_and_account_scope() {
        let root = Temp::new(); let other = Temp::new();
        let (hub, captured, worker) = server(vec![step("POST", "/api/payment/wechat/precreate", 200, created("wechat", "WX1234")), step("GET", "/api/payment/wechat/order/WX1234", 200, state("WX1234", "PAID", "88.80"))]);
        let order = call(&root.0, &hub, "create", create_body("wechat")).unwrap();
        assert_eq!(order["status"], "WAIT_PAY"); assert_eq!(order["total_amount"], "88.80"); assert_eq!(order["out_trade_no"], "WX1234");
        assert!(call(&root.0, &hub, "create", create_body("alipay")).unwrap_err().starts_with("PAYMENT_PENDING"));
        let updated = call(&root.0, &hub, "query", json!({"out_trade_no":"WX1234","channel":"alipay","total_amount":"1"})).unwrap();
        assert_eq!(updated["status"], "PAID"); assert_eq!(updated["trade_no"], "gateway-transaction");
        let list = call(&root.0, &hub, "list", json!({})).unwrap(); assert_eq!(list["orders"][0]["status"], "PAID"); assert_eq!(list["unresolved"], json!([]));
        assert_eq!(call(&other.0, &hub, "list", json!({})).unwrap()["orders"], json!([]));
        worker.join().unwrap(); assert_eq!(captured.lock().unwrap()[0].1, json!({"total_amount":"88.80","subject":"Research service","body":"Example"}));
        for entry in fs::read_dir(root.0.join("payment-orders")).unwrap().flatten() { if entry.path().extension().is_some_and(|e| e=="json") { assert!(!fs::read_to_string(entry.path()).unwrap().contains("local-test-token")); } }
    }
    #[test]
    fn payment_cancel_conflict_reconciles_paid_instead_of_claiming_closed() {
        let root = Temp::new(); let (hub, _, worker) = server(vec![step("POST", "/api/payment/alipay/precreate", 200, created("alipay", "PAY1234")), step("POST", "/api/payment/alipay/order/PAY1234/cancel", 409, json!({"ok":false})), step("GET", "/api/payment/alipay/order/PAY1234", 200, state("PAY1234", "PAID", "88.8"))]);
        call(&root.0, &hub, "create", create_body("alipay")).unwrap();
        let opened = call(&root.0, &hub, "open", json!({"out_trade_no":"PAY1234"})).unwrap(); assert!(opened["url"].as_str().unwrap().starts_with("https://openapi.alipay.com/"));
        let order = call(&root.0, &hub, "cancel", json!({"out_trade_no":"PAY1234"})).unwrap(); assert_eq!(order["status"], "PAID");
        assert!(call(&root.0, &hub, "open", json!({"out_trade_no":"PAY1234"})).unwrap_err().starts_with("PAYMENT_CLOSED")); worker.join().unwrap();
    }
    #[test]
    fn payment_query_mismatched_identity_amount_status_and_refunds_cannot_replace_saved_truth() {
        let temp = Temp::new(); let store = Store::open(&temp.0).unwrap(); let intent = seed_intent(&store, "wechat"); let record = initial_order(&intent, "WX1234");
        for bad in [state("WX5678", "PAID", "88.80"), state("WX1234", "PAID", "0.01"), state("WX1234", "SUCCESS", "88.80")] { assert!(query_response(record.clone(), &bad).is_err()); }
        let mut paid = state("WX1234", "PAID", "88.80"); paid["refunded_amount"] = json!("90"); assert!(query_response(record.clone(), &paid).is_err());
        paid["refunded_amount"] = json!("0"); let paid = query_response(record, &paid).unwrap(); assert_eq!(paid.order.status, "PAID");
        for (value, expected) in [(json!(0), "0.00"), (json!(1.25), "1.25"), (json!(88), "88.00")] { assert_eq!(refunded_amount(&json!({"refunded_amount":value})).unwrap().unwrap(), expected); }
        assert!(refunded_amount(&json!({"refunded_amount":1.001})).is_err());
        let mut closed = state("WX1234", "CLOSED", "88.80"); closed["refunded_amount"] = json!("88.80"); assert_eq!(query_response(paid, &closed).unwrap().order.status, "CLOSED");
    }
    #[test]
    fn payment_unknown_create_is_durable_and_cannot_be_retried_until_server_order_is_recovered() {
        let root = Temp::new(); let (hub, seen, worker) = server(vec![Step { method:"POST", path:"/api/payment/wechat/precreate", status:0, value:None, before_reply:None }, step("GET", "/api/payment/wechat/order/WX9999", 200, state("WX9999", "PAID", "88.80"))]);
        assert!(call(&root.0, &hub, "create", create_body("wechat")).unwrap_err().starts_with("PAYMENT_UNKNOWN"));
        let list = call(&root.0, &hub, "list", json!({})).unwrap(); assert_eq!(list["unresolved"].as_array().unwrap().len(), 1);
        assert!(call(&root.0, &hub, "create", create_body("wechat")).unwrap_err().starts_with("PAYMENT_UNKNOWN"));
        let order = call(&root.0, &hub, "resolve", json!({"id":list["unresolved"][0]["id"],"out_trade_no":"WX9999"})).unwrap(); assert_eq!(order["status"], "PAID"); assert!(order.get("code_url").is_none());
        assert_eq!(call(&root.0, &hub, "list", json!({})).unwrap()["unresolved"], json!([])); worker.join().unwrap(); assert_eq!(seen.lock().unwrap().len(), 2);
    }
    #[test]
    fn payment_documented_rejection_is_retryable_without_exposing_gateway_payloads() {
        let root = Temp::new(); let (hub, _, worker) = server(vec![step("POST", "/api/payment/wechat/precreate", 422, json!({"ok":false,"error":{"message":"secret=local-test-token"}})), step("POST", "/api/payment/wechat/precreate", 200, created("wechat", "WX1234"))]);
        let error = call(&root.0, &hub, "create", create_body("wechat")).unwrap_err(); assert!(error.starts_with("PAYMENT_INVALID")); assert!(!error.contains("local-test-token"));
        assert_eq!(call(&root.0, &hub, "list", json!({})).unwrap()["unresolved"], json!([]));
        assert_eq!(call(&root.0, &hub, "create", create_body("wechat")).unwrap()["out_trade_no"], "WX1234"); worker.join().unwrap();
    }
    #[test]
    fn payment_receipt_recovers_after_crash_without_overwriting_newer_order_status() {
        let root = Temp::new(); let existing = Temp::new();
        { let store = Store::open(&root.0).unwrap(); let intent = seed_intent(&store, "wechat"); let receipt = initial_order(&intent, "WX1234");
          store.write(&format!("{}.receipt.json", intent.id), &receipt).unwrap(); }
        assert_eq!(call(&root.0, "http://127.0.0.1:1", "list", json!({})).unwrap()["orders"][0]["out_trade_no"], "WX1234");
        { let store = Store::open(&existing.0).unwrap(); let intent = seed_intent(&store, "wechat"); let receipt = initial_order(&intent, "WX1234");
          store.write(&format!("{}.receipt.json", intent.id), &receipt).unwrap(); let paid = query_response(receipt, &state("WX1234", "PAID", "88.80")).unwrap(); store.save_order(&paid).unwrap(); }
        assert_eq!(call(&existing.0, "http://127.0.0.1:1", "list", json!({})).unwrap()["orders"][0]["status"], "PAID");
    }
    #[test]
    fn payment_save_failure_keeps_receipt_and_returns_recoverable_server_order_number() {
        let root = Temp::new(); let blocked_path = root.0.join("payment-orders/WX1234.order.json"); let blocked = blocked_path.clone();
        let mut reply = step("POST", "/api/payment/wechat/precreate", 200, created("wechat", "WX1234"));
        // Emulate a storage failure after the network response, not before dispatch.
        reply.before_reply = Some(Box::new(move || { fs::create_dir(blocked).unwrap(); }));
        let (hub, _, worker) = server(vec![reply]);
        let error = call(&root.0, &hub, "create", create_body("wechat")).unwrap_err(); worker.join().unwrap();
        assert!(error.starts_with("PAYMENT_RECOVERY")); assert!(error.contains("WX1234"));
        let receipts: Vec<_> = fs::read_dir(root.0.join("payment-orders")).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().ends_with(".receipt.json")).collect();
        assert_eq!(receipts.len(), 1); assert!(fs::read_to_string(receipts[0].path()).unwrap().contains("WX1234"));
        fs::remove_dir(blocked_path).unwrap();
        let restored = call(&root.0, &hub, "list", json!({})).unwrap(); assert_eq!(restored["orders"][0]["out_trade_no"], "WX1234"); assert_eq!(restored["unresolved"], json!([]));
    }
    #[test]
    fn payment_recovery_cannot_bind_an_existing_order_to_an_unknown_request() {
        let root = Temp::new(); let id;
        { let store = Store::open(&root.0).unwrap(); let intent = seed_intent(&store, "wechat"); id = intent.id.clone(); let record = initial_order(&intent, "WX1234"); store.save_order(&record).unwrap(); }
        let error = call(&root.0, "http://127.0.0.1:1", "resolve", json!({"id":id,"out_trade_no":"WX1234"})).unwrap_err();
        assert!(error.starts_with("PAYMENT_RECOVERY_CONFLICT"));
        let store = Store::open(&root.0).unwrap(); assert_eq!(store.pending().unwrap().len(), 1); assert_eq!(store.order("WX1234").unwrap().order.subject, "Research service");
    }
    #[test]
    fn payment_manual_resolution_requires_explicit_confirmation_preserves_evidence_and_never_creates() {
        let root = Temp::new(); let id;
        { let store = Store::open(&root.0).unwrap(); id = seed_intent(&store, "wechat").id; }
        let hub = "http://127.0.0.1:1";
        assert!(call(&root.0, hub, "resolve_not_created", json!({"id":id})).unwrap_err().starts_with("PAYMENT_CONFIRMATION"));
        assert_eq!(call(&root.0, hub, "list", json!({})).unwrap()["unresolved"].as_array().unwrap().len(), 1);
        let request = json!({"id":id,"confirmation":"support_confirmed_no_order"});
        assert_eq!(call(&root.0, hub, "resolve_not_created", request.clone()).unwrap()["ok"], true);
        assert!(call(&root.0, hub, "resolve_not_created", request).unwrap_err().starts_with("PAYMENT_NOT_FOUND"));
        let list = call(&root.0, hub, "list", json!({})).unwrap(); assert_eq!(list["orders"], json!([])); assert_eq!(list["unresolved"], json!([]));
        let store = Store::open(&root.0).unwrap(); assert!(store.read::<Intent>(&format!("{id}.intent.json")).unwrap().is_some());
        let done: Value = store.read(&format!("{id}.done.json")).unwrap().unwrap(); assert_eq!(done["outcome"], "cleared"); assert!(done["confirmed_at"].as_str().unwrap().ends_with('Z'));
    }
    #[test]
    fn payment_account_change_after_create_retains_receipt_for_original_account_only() {
        let root = Temp::new(); let (hub, _, worker) = server(vec![step("POST", "/api/payment/wechat/precreate", 200, created("wechat", "WX1234"))]);
        let checks = std::cell::Cell::new(0);
        let result = run_inner(&root.0, &Api { hub:&hub, token:"local-test-token" }, "create", create_body("wechat"), || {
            checks.set(checks.get() + 1); if checks.get() > 2 { Err(ACCOUNT_CHANGED.into()) } else { Ok(()) }
        });
        assert_eq!(result.unwrap_err(), ACCOUNT_CHANGED); worker.join().unwrap();
        assert_eq!(call(&root.0, &hub, "list", json!({})).unwrap()["orders"][0]["out_trade_no"], "WX1234");
    }
    #[test]
    fn payment_expiry_does_not_mean_closed_and_is_reconciled_before_replacement() {
        let root = Temp::new();
        { let store = Store::open(&root.0).unwrap(); let mut intent = seed_intent(&store, "wechat"); intent.created_unix = now()-7201; intent.created_at = iso(intent.created_unix);
          let record = initial_order(&intent, "WX1234"); store.save_order(&record).unwrap(); store.finish(&intent.id, "saved").unwrap(); }
        let (hub, seen, worker) = server(vec![step("GET", "/api/payment/wechat/order/WX1234", 200, state("WX1234", "PAID", "88.80"))]);
        let list = call(&root.0, &hub, "list", json!({})).unwrap(); assert_eq!(list["orders"][0]["status"], "WAIT_PAY"); assert_eq!(list["orders"][0]["expired"], true);
        assert!(call(&root.0, &hub, "create", create_body("wechat")).unwrap_err().starts_with("PAYMENT_PENDING"));
        worker.join().unwrap(); assert_eq!(seen.lock().unwrap().len(), 1); assert_eq!(call(&root.0, &hub, "list", json!({})).unwrap()["orders"][0]["status"], "PAID");
    }
    #[test]
    fn payment_unsafe_credentials_keep_order_number_without_enabling_payment_entry() {
        let root = Temp::new(); let mut response = created("alipay", "PAY1234"); response["pay_url"] = json!("https://evil.test/pay");
        let (hub, _, worker) = server(vec![step("POST", "/api/payment/alipay/precreate", 200, response)]);
        let error = call(&root.0, &hub, "create", create_body("alipay")).unwrap_err(); assert!(error.starts_with("PAYMENT_CREDENTIAL")); assert!(error.contains("PAY1234")); worker.join().unwrap();
        let list = call(&root.0, &hub, "list", json!({})).unwrap(); assert!(list["orders"][0].get("pay_url").is_none()); assert_eq!(list["orders"][0]["out_trade_no"], "PAY1234");
        assert!(call(&root.0, &hub, "open", json!({"out_trade_no":"PAY1234"})).unwrap_err().starts_with("PAYMENT_CREDENTIAL"));
    }
    #[test]
    fn payment_storage_locks_and_corruption_do_not_silently_replace_history() {
        let root = Temp::new(); let store = Store::open(&root.0).unwrap();
        assert!(Store::open(&root.0).err().unwrap().starts_with("PAYMENT_BUSY"));
        fs::write(store.directory.join("WX1234.order.json"), b"{broken").unwrap();
        assert!(store.orders().unwrap_err().starts_with("PAYMENT_CORRUPT")); assert_eq!(fs::read(store.directory.join("WX1234.order.json")).unwrap(), b"{broken");
        assert!(store.path("../outside.json").is_err()); drop(store); assert!(Store::open(&root.0).is_ok());
    }
    #[test]
    fn payment_file_lock_excludes_another_process_and_releases_after_close() {
        let root = Temp::new(); let store = Store::open(&root.0).unwrap();
        let result = std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact", "payment::tests::payment_child_lock_probe", "--nocapture"])
            .env("OMAAX_PAYMENT_TEST_LOCK", &root.0).output().unwrap();
        assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stdout));
        drop(store); assert!(Store::open(&root.0).is_ok());
    }
    #[test]
    fn payment_child_lock_probe() {
        if let Some(path) = std::env::var_os("OMAAX_PAYMENT_TEST_LOCK") {
            assert!(Store::open(Path::new(&path)).err().unwrap().starts_with("PAYMENT_BUSY"));
        }
    }
    #[test]
    fn payment_access_does_not_require_knowledge_entitlement() {
        let user = crate::hub_auth::User { id:uuid::Uuid::new_v4().to_string(),username:"customer".into(),role:"user".into(),authorizations:vec![] };
        assert_eq!(crate::hub_auth::command_unit("payment").unwrap(), Some(""));
        assert!(crate::hub_auth::has(&user, "")); assert!(!crate::hub_auth::has(&user, "qa"));
    }
    #[cfg(feature = "local-access")]
    #[test]
    fn payment_local_mode_cannot_read_a_company_session() {
        assert_eq!(crate::hub_auth::payment_session(&crate::hub_auth::base()).unwrap_err(), "UNAUTHORIZED: 支付需要登录公司账号");
    }
    #[cfg(windows)]
    #[test]
    fn payment_storage_rejects_junctions_without_touching_target() {
        use std::os::windows::process::CommandExt;
        let root = Temp::new(); let target = root.0.join("target"); let account = root.0.join("account");
        fs::create_dir(&target).unwrap(); fs::create_dir(&account).unwrap(); fs::write(target.join("original"), b"original").unwrap();
        let link = account.join("payment-orders");
        let output = std::process::Command::new("cmd.exe").args(["/D", "/C", "mklink", "/J"]).arg(&link).arg(&target).creation_flags(0x08000000).output().unwrap(); assert!(output.status.success());
        assert!(Store::open(&account).is_err()); assert_eq!(fs::read_dir(&target).unwrap().count(), 1); assert_eq!(fs::read(target.join("original")).unwrap(), b"original");
        fs::remove_dir(link).unwrap();
    }
}
