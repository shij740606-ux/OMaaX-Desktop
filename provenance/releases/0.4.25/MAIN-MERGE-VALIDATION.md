# Main integration validation — 2026-10-17

Integrated feature commit `8545f1e014c1083d269bd5bc3a1bd0cb8ac2fc34` with
`origin/main` at `a502d1a` (including `80e43cb`). Both histories are retained.

## Conflict resolution

- App, Sidebar and Tab type retain mandatory AuthGate, qa/bioflow entitlement checks and hidden knowledge UI, while adding the account route and CreditCard navigation for authenticated users.
- Preserved AccountView, accountMock, billing handover and English translations from main. No billing API or payment processing has been added. A visible notice identifies the sample usage/bills and unavailable payments. Existing plan selection and payment previews remain available.
- Plan cards adapt to narrow windows to avoid squeezed vertical plan names. The existing chat retry/call/token budget display remains independent of the sample billing figures.
- No changes to deletion/overwrite guards, server account binding, checkpoint recovery or resource scheduling in this merge.

## Re-run on merged working tree

| Check | Result |
|---|---|
| TypeScript | Passed |
| Production frontend build | Passed; existing large-chunk size warning |
| Node regression | 102 passed, 0 failed |
| Native Rust library | 34 passed, 0 failed, 1 pre-existing ignored |
| Linux regression on isolated 175 diagnostic directory | 162 passed, 0 failed, 0 skipped |
| Real SDK workflow guard | Passed; rejected extra model call |
| Auth/settings browser regression | 13 groups passed, no page errors |
| Model workflow/results/resume/budget browser regression | 13 groups passed, no page errors |
| New account integration browser test | 7 groups passed, no page errors |

The new browser test covers unauthenticated access, qa/admin/no-entitlement accounts,
knowledge hiding, retained entitlement restrictions, plan selection, both payment
previews/cancellation, logout, and light/dark layouts at 1280/900/768 widths.
Browser credentials and bridge responses are synthetic; no real payment is made.
Linux test directory: `/home/xzq/omaax/diagnostics/server-full-qa-78b6dae0c2b645369581e5bf6c4ce8ef`.
Successful temporary Linux fixtures were removed by the existing scoped test helper.

Local raw evidence: `validation-local/merge-{node,native,linux,sdk,auth-ui,workflow-ui,build}.log`
and `validation-local/account-merge-browser/result.json` / screenshots (not committed).
Reproduce UI test with Vite at port 41740 and
`python desktop/qa/server-management/account_merge_test.py` using the existing QA Playwright runtime.

This is a source integration, not a desktop installer deployment. Production login,
server account binding provisioning and real billing/payment acceptance remain outside
this merge; see ACCOUNT-DEPLOYMENT.md and BILLING_HANDOVER.md.
