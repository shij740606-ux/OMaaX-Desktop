export type ServerErrorScope='connection'|'environment'|'files'|'transfer'|'operation';

export function serverError(reason:unknown,scope:ServerErrorScope='operation'):string{
 const text=String(reason instanceof Error?reason.message:reason??'').replace(/^Error:\s*/,'').trim();
 const registry=/registry|docker(?:\.io|:|\/)|trying to pull|pull access|镜像.*(?:下载|仓库)|(?:下载|拉取).*镜像/i.test(text);
 if(/no space left|disk (?:is )?full|磁盘.*(?:已满|不足)|可用空间不足/i.test(text))return '服务器磁盘空间不足。请检查保存位置的剩余空间，清理不再需要的文件后重试。';
 if(/disk quota exceeded|quota.*exceeded|配额.*(?:不足|超出)/i.test(text))return '当前账号的存储配额不足，请联系管理员调整配额或改用其他保存位置。';
 if(/host.?key.*(?:fail|mismatch|changed)|指纹.*(?:变化|不匹配)|host denied/i.test(text))return '服务器主机指纹与已保存记录不一致。请先向管理员核对，再重新确认连接。';
 if(/authentication.*(?:fail|method)|all configured authentication|认证失败|密码验证失败|认证未通过|permission denied \(publickey/i.test(text))return 'SSH 认证未通过，请检查用户名、密码或私钥后重新连接。';
 if(registry){
  if(/manifest unknown|name unknown|not found|pull access denied|unauthorized|authentication required/i.test(text))return '基础运行镜像不可用或需要访问权限。请检查镜像地址及仓库权限，再重试环境准备；SSH 登录不受影响。';
  if(/certificate|x509|tls|ssl/i.test(text))return '连接镜像仓库时证书验证失败。请检查服务器时间、证书或网络代理配置后重试；SSH 登录不受影响。';
  return '服务器无法连接镜像仓库，基础环境尚未准备完成。请检查服务器的外网连接或镜像仓库访问配置后重试；SSH 登录和文件浏览不受影响。';
 }
 if(/timed out.*handshake|handshake.*(?:timed out|timeout)/i.test(text))return 'SSH 握手超时，尚未完成登录。请检查服务器地址、端口、网络及 SSH 服务是否正常。';
 if(/getaddrinfo|enotfound|eai_again|name or service not known|temporary failure in name resolution/i.test(text))return '无法解析服务器地址。请检查主机名和服务器的网络、域名解析配置。';
 if(/econnrefused|connection refused/i.test(text))return '服务器拒绝连接。请检查地址、端口是否正确，以及 SSH 服务和防火墙是否允许连接。';
 if(/no route to host|enetunreach|ehostunreach|network is unreachable/i.test(text))return '当前网络无法到达服务器。请检查网络连接及服务器地址后重试。';
 if(/系统策略.*隔离运行能力|未开放普通用户文件保护能力|Rootless Podman and cgroup|cgroup controllers unavailable/i.test(text))return '服务器系统未开放当前账号所需的隔离运行能力，请使用已配置环境的服务器或联系服务器管理员。';
 if(/sudo|管理员权限|系统安装权限/i.test(text))return '环境准备未完成，请重新配置用户环境；无需提供管理员密码。';
 if(/podman.*(?:not found|no such file|未安装)|尚未安装服务器运行组件/i.test(text))return '服务器缺少基础运行组件，请先到“环境管理”准备基础环境。';
 if(/permission denied|eacces|operation not permitted|无权|没有.*权限/i.test(text))return '当前账号没有完成此操作的权限。请检查目录或文件的访问权限，必要时联系管理员。';
 if(/no such file|enoent|not a directory|目录不存在|路径不存在/i.test(text))return '文件或目录不存在，或路径已发生变化。请检查路径后刷新重试。';
 if(/checksum mismatch|hash mismatch|校验.*(?:失败|不一致)/i.test(text))return '文件内容校验未通过，传输尚未确认完成。请检查源文件是否变化，再重试传输。';
 if(/connection.*(?:lost|closed|reset)|econnreset|socket hang up|连接意外断开/i.test(text))return '与服务器的连接已中断。请重新连接并刷新状态；已提交的任务请先核对进度，避免重复提交。';
 if(/timed? ?out|timeout|超时/i.test(text))return scope==='environment'?'环境准备或检查超时。请稍后刷新进度或重试；SSH 登录不受影响。':scope==='transfer'?'数据传输超时。请检查两端连接并查看传输记录，再从中断处重试。':'服务器响应超时，请检查网络或稍后重试。已提交的任务请先刷新核对，避免重复提交。';
 if(/database is locked|database.*busy/i.test(text))return '服务器正在更新任务记录，请稍后刷新重试。';
 if(/invalid.*(?:json|response)|无效响应|返回.*(?:无效|格式不正确)/i.test(text))return '服务器返回的信息不完整或格式不正确，请刷新重试；若持续出现，请保留日志以便排查。';
 // Keep short, readable business validation messages; never put a traceback or command log in the main UI.
 if(/[\u4e00-\u9fff]/.test(text)&&text.length<=300&&!/[\r\n]|Traceback|Command \[|level=|msg=|Exception:|errno|exitCode|^\s*[{[]/.test(text))return text;
 return scope==='environment'?'服务器环境准备未完成，请查看安装日志，确认所需组件和网络可用后重试。':scope==='files'?'服务器文件操作未完成，请检查路径和账号权限后重试。':scope==='transfer'?'数据传输未完成，请查看传输记录并重试，原文件不会被删除。':scope==='connection'?'服务器连接未完成，请检查连接信息和网络后重试。':'服务器操作未完成，请刷新状态后重试；若持续出现，请保留日志以便排查。';
}
