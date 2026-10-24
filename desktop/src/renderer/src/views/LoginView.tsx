import { PasswordForm } from '../components/AccountManagement';
import { useState } from 'react';
import { LockKeyhole, Loader2 } from 'lucide-react';
import { useApp } from '../store';
import { useT } from '../i18n';

export default function LoginView() {
  const { t } = useT();
  const { settings, platform, platformLogin, updateSettings, refreshPlatform } = useApp();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [recovery, setRecovery] = useState(false);
  const submit = async () => {
    if (!username.trim() || !password) return;
    setBusy(true); setError('');
    try { await platformLogin(username, password); setPassword(''); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  if (platform.deviceAuthorized) return <div className="card p-5 flex flex-col gap-3"><div className="t-headline">{t("此设备已授权，正在自动重连")}</div><div role="status">{t(platform.connectionError || '等待计算服务连接恢复，无需重新输入密码。')}</div><button className="btn btn-primary" disabled={busy} onClick={() => { setBusy(true); void refreshPlatform().catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{t("立即重试")}</button>{error && <div role="alert">{t(error)}</div>}</div>;
  return (
    <div className="h-full w-full overflow-y-auto flex items-center justify-center drag" style={{ background: 'var(--bg)' }}>
      <div className="card no-drag w-[380px] max-h-[calc(100vh-32px)] overflow-y-auto p-7 flex flex-col gap-4" style={{ boxShadow: 'var(--shadow-sheet)' }}>
        <div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}><LockKeyhole size={22} /></div>
        <div><div className="t-title">{t("首次授权此设备")}</div><div className="t-caption mt-1">{settings?.platformUrl}</div><div className="t-caption mt-2">{t("使用计算服务账号验证一次。授权后安全保存设备凭据，今后启动自动连接。")}</div></div>
        <label className="flex flex-col gap-1"><span className="t-caption">{t('用户名')}</span><input className="field" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="flex flex-col gap-1"><span className="t-caption">{t('密码')}</span><input className="field" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void submit()} /></label>
        <button className="btn btn-ghost" disabled={busy} onClick={() => setRecovery(!recovery)}>{recovery ? t("返回登录") : t("忘记密码？")}</button>
        {recovery && <PasswordForm recovery />}
        {error && <div className="t-caption" style={{ color: 'var(--red)' }}>{t(error)}</div>}
        <button className="btn btn-primary justify-center" disabled={busy || !username.trim() || !password} onClick={() => void submit()}>{busy && <Loader2 size={14} className="spin" />}{t("授权此设备并自动连接")}</button>
        <button className="btn btn-ghost justify-center" onClick={() => void updateSettings({ platformUrl: '' })}>{t('返回本地模式')}</button>
        <div className="t-caption text-center">{t("不保存账号密码。设备凭据保存在系统安全存储，可随时撤销；授权到期或密码重置后需重新验证。")}</div>
      </div>
    </div>
  );
}
