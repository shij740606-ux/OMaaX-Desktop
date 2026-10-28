// Preserve company error categories through the SDK, retry policy and localized UI.
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const companyErrors = {
  auth_session_expired: ['登录已过期，请重新登录后继续。', 'Your sign-in has expired. Sign in again to continue.'],
  auth_session_changed: ['登录账号已变化，请切回原账号或重新打开对话。', 'The signed-in account has changed. Switch back or reopen the chat.'],
  auth_forbidden: ['当前账号没有此功能权限，请联系管理员。', 'Your account does not have permission for this feature. Contact your administrator.'],
  auth_network_error: ['无法连接登录服务，请检查网络后重试。', 'Cannot reach the sign-in service. Check your connection and try again.', 'SERVER'],
  auth_timeout: ['登录校验超时，请稍后重试。', 'Sign-in verification timed out. Try again shortly.', 'TIMEOUT'],
  auth_service_unavailable: ['登录服务暂时异常，请稍后重试。', 'The sign-in service is temporarily unavailable. Try again shortly.', 'SERVER'],
  auth_invalid_response: ['登录服务返回异常，请稍后重试或联系管理员。', 'The sign-in service returned an invalid response. Try again or contact your administrator.', 'SERVER'],
  auth_local_error: ['本机无法读取登录状态，请重新打开软件；若仍失败，请联系支持人员。', 'The app cannot read your local sign-in state. Reopen it; if this continues, contact support.'],
  auth_helper_timeout: ['本机登录校验超时，请稍后重试。', 'Local sign-in verification timed out. Try again shortly.', 'TIMEOUT'],
  auth_helper_start_failed: ['本机登录校验组件无法启动，请检查软件是否被安全软件拦截。', 'The local sign-in component could not start. Check whether security software is blocking the app.'],
  auth_helper_invalid_response: ['本机登录校验组件返回异常，请重新打开软件或联系支持人员。', 'The local sign-in component returned an invalid response. Reopen the app or contact support.'],
  local_bridge_auth_error: ['本机对话连接校验失败，请重新打开软件。', 'The local chat connection could not be verified. Reopen the app.'],
  credential_network_error: ['无法连接模型授权服务，请检查网络后重试。', 'Cannot reach the model access service. Check your connection and try again.', 'SERVER'],
  credential_timeout: ['获取模型授权超时，请稍后重试。', 'The model access request timed out. Try again shortly.', 'TIMEOUT'],
  credential_service_unavailable: ['模型授权服务暂时异常，请稍后重试。', 'The model access service is temporarily unavailable. Try again shortly.', 'SERVER'],
  credential_invalid_response: ['模型授权服务返回异常，请稍后重试或联系管理员。', 'The model access service returned an invalid response. Try again or contact your administrator.', 'SERVER'],
  credential_rate_limited: ['模型授权请求过于频繁，请稍后重试。', 'Too many model access requests. Try again shortly.', 'RATE_LIMIT'],
  model_credential_rejected: ['模型服务拒绝了临时凭据，自动刷新后仍未恢复，请联系管理员。', 'The model service rejected the temporary credential even after refresh. Contact your administrator.'],
  model_access_denied: ['当前账号未获模型授权，或授权已被取消，请联系管理员。', 'Your account has no model access, or access was revoked. Contact your administrator.'],
  model_rate_limited: ['模型服务请求过于频繁，请稍后重试。', 'Too many requests to the model service. Try again shortly.', 'RATE_LIMIT'],
  model_service_unavailable: ['模型服务暂时异常，请稍后重试。', 'The model service is temporarily unavailable. Try again shortly.', 'SERVER'],
  model_request_invalid: ['模型服务无法处理本次请求，请联系支持人员并提供错误记录。', 'The model service could not process this request. Contact support with the error record.'],
  model_network_error: ['模型连接中断，请检查网络后重试。', 'The model connection was interrupted. Check your connection and try again.', 'TRANSPORT'],
  model_timeout: ['等待模型响应超时，请稍后重试。', 'The model response timed out. Try again shortly.', 'TIMEOUT'],
  model_cancelled: ['请求已取消。', 'The request was cancelled.'],
  model_invalid_response: ['模型服务返回异常，请稍后重试或联系管理员。', 'The model service returned an invalid response. Try again or contact your administrator.', 'SERVER'],
};
const retryClasses = Object.fromEntries(Object.entries(companyErrors).filter(([,v]) => v[2]).map(([k,v]) => [k,v[2]]));
const internalErrors = {
  component_load_failed: ['界面组件未能加载，请重新加载页面；若仍失败，请重启智能体。', 'The interface could not load. Reload the page; if it still fails, restart the agent.'],
  internal_auth_error: ['身份校验未通过，请检查登录状态和账号授权。', 'Authentication failed. Check your sign-in status and account permissions.'],
  internal_operation_failed: ['操作未完成，请重试；若仍失败，请重新打开软件或联系支持人员。', 'The operation could not be completed. Retry; if it still fails, reopen the app or contact support.'],
};

