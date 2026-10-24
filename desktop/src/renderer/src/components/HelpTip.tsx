import { Info } from 'lucide-react';
import { useT } from '../i18n';

export const HELP = {
  model: ['对话服务', '登录后使用公司提供的对话服务。', '无需填写访问密钥。如提示尚未开通，请联系管理员。'],
  image: ['图像服务', '连接图像模型，在对话中制作示意图和插图。', '先选择服务商，再填写该服务的接口地址、图像模型名称和密钥。保存后，可在对话中说“帮我画一张示意图”；使用内置图像生成工具，无需开启终端。不配置也能做数据分析，散点图、热图等数据图表由分析软件绘制。发送给图像服务的提示和素材会交由该服务处理，费用以服务商计费为准。'],
  mcp: ['外部工具连接', '把文献库、检索服务或其他应用接到 OMaaX 对话中。', 'MCP 是连接外部工具的一种方式。选择预设后，按该服务的说明填写连接信息，保存并重启助手。预设只是配置起点，不表示服务已经安装或可用。本机方式在这台电脑启动服务，首次可能下载组件；服务地址方式连接已有服务。它与 SSH 分析服务器是两种连接，权限和数据去向取决于所连接的服务。'],
  env: ['本机分析环境', '在这台电脑准备分析软件；服务器环境单独管理。', '基础环境包含 Python 和常用单细胞分析软件，安装到 OMaaX 自己的环境目录，不会替换系统的 Python。只有在本机运行相关分析时才需要安装；使用 SSH 服务器分析时，到服务器管理或对应流程中准备环境。本页的扩展组件只在你点击安装时添加，不会安装全部扩展工具。'],
  tools: ['工具怎么使用', '先选需要的工具，再把分析需求交给对话助手。', '“添加”获取的是工具说明及其附带文件，不代表所有分析软件都已安装。只浏览目录不会下载全部工具。运行前还要按工具要求准备环境、数据和必要的服务密钥；本机工具在电脑执行，服务器分析使用你选定的 SSH 服务器。具体支持范围和来源可在工具详情查看。'],
  resources: ['服务器资源', '这里是整台服务器的实时使用情况，不是本次任务的配额。', 'CPU、内存和磁盘数据来自当前服务器的最近一次查询，其他用户的任务也会影响这些数值。任务可用多少资源，在提交计划时单独设置；资源概览不会为你预留资源。遇到旧数据可刷新，连接失败时请查看页面给出的原因。'],
  cpu: ['CPU 使用率', '表示整台服务器正在忙于计算的比例。', '占用率越高，可用于新任务的余量通常越小。逻辑核数是系统可调度的线程数，可能多于物理核数；任务设置中的 CPU 数是该任务的使用上限，不表示独占这些核心。'],
  memory: ['运行内存', '分析运行时存放数据的空间，和磁盘容量不同。', '大矩阵、组装和比对等任务可能需要较多内存。可用内存会随服务器上的任务变化；不要把磁盘剩余空间当成可用内存。任务超出设定的内存上限时可能停止。'],
  swap: ['交换空间', '系统用磁盘暂存部分内存内容，速度通常慢于运行内存。', '交换空间已有占用，不一定表示服务器当前缺内存，也可能是先前任务留下的状态。判断能否运行分析，要结合当前可用内存、CPU 和任务情况；不要把交换空间当作同等大小的运行内存配额。'],
  quota: ['本次计算额度', '同一份额度供所选步骤依次使用，不按步骤数量累加。', 'CPU 和内存是当前步骤及其子程序合计的使用上限，不会预留整台服务器。默认 20 核、50 GiB、24 小时，可按数据大小和服务器容量调整。时间上限用于全部步骤累计执行；排队另有同样的等待时限，二者分别计时。'],
} as const;

