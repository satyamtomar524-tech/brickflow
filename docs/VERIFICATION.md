# Sample verification

Verified locally on **11 November 2026** using the existing project environment. No cloud runtime or CI was used.

| Check | Result |
| --- | --- |
| Python app, ledger, accounts, reports, process and planning suite | 121 tests and 26 subtests passed |
| JavaScript browser-logic suite | 12 tests passed |
| Scoped Ruff checks listed in the README | Passed |

The exact commands are in the root README. Tests create disposable fictional databases. The financial cases cover missing versus zero openings, account selection, per-account funds, opening settlements, advance allocation caps, duplicate handling, atomic rollback, concurrent settlements, backup/reopen and supported copy upgrades. Report tests include correction and approval references behind supplier totals.

This release was prepared from the reviewed local source baseline `3806c05` into a separate sample checkout. The original private working directory and its history were not copied. Public-copy changes are documentation, generic BrickFlow branding, removal of a personal margin target from help text, and a portable Windows launcher. The ledger and fixture bytes are unchanged.

The HTTP integration tests exercise the server with disposable data. JavaScript tests use a mocked browser and network. The attempted browser-automation preview was rejected because that navigation sent `Sec-Fetch-Site: cross-site`; the local same-origin restriction was retained. This release does not claim a fresh visual browser walkthrough or real-plant validation. Open the loopback address directly in a normal browser as described in the README.

No claims of production security, complete profit/tax accounting, operational deployment, forecasting accuracy or achieved business improvement follow from these checks. Demo roles are not authenticated.
