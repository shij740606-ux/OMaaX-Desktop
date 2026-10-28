import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const marker = '/*OMAAX_STALE_LOCAL_COOKIES_V1*/';
const netImport = 'import { connect as omaaxCookiePortConnect } from "node:net";\n';

// Only a refused local TCP connection proves that an old authority is no longer listening.
export const cookieCleanupSource = `${marker}
function omaaxCookiePortClosed(port) {
  return new Promise(resolve => {
    let socket, settled = false;
    const finish = closed => {
      if (settled) return;
      settled = true;
      if (socket) socket.destroy();
      resolve(closed);
    };
    try {
      socket = omaaxCookiePortConnect({host: "127.0.0.1", port});
      socket.once("connect", () => finish(false));
      socket.once("error", error => finish(error.code === "ECONNREFUSED"));
      socket.setTimeout(100, () => finish(false));
    } catch { finish(false); }
  });
}
async function omaaxExpiredLocalCookies(rawCookie, currentName) {
  if (typeof rawCookie !== "string") return [];
  const cookies = new Map();
  for (const segment of rawCookie.split(";")) {
    const at = segment.indexOf("=");
    if (at < 0) continue;
    const name = segment.slice(0, at).trim();
    if (name.startsWith("dsh-auth-") && !cookies.has(name)) cookies.set(name, segment.slice(at + 1).trim());
  }
  if (cookies.size < 32) return [];
  const candidates = [];
  for (const [name, value] of cookies) {
    if (name === currentName) continue;
    try {
      const parts = value.split(".");
      if (parts.length !== 3 || parts[0] !== "v1" || parts[1].length > 2048) continue;
      const body = decodeBase64Url(parts[1]), signature = decodeBase64Url(parts[2]);
      if (!body || !signature || signature.byteLength !== 32) continue;
      const payload = JSON.parse(body.toString("utf8"));
      if (!isRecord(payload) || payload.version !== 1 || typeof payload.authority !== "string") continue;
      const match = /^127\\.0\\.0\\.1:([1-9]\\d{0,4})$/.exec(payload.authority);
      if (!match || Number(match[1]) > 65535 || cookieName(payload.authority) !== name) continue;
      candidates.push({name, port: Number(match[1])});
      if (candidates.length === 180) break;
    } catch { /* Malformed or unrelated cookies are never removed. */ }
  }
  const expired = await Promise.all(candidates.map(async ({name, port}) =>
    await omaaxCookiePortClosed(port) ? sessionCookie(name, "", 0, 0) : null));
  return expired.filter(value => value !== null);
}
`;

const originalExchange = `\t\t\t\tres.writeHead(303, {
\t\t\t\t\t"cache-control": "no-store",
\t\t\t\t\t"location": "/",
\t\t\t\t\t"referrer-policy": "no-referrer",
\t\t\t\t\t"set-cookie": sessionCookie(cookieName(authority), value, expiresAt, Math.floor(this.maxAgeMilliseconds / 1e3))
\t\t\t\t});
\t\t\t\tres.end();
\t\t\t\treturn false;`;

const patchedExchange = `\t\t\t\tconst currentCookieName = cookieName(authority);
\t\t\t\tconst currentCookie = sessionCookie(currentCookieName, value, expiresAt, Math.floor(this.maxAgeMilliseconds / 1e3));
\t\t\t\tconst finishExchange = expired => {
\t\t\t\t\tif (res.destroyed || res.writableEnded) return;
\t\t\t\t\tres.writeHead(303, {
\t\t\t\t\t\t"cache-control": "no-store",
\t\t\t\t\t\t"location": "/",
\t\t\t\t\t\t"referrer-policy": "no-referrer",
\t\t\t\t\t\t"set-cookie": [...expired, currentCookie]
\t\t\t\t\t});
\t\t\t\t\tres.end();
\t\t\t\t};
\t\t\t\tvoid omaaxExpiredLocalCookies(header(req.headers, "cookie"), currentCookieName).then(finishExchange, () => finishExchange([]));
\t\t\t\treturn false;`;

function once(source, before, after) {
  if (source.split(before).length !== 2) throw Error('Local cookie cleanup patch anchor changed; review the runtime before staging');
  return source.replace(before, after);
}

export function patchCookieCleanupSource(input) {
  const source = input.replace(/\r\n/g, '\n');
  // Recheck the authentication boundary even when this runtime was already patched.
  for (const contract of [
    'req.method === "GET" && url.pathname === "/" && tokens.length === 1 && authority !== void 0 && tokenMatches(tokens.join(""), this.launchToken)',
    'if (this.isAuthenticated(req)) return true;',
    'this.writeUnauthorized(req, res);',
  ]) if (!source.includes(contract)) throw Error('Browser authentication contract changed; review cookie cleanup before staging');
  if (source.split('var BrowserAuth = class BrowserAuth {').length !== 2) throw Error('Local cookie cleanup patch anchor changed');
  if (source.includes(marker)) {
    if (!source.includes(netImport) || !source.includes(cookieCleanupSource) || !source.includes(patchedExchange) || source.includes(originalExchange)) throw Error('Local cookie cleanup generated region changed');
    return source;
  }
  if (source.includes('omaaxExpiredLocalCookies') || source.includes('omaaxCookiePortConnect')) throw Error('Partial local cookie cleanup patch found');
  return netImport + once(once(source, originalExchange, patchedExchange), 'var BrowserAuth = class BrowserAuth {', cookieCleanupSource + 'var BrowserAuth = class BrowserAuth {');
}

export function patchDshCookies(runtimeDir = path.join(root, 'dsh-runtime')) {
  const candidates = [
    'node_modules/@deepseek-ai/dsh-client-connection/lib/index.js',
    'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js',
  ].map(file => path.join(path.resolve(runtimeDir), file)).filter(existsSync);
  if (!candidates.length) throw Error('Local browser authentication runtime missing; cannot stage cookie cleanup');
  const changes = candidates.map(file => [file, patchCookieCleanupSource(readFileSync(file, 'utf8'))]);
  for (const [file, source] of changes) writeFileSync(file, source);
  return candidates;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(`Local cookie cleanup ready (${patchDshCookies(process.argv[2]).length} runtime)`);
}