const EN: Record<keyof typeof HELP, readonly [string, string, string]> = {
  model: ['Conversation service', 'Use the company-provided service after signing in.', 'No personal API key is needed. Contact your administrator if access is unavailable.'],
  image: ['Image service', 'Connect an image model to create illustrations in conversation.', 'Select a provider and enter its endpoint, image model and key. Save, add the relevant image tool, then ask for an illustration in chat. Data plots such as heatmaps do not require this service. Prompts and supplied material are processed by the provider, whose charges apply.'],
  mcp: ['External tool connections', 'Connect a reference library, search service or another application to chat.', 'MCP connects external tools to OMaaX. A preset is a starting configuration, not an installed or verified service. Configure it, save and restart the assistant. Local services run on this computer and may download dependencies on first use; address-based connections use an existing service. This is separate from SSH analysis. Access and data handling depend on that service.'],
  env: ['Local analysis environment', 'Prepare software on this computer; SSH server environments are separate.', 'The base environment contains Python and common single-cell packages in an OMaaX-specific directory, without replacing system Python. Install it for local analyses that need it. Prepare SSH environments in Server Management or the workflow. Extensions are added only when selected, not with the entire community catalog.'],
  tools: ['Using tools', 'Add the tools you need, then describe the analysis in chat.', 'Adding a tool fetches its guide and bundled files, not every software dependency. Browsing does not download the entire catalog. Prepare its environment, data and any service keys before running. Local tools run on your computer; server analyses use the selected SSH server. Details retain each tool’s requirements and source.'],
  resources: ['Server resources', 'Recent usage across the entire server, not the allowance for this task.', 'Usage includes other users’ tasks and reflects the latest successful query. Set a task’s allowance separately when preparing a plan. This overview does not reserve resources. Refresh older readings; connection failures show their cause on the page.'],
  cpu: ['CPU usage', 'The share of server processing capacity currently in use.', 'Higher usage usually leaves less room for new tasks. Logical cores are schedulable threads and may outnumber physical cores. A task’s CPU setting limits its usage; it does not grant exclusive cores.'],
  memory: ['Memory', 'Space used to hold data while an analysis runs, separate from disk storage.', 'Large matrices, assemblies and alignments can need substantial memory. Availability changes with server workload. Free disk space is not available RAM. A task exceeding its memory limit may stop.'],
  swap: ['Swap', 'Disk space used for some memory contents; usually slower than RAM.', 'Used swap may reflect earlier workloads, not a current memory shortage. Consider available RAM, CPU usage and active tasks together. Swap is not equivalent to the same amount of RAM for task allocation.'],
  quota: ['Task allowance', 'The selected steps share these limits and run in sequence.', 'CPU and memory limit each current step and its subprocesses without reserving the server. Defaults are 20 cores, 50 GiB and 24 hours; adjust for data size and capacity. Runtime is cumulative across steps. Queue waiting has the same limit but is timed separately.'],
};

export function useHelp(topic: keyof typeof HELP) {
  const { lang } = useT();
  const [title, summary, detail] = lang === 'en' ? EN[topic] : HELP[topic];
  return { title, summary, detail };
}

export default function HelpTip({ topic, text, title, align = 'left' }: { topic?: keyof typeof HELP; text?: string; title?: string; align?: 'left' | 'right' }) {
  const { lang, t } = useT();
  const copy = useHelp(topic || 'tools');
  const heading = (title && t(title)) || (topic ? copy.title : lang === 'en' ? 'Help' : '使用说明');
  return <details className={`omaax-help${align === 'right' ? ' align-right' : ''}`} onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape') { e.currentTarget.open = false; e.currentTarget.querySelector('summary')?.focus(); e.stopPropagation(); } }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) e.currentTarget.open = false; }}>
    <summary aria-label={`${lang === 'en' ? 'Help: ' : '查看说明：'}${heading}`}><Info size={13} /><span>{lang === 'en' ? 'Help' : '说明'}</span></summary>
    <span className="omaax-help-content" role="note"><strong>{heading}</strong>{topic && <span className="omaax-help-lead">{copy.summary}</span>}<span>{text ? t(text) : copy.detail}</span></span>
  </details>;
}
