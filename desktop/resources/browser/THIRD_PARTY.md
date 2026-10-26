# Browser operation provenance

OMaaX uses the same fixed `@playwright/mcp` 0.0.80 engine selected by the official
DeepSeek Harness Playwright browser provider. The current official DSH provider
is experimental and requires the newer 0.2.x service graph; this integration
uses an original, small adapter for the existing stable DSH 0.1.5 MCP and scope
interfaces. It does not install or migrate a second DSH runtime.

- Official provider: https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/experimental/browser-use-playwright-mcp
- Official MCP backend: https://github.com/microsoft/playwright-mcp
- DeepSeek Harness and its existing MCP client: MIT, licenses kept in the stable runtime.
- `@playwright/mcp` 0.0.80, `playwright` and `playwright-core`
  1.63.0-alpha-2026-08-31: Apache-2.0. Upstream packages, LICENSE and NOTICE files
  remain unchanged in `node_modules`.

The local adapter launches an installed Edge/Chrome with an independent,
temporary profile per live conversation. It does not copy the default browser
profile, saved passwords, cookies, or model credentials. The current OMaaX
model chooses and invokes the tools, so no additional model API key is needed.
Sign-in inside the managed browser is manual. Browser state is not restored
from old conversations after the application or agent restarts.

The tool is discovered as `omaax_browser`. Starting it discovers the official
MCP tools in that conversation. Only requested browser work launches a browser;
normal chats incur no browser startup. Tool calls are recorded in the existing
conversation log and operation-only audit metadata in the account browser area.
Screenshots/downloads are stored below the active project's `browser-results`.
The browser installation tool and reusable login-state export are unavailable;
no additional browser binary is downloaded during installation or use.

Web content is untrusted input. Existing original-data protection and fixed
analysis workflow rules continue to apply. Browser operation does not provide
new authorization for uploads, payments, messages or remote account changes.
