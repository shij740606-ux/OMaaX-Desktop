// Run the pinned upstream server with product-owned launch settings only.
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export function browserExecutable(platform = process.platform, env = process.env) {
  const candidates = platform === 'win32' ? [
    path.join(env['ProgramFiles(x86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe'),
    path.join(env.ProgramFiles || 'C:/Program Files', 'Microsoft/Edge/Application/msedge.exe'),
    path.join(env.LOCALAPPDATA || '', 'Microsoft/Edge/Application/msedge.exe'),
    path.join(env.ProgramFiles || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
  ] : platform === 'darwin' ? [
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ] : ['/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  const found = candidates.find(candidate => path.isAbsolute(candidate) && existsSync(candidate));
  if (!found) throw Error('Install Microsoft Edge or Google Chrome, then retry browser access. OMaaX does not download a browser automatically.');
  return found;
}

export function launchArguments(output, headless = false, executable = browserExecutable()) {
  if (!path.isAbsolute(output)) throw Error('Browser output must be an absolute project directory.');
  return ['--browser', 'chromium', '--executable-path', executable, '--isolated',
    '--output-dir', output, '--output-max-size', '0', '--image-responses', 'allow', '--timeout-navigation', '60000',
    '--timeout-action', '10000', ...(headless ? ['--headless'] : [])];
}

export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 2 || !['visible', 'headless'].includes(argv[1])) throw Error('Invalid managed browser launch.');
  // No inherited endpoint, extension, storage-state, proxy or profile may attach a personal browser.
  for (const key of Object.keys(process.env)) {
    if (/^PLAYWRIGHT_MCP_/i.test(key) || /^(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NODE_OPTIONS)$/i.test(key)) delete process.env[key];
  }
  process.env.PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1';
  const cli = fileURLToPath(new URL('./node_modules/@playwright/mcp/cli.js', import.meta.url));
  process.argv = [process.execPath, cli, ...launchArguments(argv[0], argv[1] === 'headless')];
  await import(pathToFileURL(cli).href);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main().catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
