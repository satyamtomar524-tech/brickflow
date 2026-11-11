# Five-minute fictional demo

Start the server using the README and open http://127.0.0.1:8770/.

1. **Overview:** an untouched seeded workspace shows 500 bricks and INR 3,500 cash. Read stock and money separately.
2. **Owner report:** inspect the supporting record references. Earnings, approvals and payments are separate events.
3. **Bill corrections:** follow the [three entries below](#check-a-bill-correction) to see when the amount owed changes and when cash moves.
4. **Stock:** follow a lot through stages. A transfer changes location/stage, not the number newly produced. Returned stock needs inspection before it becomes saleable.
5. **Backup:** use the app's backup action to create a verified local copy. Keep backup files off GitHub.

The quoted figures apply only to an untouched demonstration. Your saved entries change its balances. To try again without losing anything, start with a new `--db` filename rather than deleting your current database.

## Check a bill correction

In a fresh seeded workspace, open **Record an activity** and keep the suggested dates. Use fictional text for the reason and evidence reference.

| Activity | What to enter | Expected result |
| --- | --- | --- |
| Correction request | Select `BILL-001`; enter old amount 200 and proposed amount 250 | Request is pending. Nothing is owed on the original paid bill; cash stays INR 3,500. |
| Correction review | Select the request and approve it | INR 50 is now owed. Cash stays INR 3,500. |
| Bill payment | Select the same bill, the approved correction and its cash account; pay 50 | Nothing is owed. Cash falls to INR 3,450. |

In **Owner’s report**, expand **Supplier payable total** under **Trace each measure**. The supporting references include the correction and approval; **Record trail → Correction history** shows the original and proposed amounts.

## Review opening balances

Use the README's `--reviewed` command with a new filename. This starts without financial openings or posted activity, while retaining fictional parties and a fixed demonstration period.

1. Register internally named cash/bank accounts. Do not enter bank credentials.
2. Enter fictional account openings with evidence references and review notes. An explicitly reviewed zero is valid; a missing opening remains **Not established**.
3. Review the relevant party openings: worker payable, supplier payable, customer receivable and customer advance. Opposing balances remain separate.
4. Lock account setup after every declared account has a reviewed opening. Current activity can then be posted. A previously unknown party opening can receive its first review later, but a reviewed value cannot be overwritten.
5. Settle opening debt through the selected account. This does not create a current bill or wage. Allocate opening customer advances to the same buyer's sale without moving money again.

Use the fixed period shown by the sample. Backdated insertion is intentionally restricted; do not substitute today's date blindly.

## Backups and upgrades

The app saves to `development/review_app/data/review.sqlite3` by default. `--db` chooses another path. The in-app backup action verifies the copy before reporting success.

For an older supported schema, stop the app and run:

```sh
python development/chapter3/upgrade.py path/to/old.sqlite3 path/to/new-v3.sqlite3
python development/review_app/server.py --db path/to/new-v3.sqlite3 --port 8770
```

Replace the example paths. The destination must not already exist. The source is preserved, and legacy amounts retain a carried-over label rather than becoming invented owner reviews.

## Troubleshooting

- **Port occupied:** stop your earlier server or use another `--port`; open the matching URL.
- **Reviewed setup requires a new database:** omit `--reviewed` to reopen, or choose a new filename.
- **Fixture fingerprint mismatch:** restore both tracked JSON fixtures from the same Git revision. They are preserved byte-for-byte; do not reformat them independently.
- **Unknown opening / setup not locked:** complete the fictional account reviews before posting. Missing business facts must not be replaced with guessed amounts.
- **No private source register installed:** expected in the public sample. Private documents are intentionally absent.
- **SQLite cannot open a backup file on Windows:** extract the sample into a short local path. Deeply nested paths can exceed the filesystem path limit when tests create database and journal filenames.