// Only error renderers call this classifier. Keep the original diagnostic and user content untouched.
export function internalErrorCode(message, code) {
  const text = `${typeof message === 'string' ? message : ''} ${typeof code === 'string' ? code : ''}`;
  if (!/deepseek|harness|\bdsh-[\w-]+|failed to import loader entry|client-modules:|[\\/]plugins(?:[\\/?])|bundle script[^\n]*failed to load/i.test(text)) return null;
  if (code === 'AUTH') return null;
  if (/failed to (?:load plugins|import loader entry)|client-modules:|[\\/]plugins(?:[\\/?])|bundle script|\bdsh-client-/i.test(text)) return 'component_load_failed';
  if (code === 'TIMEOUT' || /\btimeout\b|timed out|ETIMEDOUT|超时/i.test(text)) return 'model_timeout';
  if (code === 'RATE_LIMIT' || /\b429\b|rate[ _-]?limit|too many requests|限流|请求过于频繁/i.test(text)) return 'model_rate_limited';
  if (/\b40[13]\b|unauthorized|forbidden|invalid.{0,12}(?:api[ _-]?)?key|身份校验|登录过期/i.test(text)) return 'internal_auth_error';
  if (code === 'TRANSPORT' || /network|fetch failed|ECONN(?:REFUSED|RESET)|ENOTFOUND|EAI_AGAIN|连接中断|网络/i.test(text)) return 'model_network_error';
  if (code === 'SERVER' || /\b50[0234]\b|service unavailable|server error/i.test(text)) return 'model_service_unavailable';
  if (code === 'CANCELLED' || /\bcancelled\b|\bcanceled\b|\baborted\b|已取消/i.test(text)) return 'model_cancelled';
  return 'internal_operation_failed';
}

function patchInternalErrorUi(source, kind) {
  const prefix = kind === 'chat' ? 'message.failure.company.' : 'details.failure.company.';
  const marker = `/*OMAAX_INTERNAL_ERROR_${kind.toUpperCase()}_V1*/`;
  const args = kind === 'chat' ? 'message, code' : 'request.error, request.errorCode';
  const anchor = kind === 'chat' ? '\t\t\treturn code === "AUTH" ? t("message.failure.auth") : message;' : '\t\t\treturn request.error;';
  const inserted = `\t\t\t${marker}\n\t\t\tconst publicCode = (${internalErrorCode.toString()})(${args});\n\t\t\tif (publicCode) return t(${JSON.stringify(prefix)} + publicCode);\n`;
  if (source.includes(marker)) {
    if (!source.includes(inserted)) throw Error('Internal error UI generated region changed');
  } else source = once(source, anchor, inserted + anchor);
  for (const [locale,index] of [['zh',0],['en',1]]) {
    const start = source.indexOf(`const ${locale} = {`), end = source.indexOf('\n\t\t};',start);
    if (start < 0 || end < 0) throw Error('Internal error UI locale region changed');
    let region = source.slice(start,end);
    for (const [code,labels] of Object.entries(internalErrors)) {
      const key = JSON.stringify(prefix + code);
      if (!region.includes(key + ':')) region = region.replace(`const ${locale} = {`, `const ${locale} = {\n\t\t\t${key}: ${JSON.stringify(labels[index])},`);
    }
    source = source.slice(0,start) + region + source.slice(end);
  }
  return source;
}

