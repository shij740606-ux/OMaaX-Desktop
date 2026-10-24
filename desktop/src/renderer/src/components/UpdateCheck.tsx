import { useT } from '../i18n';
export default function UpdateCheck({currentVersion}:{currentVersion?:string}) {
 const {t,lang}=useT(); return <div className="t-caption">OMaaX {currentVersion} · {t("独立版本，自动更新暂未启用。")}</div>; }
