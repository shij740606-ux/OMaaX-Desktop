import { useT } from '../i18n';
import { Check, Download, Loader2 } from 'lucide-react';
import { useApp } from '../store';
import type { CatalogSkill } from '@shared/types';

export default function InstallButton({ skill, size = 'sm' }: { skill: CatalogSkill; size?: 'sm' | 'lg' }) {
 const {t,lang}=useT();
  const { statuses, busy, install } = useApp();
  const status = statuses[skill.id];
  const installed = status?.state === 'installed' || status?.state === 'update_available';
  const pending = !!busy[skill.id];
  return <div className="flex flex-col items-end gap-1 max-w-full">
    <button className={`btn ${installed ? 'btn-ghost' : 'btn-tint'} ${size === 'sm' ? '!h-7 !px-2 text-xs' : ''}`}
      disabled={installed || pending || !!skill.unavailableReason}
      onClick={e => { e.stopPropagation(); void install(skill.id); }}
      title={skill.tier === 'community' ? t("只下载这个工具，不安装其他工具或运行依赖") : t("添加此基础工具，运行环境按需配置")}>
      {pending ? <Loader2 size={14} className="animate-spin" /> : installed ? <Check size={14} /> : <Download size={14} />}
      {pending ? t("正在添加…") : installed ? t("已添加") : skill.unavailableReason ? t("来源暂不可用") : status?.error ? t("重试添加") : t("添加")}
    </button>
    {!installed && (skill.unavailableReason || status?.error) && <span role="alert" className="text-xs max-w-64 text-right break-words" style={{color:'var(--red)'}}>{t(skill.unavailableReason || status?.error || "")}</span>}
  </div>;
}