function once(source, before, after) {
  if (source.split(before).length !== 2) throw new Error(`Company error patch anchor changed: ${before}`);
  return source.replace(before, after);
}

export function patchCompanyAdapterSource(source) {
  const marker = '/*OMAAX_COMPANY_ERROR_CODES_V1*/';
  if (source.includes(marker)) {
    const lineStart = source.indexOf('\n',source.indexOf(marker))+1;
    const lineEnd = source.indexOf('\n',lineStart);
    if (!source.slice(lineStart,lineEnd).endsWith('return error.code;')) throw new Error('Company adapter generated region changed');
    return source.slice(0,lineStart)+`\tif (${JSON.stringify(Object.keys(companyErrors))}.includes(error?.code)) return error.code;`+source.slice(lineEnd);
  }
  return once(source, 'function httpErrorCode(status, error) {',
    `function httpErrorCode(status, error) {\n\t${marker}\n\tif (${JSON.stringify(Object.keys(companyErrors))}.includes(error?.code)) return error.code;`);
}

// pi-ai preserves the HTTP JSON body inside its terminal error message.
export function companyCodeFromPiError(message) {
  if (typeof message !== 'string') return null;
  const start = message.indexOf('{');
  if (start < 0) return null;
  try {
    const body = JSON.parse(message.slice(start));
    const code = body?.error?.code ?? body?.code;
    return Object.hasOwn(companyErrors, code) ? code : null;
  } catch { return null; }
}

export function patchCompanyPiAdapterSource(source) {
  const marker = '/*OMAAX_COMPANY_PI_ERROR_CODES_V1*/';
  const generated = `\t${marker}\n\tconst companyErrors = ${JSON.stringify(Object.fromEntries(Object.keys(companyErrors).map(code => [code, true])))};\n\tconst companyCode = (${companyCodeFromPiError.toString()})(message);\n\tif (companyCode) return companyCode;`;
  if (source.includes(marker)) {
    if (!source.includes(generated)) throw new Error('Company pi-ai adapter generated region changed');
    return source;
  }
  return once(source, 'function classifyPiAiError(message) {', `function classifyPiAiError(message) {\n${generated}`);
}

export function patchCompanyRetrySource(source) {
  const marker = '/*OMAAX_COMPANY_ERROR_RETRY_V1*/';
  if (source.includes(marker)) return source;
  // Retain the existing bounded retry policy, including user-configured classes.
  return once(source, '!policy.retryableCodes.includes(failure.code)',
    `${marker} !policy.retryableCodes.includes(failure.code) && !policy.retryableCodes.includes((${JSON.stringify(retryClasses)})[failure.code])`);
}

