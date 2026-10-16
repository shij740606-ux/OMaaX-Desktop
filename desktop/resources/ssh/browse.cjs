const path = require('node:path').posix;
const invoke = (sftp, name, ...args) => new Promise((resolve, reject) => sftp[name](...args, (error, value) => error ? reject(error) : resolve(value)));

module.exports = async function browse(client, request) {
  const sftp = await new Promise((resolve, reject) => client.sftp((error, value) => error ? reject(error) : resolve(value)));
  let handle;
  try {
    const home = await invoke(sftp, 'realpath', '.');
    const given = request.path || home;
    const destination = given === '~' ? home : given.startsWith('~/') ? path.join(home, given.slice(2)) : path.isAbsolute(given) ? given : path.join(home, given);
    const canonical = await invoke(sftp, 'realpath', destination);
    const info = await invoke(sftp, 'stat', canonical);
    if (!info.isDirectory()) throw Error('该路径不是文件夹，请选择其所在目录');
    handle = await invoke(sftp, 'opendir', canonical);
    const offset = request.offset || 0, limit = request.limit || 200, files = [];
    let seen = 0, more = false;
    while (!more) {
      const rows = await invoke(sftp, 'readdir', handle).catch(error=>{if(error.code===1)return false;throw error;});
      if (rows === false || !rows.length) break;
      for (const row of rows) {
        const name = row.filename;
        if (name === '.' || name === '..' || !request.showHidden && name.startsWith('.')) continue;
        if (seen++ < offset) continue;
        if (files.length === limit) { more = true; break; }
        files.push({ name, path:path.join(canonical,name), directory:row.attrs.isDirectory(), link:row.attrs.isSymbolicLink(), size:row.attrs.size, modified:row.attrs.mtime, permissions:(row.attrs.mode & 0o777).toString(8).padStart(3,'0') });
      }
    }
    return {path:canonical, home, parent:canonical==='/'?null:path.dirname(canonical), files, nextOffset:more?offset+files.length:null};
  } finally {
    if (handle) await invoke(sftp, 'close', handle).catch(()=>{});
    sftp.end();
  }
};
