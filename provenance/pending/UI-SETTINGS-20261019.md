# Settings and navigation changes — 2026-10-19

Source changes only; no build, installer, desktop replacement or GitHub push requested.

- Rename visible tool-center community labels to 扩展工具 / 推荐工具; preserve catalog identifiers and source attribution.
- Rename analysis navigation and page title to 分析结果.
- Move authenticated identity, admin entry and logout to Settings > 账号和用量, retaining existing account/usage UI.
- Retain AuthGate logout revision and immediate unmount protection.
- Remove settings navigation footer hint.

## MCP and image restoration (subsequently authorized)

Restored image and MCP configuration forms. Native settings validate MCP names, reserved namespaces, duplicate names, transports, URLs and environment variable keys before saving. Enabled configurations are passed to the actual MCP client at assistant startup; invalid legacy configurations display an error rather than silently disappearing.

External MCP calls require an SDK user approval for each exact execution. A monotonic guard rejects calls that skip the approval listener, and rechecks login after approval. Third-party services must be trusted: the application cannot sandbox arbitrary user-installed service programs or verify their implementation. No terminal or model-facing approval tool was enabled. Fixed-workflow conversations still only admit their original protected tools.

The built-in omaax_generate_image tool calls the configured OpenAI-compatible Images API directly, supports base64 or URL images, bounds response size and duration, uses the caller cancellation signal, excludes API credentials from download requests and results, and writes a fresh uniquely named directory/file. It returns a local authenticated preview link. It has no deletion, overwrite, arbitrary output-path, shell or reference-file input.

## Validation

- TypeScript noEmit passed.
- Auth browser: 17 groups passed.
- Account/usage browser: 7 groups passed (including no global account widget and no settings footer hint).
- Cloud knowledge browser: 18 groups passed.
- Navigation browser: 4 groups passed, including result sources and cancelled deletion.
- Auth/workflow/budget/education guard tests: 13 passed.

Browser tests use synthetic bridges and real React/Vue in Edge, not production accounts or paid models. Initial guard run hit sandbox filesystem/listener restrictions; normal-permission rerun passed. Initial navigation invocation lacked its dedicated 41742/41743 services; rerun with both services passed. No installed-desktop validation or packaging was performed.

## Restoration validation

- TypeScript noEmit passed after final UI changes.
- Rust suite: 41 passed, 2 explicitly ignored integrations; final native startup validation compiled and targeted test passed again.
- Auth/image/external MCP/workflow/budget/education protection Node tests: 16 passed.
- Real SDK with real stdio MCP transport connected to an isolated fixture: 7 checks passed. Approval outcomes and image provider are synthetic; exact approval permits one MCP dispatch; rejection, expired login and pre-execute bypass do not dispatch. The real SDK approval service is present and image schema/render registration executes successfully.
- Browser configuration checks: 7 groups passed, including JSON argument/environment values containing spaces, invalid JSON blocking save, error display, removing a row without mixing adjacent configuration, and responsive layouts.
- Login/remember/logout browser regression: 17 groups passed.
- Actual paid image provider, external Zotero/PubMed deployments and manual approval clicking were not exercised. No paid calls or production credentials were used. No package or desktop replacement was performed.
