import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const manifestTarget = 'installation/payload-manifest.json';

// The NSIS bundler patches this fixed-width marker after beforeBundleCommand.
export function nsisExecutableBytes(bytes) {
  const from = Buffer.from('__TAURI_BUNDLE_TYPE_VAR_UNK');
  const to = Buffer.from('__TAURI_BUNDLE_TYPE_VAR_NSS');
  const result = Buffer.from(bytes);
  const offset = result.indexOf(from);
  if (offset < 0 || result.indexOf(from, offset + 1) >= 0) {
    throw new Error('Expected exactly one Tauri bundle marker in the release executable');
  }
  to.copy(result, offset);
  return result;
}

export function payloadManifest({ version, executable, configDir, resources }) {
  const files = [];
  const seen = new Set();
  function add(source, relative, main = false) {
    const dest = relative.replaceAll('\\', '/');
    if (!dest || dest.startsWith('/') || dest.split('/').some(p => !p || p === '..' || p === '.') || /[:\x00-\x1f]/.test(dest)) {
      throw new Error(`Unsafe payload destination: ${relative}`);
    }
    const stat = lstatSync(source);
    if (stat.isSymbolicLink()) throw new Error(`Payload links must be resolved before bundling: ${source}`);
    if (stat.isDirectory()) {
      for (const entry of readdirSync(source).sort()) add(path.join(source, entry), `${dest}/${entry}`);
      return;
    }
    if (!stat.isFile()) throw new Error(`Unsupported payload entry: ${source}`);
    if (seen.has(dest.toLowerCase())) throw new Error(`Duplicate payload destination: ${dest}`);
    seen.add(dest.toLowerCase());
    const raw = readFileSync(source);
    const content = main ? nsisExecutableBytes(raw) : raw;
    files.push({ path: dest, bytes: content.length, sha256: createHash('sha256').update(content).digest('hex') });
  }
  add(executable, 'OMaaX.exe', true);
  for (const [source, destination] of Object.entries(resources)) {
    if (destination === manifestTarget) continue; // A manifest cannot hash itself.
    add(path.resolve(configDir, source), destination);
  }
  files.sort((a, b) => a.path.localeCompare(b.path, 'en'));
  return { schemaVersion: 1, version, files };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes('--prepare')) {
    if (process.platform === 'win32') {
      mkdirSync(path.join(desktop, 'installer-payload'), { recursive: true });
      // Tauri checks resource paths while compiling. This deliberately invalid
      // placeholder must be replaced after compilation, before NSIS bundling.
      writeFileSync(path.join(desktop, 'installer-payload/manifest.json'), JSON.stringify({ state: 'pending-build' }));
    }
    process.exit(0);
  }
  const configDir = path.join(desktop, 'src-tauri');
  const base = JSON.parse(readFileSync(path.join(configDir, 'tauri.conf.json')));
  const windows = JSON.parse(readFileSync(path.join(configDir, 'tauri.windows.conf.json')));
  const pkg = JSON.parse(readFileSync(path.join(desktop, 'package.json')));
  if (base.version !== pkg.version) throw new Error('Package and Tauri versions differ');
  const executable = path.join(configDir, 'target/release/OMaaX.exe');
  const executableVersion = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
    '[Diagnostics.FileVersionInfo]::GetVersionInfo($env:OMAAX_PAYLOAD_EXE).ProductVersion'], {
    encoding: 'utf8', windowsHide: true, env: { ...process.env, OMAAX_PAYLOAD_EXE: executable },
  }).trim();
  if (executableVersion !== base.version) throw new Error(`Executable version ${executableVersion} differs from release ${base.version}`);
  const manifest = payloadManifest({
    version: base.version,
    executable,
    configDir,
    resources: { ...base.bundle.resources, ...windows.bundle.resources },
  });
  mkdirSync(path.join(desktop, 'installer-payload'), { recursive: true });
  writeFileSync(path.join(desktop, 'installer-payload/manifest.json'), JSON.stringify(manifest));
  console.log(`Installation manifest: ${manifest.files.length} files, version ${manifest.version}`);
}
