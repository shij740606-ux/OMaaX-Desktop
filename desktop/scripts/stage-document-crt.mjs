// App-local deployment of the official release CRT. Never copy system DLLs.
import { copyFileSync, existsSync, readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

function directories(parent) {
  try { return readdirSync(parent, { withFileTypes: true }).filter(item => item.isDirectory()).map(item => path.join(parent, item.name)); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}

export function officialCrtSources(env = process.env) {
  const installs = [env.VSINSTALLDIR].filter(Boolean);
  for (const base of new Set([env['ProgramFiles(x86)'], env.ProgramFiles].filter(Boolean))) {
    installs.push(...directories(path.join(base, 'Microsoft Visual Studio', '2022')));
  }
  return [...new Set([
    env.OMAAX_VC_REDIST_DIR,
    ...installs.flatMap(install => directories(path.join(install, 'VC', 'Redist', 'MSVC')).map(version => path.join(version, 'x64', 'Microsoft.VC143.CRT'))),
  ].filter(Boolean))];
}

function verifiedFiles(directory, lock) {
  return Object.entries(lock.files).every(([name, expected]) => {
    try {
      const bytes = readFileSync(path.join(directory, name));
      return bytes.length === expected.bytes && createHash('sha256').update(bytes).digest('hex') === expected.sha256;
    } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  });
}

export function stageDocumentCrt({ runtime, checkOnly = false, platform = process.platform, arch = process.arch, env = process.env, sourceDirectories } = {}) {
  if (platform !== 'win32') return { required: false };
  const lock = JSON.parse(readFileSync(path.join(runtime, 'windows-crt.lock.json'), 'utf8'));
  if (lock.platform !== `${platform}-${arch}`) throw new Error(`Document CRT deployment is not locked for ${platform}-${arch}.`);
  const destination = path.join(runtime, lock.helperDirectory);
  if (!existsSync(path.join(destination, 'libreoffice-kit.exe'))) throw new Error('Stage the document engine before deploying its Windows runtime.');
  const result = { required: true, version: lock.fileVersion, files: Object.keys(lock.files).length, directory: destination };
  // Installed applications and already staged build trees need no Visual Studio.
  if (verifiedFiles(destination, lock)) return { ...result, staged: false };
  if (checkOnly) throw new Error('The bundled document CRT is missing or differs from windows-crt.lock.json. Run stage-documents.mjs to repair it before packaging.');
  const sources = sourceDirectories ?? officialCrtSources(env);
  const source = sources.find(directory =>
    /[\\/]VC[\\/]Redist[\\/]MSVC[\\/][^\\/]+[\\/]x64[\\/]Microsoft\.VC143\.CRT[\\/]?$/i.test(directory)
    && verifiedFiles(directory, lock));
  if (!source) throw new Error(`The document converter needs the official Visual Studio x64 release CRT ${lock.fileVersion}. Install the matching VC Redist build files or set OMAAX_VC_REDIST_DIR to its VC/Redist/MSVC/<version>/x64/Microsoft.VC143.CRT directory. Do not copy DLLs from Windows/System32. Already bundled, verified files do not need Visual Studio.`);
  for (const name of Object.keys(lock.files)) copyFileSync(path.join(source, name), path.join(destination, name));
  if (!verifiedFiles(destination, lock)) throw new Error('Document CRT verification failed after staging.');
  return { ...result, staged: true };
}
