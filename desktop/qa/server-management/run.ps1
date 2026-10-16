param([switch]$Soak,[switch]$LiveServers)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$taskPython=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$skill=Join-Path $env:USERPROFILE '.codex\skills\webapp-testing\scripts\with_server.py'
$cargo=Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
if(-not(Test-Path -LiteralPath $taskPython)){throw '需要配置 Python 运行时。'}
if(-not(Test-Path -LiteralPath $skill)){throw '请先安装 webapp-testing 技能。'}
if(-not(Test-Path -LiteralPath (Join-Path $projectRoot 'validation-local\qa-runtime\python\playwright\__init__.py'))){throw '需要先按 testing-config.json 配置独立 Playwright 依赖。'}
Push-Location $projectRoot
try{
 if(Get-NetTCPConnection -LocalPort 41739 -State Listen -ErrorAction SilentlyContinue){throw '测试端口 41739 已被占用，请先结束上一轮测试；不会连接或结束未知服务。'}
 & node desktop/node_modules/typescript/bin/tsc --noEmit -p desktop/tsconfig.json
 if($LASTEXITCODE -ne 0){throw 'TypeScript 检查未通过。'}
 & node --test desktop/scripts/server-management-ui.test.mjs desktop/scripts/server-requests.test.mjs desktop/scripts/product-help-ui.test.mjs desktop/resources/ssh/client.test.cjs desktop/resources/ssh/browse.test.cjs desktop/resources/ssh/relay.test.cjs desktop/resources/ssh/relay-recovery.test.cjs
 if($LASTEXITCODE -ne 0){throw '组件或协议回归未通过。'}
 Push-Location desktop/src-tauri
 try{$env:OMAAX_TEST_PYTHON=$taskPython;& $cargo test --lib -- --test-threads=1;if($LASTEXITCODE -ne 0){throw '原生回归未通过。'}}finally{Pop-Location}
 & $taskPython -X utf8 -B $skill --server 'node desktop/node_modules/vite/bin/vite.js --config desktop/qa/server-management/vite.config.ts' --port 41739 -- $taskPython -X utf8 -B desktop/qa/server-management/browser_test.py
 if($LASTEXITCODE -ne 0){throw '浏览器交互回归未通过。'}
 if($Soak){
  & $taskPython -X utf8 -B $skill --server 'node desktop/node_modules/vite/bin/vite.js --config desktop/qa/server-management/vite.config.ts' --port 41739 -- $taskPython -X utf8 -B desktop/qa/server-management/soak_test.py
  if($LASTEXITCODE -ne 0){throw '持续稳定性测试未通过。'}
 }
 if($LiveServers){
  foreach($script in @('server-full-linux-tests.py','server-full-live-tests.py','server-full-transfer-tests.py','server-full-installed-mcp.py')){
   if(-not(Test-Path -LiteralPath (Join-Path 'validation-local' $script))){throw "本机真实服务器诊断脚本不存在：$script；默认本机测试不需要这些脚本。"}
   & $taskPython -X utf8 -B (Join-Path 'validation-local' $script)
   if($LASTEXITCODE -ne 0){throw "真实服务器测试未通过：$script"}
  }
 }
 Write-Output '测试结果、截图和日志位于 validation-local/server-full-qa；真实服务器连接失败会单独记录。'
}finally{Pop-Location}
