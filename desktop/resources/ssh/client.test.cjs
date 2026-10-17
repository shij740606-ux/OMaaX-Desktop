const assert = require('node:assert/strict');
const { generateKeyPairSync } = require('node:crypto');
const { spawn } = require('node:child_process');
const { Server, utils } = require('ssh2');
const path = require('node:path');
const key = generateKeyPairSync('rsa', { modulusLength: 2048 }).privateKey.export({ type: 'pkcs1', format: 'pem' });
const server = new Server({ hostKeys: [key] }, connection => {
  connection.on('error', () => {});
  connection.on('authentication', context => {
    if (context.username !== 'test') return context.reject();
    if (context.method === 'password' && context.password === 'test') return context.accept();
    const parsed = utils.parseKey(key);
    if (context.method === 'publickey' && context.key.data.equals(parsed.getPublicSSH()) && (!context.signature || parsed.verify(context.blob, context.signature, context.hashAlgo))) return context.accept();
    context.reject();
  });
  connection.on('ready', () => connection.on('session', accept => accept().on('exec', (accept, reject, info) => {
    if(info.command==='disconnect'){accept();connection.destroy();return;}
    if(info.command==='timeout'){accept();return;}
    if (info.command === '/bin/bash -s') {
      const stream=accept();let text='';stream.on('data',chunk=>text+=chunk);
      stream.on('end',()=>{stream.write(JSON.stringify({bytes:Buffer.byteLength(text),text}));stream.exit(0);stream.end();});
      return;
    }
    if(info.command.startsWith('python3 -c')){const stream=accept();stream.exit(1);stream.end();return;}
    if (info.command.startsWith('sudo ')) {
      assert.equal(info.command.includes('private-sudo'),false);
      assert.equal(info.command.includes('UNTRUSTED_COMMAND'),false);
      assert.ok(info.command.includes('apt-get install -y python3 podman uidmap slirp4netns fuse-overlayfs'));
      const stream=accept();let password='';stream.on('data',chunk=>password+=chunk);
      stream.on('end',()=>{stream.write(password);stream.stderr.write(password);stream.exit(password==='private-sudo-$(echo nope)\n'?0:1);stream.end();});
      return;
    }
    const stream = accept(); stream.write('hello'); stream.stderr.write('note'); stream.exit(0); stream.end();
  })));
});
function request(value) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(__dirname, 'client.cjs')]);
    let output = ''; child.stdout.on('data', chunk => { output += chunk; });
    child.on('error', reject); child.on('close', () => { try { resolve(JSON.parse(output)); } catch (error) { reject(error); } });
    child.stdin.end(JSON.stringify({ host: '127.0.0.1', port: server.address().port, username: 'test', password: 'test', timeoutSeconds: 5, ...value }));
  });
}
server.listen(0, '127.0.0.1', async () => {
  try {
    const probe = await request({ operation: 'probe' });
    assert.match(probe.fingerprint, /^SHA256:/); assert.ok(probe.hostKey);
    assert.ok((await request({ operation: 'execute', command: 'hello', hostKey: 'wrong' })).error);
    assert.ok((await request({ operation: 'test', hostKey: probe.hostKey, password: 'wrong' })).error);
    const result = await request({ operation: 'execute', command: 'hello', hostKey: probe.hostKey });
    assert.equal(result.stdout, 'hello'); assert.equal(result.stderr, 'note'); assert.equal(result.exitCode, 0);
    assert.match((await request({operation:'execute',command:'hello',hostKey:probe.hostKey,accountOwner:'a'.repeat(64)+':761f059b-1111-4111-8111-123456789abc'})).error,/独立账号/);
    const keyResult = await request({ operation: 'execute', command: 'hello', hostKey: probe.hostKey, password: undefined, privateKey: key });
    assert.equal(keyResult.exitCode, 0);
    const longCommand='# trusted source\n'+ 'x'.repeat(500000)+'\n# quotes: \" \' ` $() end';
    const longResult=await request({operation:'execute',command:longCommand,hostKey:probe.hostKey});
    assert.equal(longResult.exitCode,0);assert.equal(JSON.parse(longResult.stdout).text,longCommand+'\n');
    assert.ok((await request({operation:'execute',command:null,hostKey:probe.hostKey})).error);
    const install = await request({operation:'install_environment',hostKey:probe.hostKey,sudoPassword:'private-sudo-$(echo nope)',command:'UNTRUSTED_COMMAND'});
    assert.equal(install.exitCode,0);assert.equal(install.stdout,'');assert.equal(install.stderr,'');
    assert.equal((await request({operation:'install_environment',hostKey:probe.hostKey,sudoPassword:'wrong'})).exitCode,1);
    assert.ok((await request({operation:'install_environment',hostKey:probe.hostKey,sudoPassword:'bad\npassword'})).error);
    assert.ok((await request({operation:'install_environment',hostKey:'wrong',sudoPassword:'private-sudo'})).error);
    assert.ok((await request({operation:'execute',command:'disconnect',hostKey:probe.hostKey})).error);
    assert.match((await request({operation:'execute',command:'timeout',hostKey:probe.hostKey})).error,/超时/);
    console.log('SSH probe, host-key rejection, authentication, execution and sudo installation stdin/redaction/failure checks passed');
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally { server.close(); }
});
