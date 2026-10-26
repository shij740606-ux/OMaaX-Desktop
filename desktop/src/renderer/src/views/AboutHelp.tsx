import { useT } from '../i18n';
import {useState} from 'react';

const topics = [
  {group:'开始使用', title:'如何让助手操作网页？', text:'直接在主对话中说明网址和操作目标，例如查找资料、填写表单或截图。助手使用独立浏览器窗口；需要登录时请自行登录，再让助手继续。窗口不会沿用日常浏览器的登录状态。'},
  {group:'分析与数据', title:'如何生成 Word、PPT 或 PDF？', text:'在主对话中说“把这些结果整理成 Word、PPT 或 PDF”，并说明用途、内容和保存项目。生成后可以继续对话修改，在“分析结果 → 项目文档”查看和打开文件。'},
  {group:'知识库与科研', title:'课题研究怎么使用？', text:'在“知识库问答”打开“课题研究”，关联当前项目并填写课题。展开“研究资料”可收录问答结果和添加笔记，更新后在“关系图谱”查看资料关联，在“下一步建议”查看行动步骤并交给主对话执行。建议需要结合真实数据和实验条件核实。'},
  {group:'常见问题', title:'付款后会自动增加模型额度吗？', text:'当前支付功能用于收款和查询订单，尚未接入套餐或额度入账。请按实际约定付款，以订单显示的状态为准；状态未确认时先查询订单，不要重复付款。'},
  {group:'开始使用', title:'第一次使用，从哪里开始？', text:'登录后创建项目或新对话，说明目标、数据位置和希望得到的结果。可先让助手检查输入和运行环境，再开始分析。已有对话可以从左侧项目列表继续。'},
  {group:'开始使用', title:'任务在本机还是服务器运行？', text:'本机文件可以在本机处理。服务器数据请先在“服务器管理”连接对应服务器，并在对话中说明服务器和文件路径。助手会根据任务和数据位置选择运行位置；不必把所有本机文件上传。'},
  {group:'分析与数据', title:'如何使用一体化分析流程？', text:'进入“一体化分析流程”，选择分析类型和步骤，填写已知数据和参数，再交给对话准备分析。可以在对话中补充输入、修改参数和修复环境。正式分析按所选流程执行，请通过执行记录、日志和产物确认完成状态。'},
  {group:'分析与数据', title:'如何保护原始数据？', text:'在“服务器管理 → 数据保护与审计”中登记原始数据路径。已接入保护的文件操作会阻止对这些路径写入、覆盖和删除；可以读取、向外复制或建立软链接。这不等于备份，自定义脚本仍需谨慎使用。'},
  {group:'分析与数据', title:'软件缺失或环境报错怎么办？', text:'把报错和任务目标发给助手，让它检查依赖并修复环境。不要反复提交同一个分析；先查看已有任务的日志和状态。如果遇到账号无权访问、网络不可达等问题，需先解决对应连接条件。'},
  {group:'知识库与科研', title:'知识库怎么提问、停止和复制？', text:'在“知识库问答”输入科研问题或文献关键词，选择检索或问答。处理中可使用停止按钮；已有内容可继续查看。通过复制入口复制回答或检索结果，图片能否复制取决于图片来源与当前系统支持。'},
  {group:'知识库与科研', title:'教育科研怎么使用？还要填写 Key 吗？', text:'选择期刊查询、投稿推荐、审稿回复、排版或科研汇报等功能，按页面要求提供文本或文件。智能功能复用主对话的账号模型服务，不需要单独填写 Key；首次打开可能需要准备本机组件，请耐心等待。'},
  {group:'知识库与科研', title:'科研绘图怎么修改和导出？', text:'在“科研绘图”描述需要的图，生成后可以对话修改，也可以手动调整素材、位置、大小和图层，并保存到项目。二维示意图可导出 SVG 或 PNG；三维结构图使用图片导出，不能把截图当作可编辑矢量图。'},
  {group:'知识库与科研', title:'工具中心“添加”后就能直接分析吗？', text:'点击添加会下载所选工具。部分工具还需要数据和运行依赖，添加成功不代表所有依赖已经准备好。可在对话中指定工具和数据，让助手检查后运行。下载失败时先检查网络，再重试。'},
  {group:'常见问题', title:'服务器断开了，任务会不会丢？', text:'先恢复服务器连接，再查看已有任务和日志，不要立即重复提交。已提交到后台的任务可能仍在运行；交互式任务可能中断，具体以实际状态为准。'},
  {group:'常见问题', title:'回答停住、生成很慢怎么办？', text:'先查看处理状态和工具日志。长内容达到单次输出上限时会自动续接；如果发生网络或服务错误，恢复后可继续当前对话。停止按钮用于终止当前处理，避免连续点击发送造成重复任务。'},
  {group:'常见问题', title:'登录失效或模型暂不可用怎么办？', text:'确认网络正常，必要时退出后重新登录。如果主对话也不能使用，请联系管理员核对账号和服务状态。不要把密码、Key 或完整凭据发到对话中。'},
  {group:'常见问题', title:'结果保存在哪里？更新会清空对话吗？', text:'本机文件可在项目和“本次分析文件”中查看，服务器产物请查看对应任务和服务器文件。设置中的“打开数据目录”可查看应用数据位置。正常升级应保留配置和对话；更新前建议备份重要项目，不要手动删除数据目录。'},
  {group:'常见问题', title:'问题仍未解决，反馈时需要提供什么？', text:'提供软件版本、操作步骤、发生时间和报错截图；分析问题可附任务名称及相关日志片段。先遮盖账号凭据、个人信息和不希望分享的数据。'},
];

export default function AboutHelp(){
 const {t,lang}=useT();
  const [query,setQuery]=useState('');
  const needle=query.trim().toLocaleLowerCase();
  const visible=topics.filter(item=>`${t(item.group)} ${t(item.title)} ${t(item.text)}`.toLocaleLowerCase().includes(needle));
  return <section className="mt-6" aria-labelledby="about-help-title">
    <h3 id="about-help-title" className="t-body font-semibold">{t("帮助与常见问题")}</h3>
    <p className="t-caption mt-2">{t("按功能查找使用方法，或搜索你遇到的问题。")}</p>
    <input type="search" aria-label={t("搜索帮助")} placeholder={t("搜索功能、服务器、绘图、报错…")} value={query} onChange={e=>setQuery(e.target.value)} className="input w-full mt-3 mb-3"/>
    <p className="t-caption mb-2" role="status">{t("找到 {count} 条帮助", {count:visible.length})}</p>
    {visible.map(item=><details key={item.title} className="py-3 border-b border-black/10 dark:border-white/10">
      <summary className="cursor-pointer t-body"><span className="t-caption mr-2">{t(item.group)}</span>{t(item.title)}</summary>
      <p className="t-body mt-3 leading-relaxed whitespace-pre-wrap">{t(item.text)}</p>
    </details>)}
    {!visible.length&&<p className="t-caption py-4">{t("没有找到相关内容，请换一个关键词，例如“服务器”或“结果”。")}</p>}
  </section>;
}
