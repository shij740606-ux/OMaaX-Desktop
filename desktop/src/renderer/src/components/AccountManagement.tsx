import { useT } from '../i18n';
import { useState } from 'react';
import { useApp } from '../store';

const call = (action: string, body: unknown = {}) =>
  window.biodsh.platformProjectCollaboration('', `account-${action}`, body);

export function PasswordForm({ recovery = false }: { recovery?: boolean }) {
 const {t,lang}=useT();
  const { refreshPlatform } = useApp();
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (password !== confirm) { setNotice('两次输入的新密码不一致'); return; }
    setBusy(true); setNotice('');
    try {
      await call(recovery ? 'reset' : 'password', recovery
        ? { token: current.trim(), new_password: password }
        : { current_password: current, new_password: password });
      setCurrent(''); setPassword(''); setConfirm('');
      setNotice('密码已更新，请使用新密码登录。');
      if (!recovery) await refreshPlatform();
    } catch (error) { setNotice(String(error)); }
    finally { setBusy(false); }
  };
  return <div className="flex flex-col gap-2">
    <div className="t-body font-semibold">{recovery ? t("找回平台账号密码") : t("修改平台账号密码")}</div>
    <div className="t-caption">{recovery ? t("请联系管理员核验身份并获取一次性重置码，15 分钟内有效。") : t("仅适用于本地平台账号。修改后所有设备的旧登录都会失效。")}</div>
    <input className="field" type="password" autoComplete={recovery ? 'off' : 'current-password'} aria-label={recovery ? t("一次性重置码") : t("当前密码")} placeholder={recovery ? t("一次性重置码") : t("当前密码")} value={current} onChange={e => setCurrent(e.target.value)} />
    <input className="field" type="password" autoComplete="new-password" aria-label={t("新密码")} placeholder={t("新密码（12 至 256 个字符）")} maxLength={256} value={password} onChange={e => setPassword(e.target.value)} />
    <input className="field" type="password" autoComplete="new-password" aria-label={t("确认新密码")} placeholder={t("再次输入新密码")} maxLength={256} value={confirm} onChange={e => setConfirm(e.target.value)} />
    <button className="btn btn-primary self-start" disabled={busy || !current || password.trim().length === 0 || password.length < 12 || password !== confirm} onClick={() => void submit()}>{busy ? t("正在保存…") : t("保存新密码")}</button>
    {notice && <div role="status" className="t-caption break-words">{t(notice)}</div>}
  </div>;
}
