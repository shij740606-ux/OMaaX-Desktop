const { Client } = require('ssh2');
const { createHash } = require('node:crypto');
const { StringDecoder } = require('node:string_decoder');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { input += chunk; if (Buffer.byteLength(input, 'utf8') > 8 * 1048576) { finish({error:'SSH 请求超过 8 MB'}); process.exit(1); } });
process.stdin.on('end', () => {
  let request;
  try { request = JSON.parse(input); } catch { return finish({ error: 'SSH 请求无效' }); }
  input = '';
  if (!['probe','test','execute','files'].includes(request.operation)) return finish({error:'不支持的 SSH 操作'});
  if (request.operation === 'files' && (request.path!==undefined && (typeof request.path!=='string'||request.path.length>4096||request.path.includes('\0')) || request.offset!==undefined && (!Number.isInteger(request.offset)||request.offset<0||request.offset>1000000) || request.limit!==undefined && (!Number.isInteger(request.limit)||request.limit<1||request.limit>200) || request.showHidden!==undefined && typeof request.showHidden!=='boolean')) return finish({error:'目录查询参数不正确'});
  if (request.operation === 'execute' && typeof request.command !== 'string') return finish({error:'SSH 执行内容无效'});
  if (request.operation === 'execute' && (Buffer.byteLength(request.command, 'utf8') > 6 * 1048576 || request.command.includes('\0'))) return finish({error:'SSH 执行内容超过限制或包含无效字符'});
  const connection = new Client();
  let fingerprint = '', hostKey = '', finished = false, phase='连接服务器';
  const timer = setTimeout(() => done({ error: 'SSH 操作超时；远程命令是否停止需另行确认' }), Math.min(900, Math.max(5, request.timeoutSeconds || 30)) * 1000);
  function done(value) {
    if (finished) return;
    finished = true; clearTimeout(timer); connection.destroy(); finish(value);
  }
  connection.on('error', error => done({ error: request.operation === 'probe' && hostKey ? undefined : (error.level === 'client-authentication' ? 'SSH 认证失败，请检查用户名、密码或私钥' : error.message), phase, fingerprint, hostKey }));
  connection.on('close',()=>{if(!finished)done({error:`SSH 连接意外断开（${phase}）；已提交任务请刷新核对，不要重复提交`,phase});});
  connection.on('ready', async () => {
    phase='执行操作';
    if (request.operation === 'files') {
      phase='读取目录';
      require('./browse.cjs')(connection,request).then(done,error=>done({error:error.code===3?'当前账号无权读取该目录':error.code===2?'目录不存在，请检查路径':String(error.message||error),phase}));
      return;
    }
    if (request.operation !== 'execute') return done({ fingerprint, hostKey, connected: true });
    const command = request.command;
    // OpenSSH passes exec text as one shell argument, limited to ~128 KiB on Linux.
    const streamed = Buffer.byteLength(command, 'utf8') > 65536;
    connection.exec(streamed ? '/bin/bash -s' : command, (error, stream) => {
      if (error) return done({ error: error.message });
      let stdout = '', stderr = '', total = 0;
      const outDecoder = new StringDecoder('utf8'), errDecoder = new StringDecoder('utf8');
      function collect(chunk, target) {
        total += chunk.length;
        if (total > 1048576) return done({ error: '输出超过 1 MB，请将结果写入服务器文件后分段查看' });
        if (target === 'stdout') stdout += outDecoder.write(chunk); else stderr += errDecoder.write(chunk);
      }
      stream.on('data', chunk => collect(chunk, 'stdout'));
      stream.stderr.on('data', chunk => collect(chunk, 'stderr'));
      stream.on('error', error => done({ error: error.message }));
      stream.on('close', (code, signal) => done({ stdout: stdout + outDecoder.end(), stderr: stderr + errDecoder.end(), exitCode: code ?? null, signal: signal ?? null }));
      if (streamed) { stream.end(command + '\n'); request.command = ''; }
    });
  });
  try {
    connection.connect({ host: request.host, port: request.port, username: request.username,
      password: request.operation === 'probe' ? undefined : request.password,
      privateKey: request.operation === 'probe' ? undefined : request.privateKey,
      passphrase: request.passphrase, readyTimeout: 15000, keepaliveInterval:15000, keepaliveCountMax:3,
      hostVerifier: key => {
        hostKey = key.toString('base64');
        fingerprint = 'SHA256:' + createHash('sha256').update(key).digest('base64').replace(/=+$/, '');
        if (request.operation === 'probe') { setImmediate(() => done({ fingerprint, hostKey })); return false; }
        return request.hostKey === hostKey;
      },
    });
  } catch (error) { done({ error: error.message }); }
});
function finish(value) { process.stdout.write(JSON.stringify(value)); }