export function patchCompanyChatSource(source) {
  const marker = '/*OMAAX_COMPANY_ERROR_UI_V1*/';
  if (source.includes(marker)) {
    const lineStart = source.indexOf('\n',source.indexOf(marker))+1;
    const lineEnd = source.indexOf('\n',lineStart);
    if (!source.slice(lineStart,lineEnd).endsWith('return t("message.failure.company." + code);')) throw new Error('Company UI generated region changed');
    source = source.slice(0,lineStart)+`\t\t\tif (${JSON.stringify(Object.keys(companyErrors))}.includes(code)) return t("message.failure.company." + code);`+source.slice(lineEnd);
    for (const [locale,index] of [['zh',0],['en',1]]) {
      const start = source.indexOf(`const ${locale} = {`);
      const end = source.indexOf('\n\t\t};', start);
      if (start<0||end<0) throw new Error('Company UI locale region changed');
      let region = source.slice(start,end);
      for (const [code,labels] of Object.entries(companyErrors)) {
        const key = JSON.stringify('message.failure.company.' + code);
        if (!region.includes(key+':')) region = region.replace(`const ${locale} = {`,`const ${locale} = {\n\t\t\t${key}: ${JSON.stringify(labels[index])},`);
      }
      source = source.slice(0,start)+region+source.slice(end);
    }
    return patchInternalErrorUi(source, 'chat');
  }
  source = once(source, 'function failureMessage(message, code, t) {',
    `function failureMessage(message, code, t) {\n\t\t\t${marker}\n\t\t\tif (${JSON.stringify(Object.keys(companyErrors))}.includes(code)) return t("message.failure.company." + code);`);
  source = once(source, '"message.failure.auth": "API 密钥无效",',
    '"message.failure.auth": "身份校验未通过，请检查登录状态和账号授权。",');
  source = once(source, '"message.failure.auth": "API key is invalid",',
    '"message.failure.auth": "Authentication failed. Check your sign-in status and account permissions.",');
  for (const [locale,index] of [['zh',0],['en',1]]) {
    const entries = Object.entries(companyErrors).map(([code,labels]) => `\t\t\t${JSON.stringify('message.failure.company.' + code)}: ${JSON.stringify(labels[index])},`).join('\n');
    source = once(source, `const ${locale} = {`, `const ${locale} = {\n${entries}`);
  }
  return patchInternalErrorUi(source, 'chat');
}

export function patchCompanyTrajectorySource(source) {
  const marker = '/*OMAAX_COMPANY_ERROR_TRAJECTORY_V1*/';
  if (source.includes(marker)) return patchInternalErrorUi(source, 'trajectory');
  source = once(source,'function requestErrorMessage(request, t) {',
    `function requestErrorMessage(request, t) {\n\t\t\t${marker}\n\t\t\tif (${JSON.stringify(Object.keys(companyErrors))}.includes(request.errorCode)) return t("details.failure.company." + request.errorCode);`);
  source = once(source,'"details.failure.auth": "API 密钥无效",','"details.failure.auth": "身份校验未通过，请检查登录状态和账号授权。",');
  source = once(source,'"details.failure.auth": "API key is invalid",','"details.failure.auth": "Authentication failed. Check your sign-in status and account permissions.",');
  for (const [locale,index] of [['zh',0],['en',1]]) {
    const entries = Object.entries(companyErrors).map(([code,labels]) => `\t\t\t${JSON.stringify('details.failure.company.'+code)}: ${JSON.stringify(labels[index])},`).join('\n');
    source = once(source,`const ${locale} = {`,`const ${locale} = {\n${entries}`);
  }
  return patchInternalErrorUi(source, 'trajectory');
}

export function patchCompanyErrors(runtimeDir = path.join(root,'dsh-runtime')) {
  const bases = ['node_modules/@deepseek-ai','node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai']
    .map(p => path.join(runtimeDir,p)).filter(p => existsSync(path.join(p,'dsh-llm-retry/lib/index.js')));
  if (!bases.length) throw new Error('Company error runtime missing');
  const files = [];
  for (const base of bases) {
    const modern = existsSync(path.join(base,'dsh-llm-pi-ai/lib/index.js'));
    const adapters = modern ? [['dsh-llm-pi-ai/lib/index.js',patchCompanyPiAdapterSource]] : [['dsh-llm-deepseek/lib/index.js',patchCompanyAdapterSource]];
    if (!adapters.length) throw new Error('Company model adapter missing');
    for (const [name,patch] of [...adapters,['dsh-llm-retry/lib/index.js',patchCompanyRetrySource],['dsh-client-ui-chat/lib/client.js',patchCompanyChatSource],['dsh-client-ui-trajectory/lib/client.js',patchCompanyTrajectorySource]]) {
      const file = path.join(base,name);
      writeFileSync(file,patch(readFileSync(file,'utf8')));
      files.push(file);
    }
  }
  return files;
}

if (process.argv[1] && import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(`Company error categories ready (${patchCompanyErrors(process.argv[2]).length} files)`);
}
