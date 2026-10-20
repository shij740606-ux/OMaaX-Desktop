import { useState } from 'react';
import {useAuth} from '../AuthGate';
import { ArrowRight, Check, ExternalLink, Star } from 'lucide-react';
import { useApp } from '../store';
import { Logo, WindowControls } from '../components/Sidebar';
import { useT } from '../i18n';

// 引导覆盖整个应用，内容随窗口缩放，小窗口可滚动。
export default function Onboarding() {
  const { updateSettings, env, installEnv, setTab } = useApp();
  const { t } = useT();
  const {user}=useAuth();const bioflow=!!user;
  const [step, setStep] = useState(0);
  const installing = ['python', 'venv', 'packages', 'verify'].includes(env.step);
  const finish = async (goStore = true) => { await updateSettings({ onboarded: true }); setTab(goStore ? 'store' : 'chat'); };

  return (
    <div data-testid="onboarding" className="fixed inset-0 z-50 overflow-y-auto" style={{ background: 'var(--bg)' }}>
      <div className="sticky top-0 h-12 w-full drag z-10" style={{ background: 'var(--bg)' }}><div className="absolute right-3 top-2 no-drag"><WindowControls compact /></div></div>
      <div className="min-h-[calc(100%-3rem)] flex items-center justify-center px-6 py-10" style={{ minHeight: 'calc(100% - 3rem)' }}>
      <div className="w-full max-w-[640px] flex flex-col items-center text-center no-drag rise" key={step}>
        {step === 0 && (
          <>
            <Logo size={72} />
            <div className="t-largetitle mt-6">{t('欢迎使用 OMaaX')}</div>
            <p className="t-body mt-3 max-w-[400px]" style={{ color: 'var(--text-2)', fontSize: 14, lineHeight: '21px' }}>
              {t('用自然语言开展分析，连接你的数据、工具和服务器。')}
            </p>
            <button className="btn btn-primary btn-lg mt-8" onClick={() => setStep(1)}>{t('开始')} <ArrowRight size={15} /></button>
          </>
        )}
        {step === 1 && (
          <>
            <div className="t-caption">{t('第 1 步 / 共 3 步')}</div>
            <div className="t-title1 mt-2">{t('对话服务')}</div>
            <p className="t-body mt-2">{t('登录后使用公司提供的对话服务，无需填写访问密钥。')}</p>
            <button className="btn btn-primary btn-lg mt-6" onClick={() => setStep(bioflow?2:3)}>{t('继续')} <ArrowRight size={15}/></button>
          </>
        )}
        {step === 2 && (
          <>
            <div className="t-caption">{t('第 2 步 / 共 3 步')}</div>
            <div className="t-title1 mt-2">{t('准备计算环境')}</div>
            <p className="t-body mt-2 max-w-[400px]" style={{ color: 'var(--text-2)' }}>
              {t('技能依赖一套 Python 分析软件包（scanpy 等）。只需安装一次，约需几分钟，期间可以先去逛商店。')}
            </p>
            <div className="w-full mt-6 rounded-xl p-4 text-left" style={{ background: 'var(--surface-2)' }}>
              {env.ready ? (
                <div className="flex items-center gap-2 t-body"><span className="w-5 h-5 rounded-full flex items-center justify-center" style={{ background: 'var(--green)', color: '#fff' }}><Check size={12} strokeWidth={3} /></span>{t('环境已就绪 · Python {v}', { v: env.pythonVersion ?? '' })}</div>
              ) : installing ? (
                <>
                  <div className="t-body">{env.message}</div>
                  <div className="mt-2 h-[6px] rounded-full overflow-hidden" style={{ background: 'var(--fill)' }}><div className="h-full rounded-full transition-all" style={{ width: `${Math.round(env.progress * 100)}%`, background: 'var(--accent)' }} /></div>
                </>
              ) : env.step === 'error' ? (
                <div className="t-body" style={{ color: 'var(--red)' }}>{env.message}</div>
              ) : (
                <div className="t-body" style={{ color: 'var(--text-2)' }}>{t('安装位置：应用数据目录/bioenv（不影响系统 Python）')}</div>
              )}
            </div>
            <div className="flex gap-3 mt-6">
              {!env.ready && !installing && <button className="btn btn-primary btn-lg" onClick={() => installEnv()}>{env.step === 'error' ? t('重试安装') : t('开始安装')}</button>}
              <button className={`btn btn-lg ${env.ready ? 'btn-primary' : 'btn-fill'}`} onClick={() => setStep(3)}>{env.ready ? t('下一步') : t('稍后再装')} <ArrowRight size={15} /></button>
            </div>
          </>
        )}
        {step === 3 && <><Logo size={64}/><div className="t-title1 mt-5">准备开始</div><p className="t-body mt-3">新建项目、添加数据，然后用自然语言描述你的分析目标。</p><button className="btn btn-primary mt-6" onClick={()=>finish(false)}>进入 OMaaX</button></>}

      </div>
      </div>
    </div>
  );
}
