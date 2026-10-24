import {useT} from '../i18n';
import type { PlatformJob } from '@shared/types';

export default function ExecutionProtection({ job }: { job: PlatformJob }) {
 const {t,lang}=useT();
  const raw = job.resultManifest?.protection;
  const proof = raw && typeof raw === 'object' ? raw as Record<string, unknown> : null;
  return <section className="card p-4 flex flex-col gap-2" aria-label={t("执行保护记录")}>
    <div className="t-headline">{t("执行保护记录")}</div>
    {proof?.policy === 'isolated-v1' && proof.container_stopped === true ? <>
      <div className="t-body">{t("Worker 已报告隔离执行结束；输入只读，输出保存在本次独立任务目录。")}</div>
      <div className="t-caption break-all">{t("环境版本：")}{String(proof.image || '')}</div>
      <div className="t-caption break-all">{t("脚本及参数校验：")}{String(proof.command_sha256 || '')}</div>
      <div className="t-caption">{t("工作区硬容量限制：")}{proof.hard_workspace_limit === true ? t("已启用独立文件系统上限") : t("尚未启用，仅监测用量")}</div>
      <div className="t-caption">{t("此记录不代表原始数据已经备份，也不代替分析结果的科学质量检查。")}</div>
    </> : <div className="t-caption">{t("尚无已完成的隔离执行记录。只有配置了数据保护的计算节点才能领取新任务；等待、失败或历史任务不显示为已通过保护验收。")}</div>}
  </section>;
}
