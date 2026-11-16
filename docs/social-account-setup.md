# Social account setup workflow

Use this for explicitly requested brands and platforms. Account creation is a
real external action; respect the requested scope and keep existing sessions.
Never publish, follow, invite, link accounts, buy services or change recovery
details unless that action is authorized for the task.

## Before creating

1. Read the current account inventory and previous receipts for the exact brand
   and platform. Check the app's account switcher or another authorized identity
   source for existing accounts. An unresolved previous submission must be
   reconciled first; do not retry merely because a screen failed to advance.
2. Confirm the intended device, signed-in owner, brand, platform and exact
   identity. Make only requested accounts.
3. Select an appropriate account type and accurate details. A platform-proposed
   handle is not necessarily the intended final handle; record it as proposed
   until saved and independently read back. Do not invent personal information.

## During setup

- Inspect a fresh screen before each input. Re-observe after navigation, typing,
  category selection and submission. Use visible labels and verify the focused
  field and its actual value before continuing; the on-screen keyboard can move
  buttons. If the layout is unclear, stop and inspect rather than guessing.
- The owner handles passwords, PINs, OTPs, CAPTCHA, identity and payment screens
  directly. Never put credentials or codes into screenshots, receipts, shell
  arguments or chat. Preserve existing sign-ins and permissions.
- Submit creation once. If it stalls, errors or returns to the form, mark the
  result **uncertain** and inspect the account list before retrying. A platform
  confirmation is useful but still verify the resulting identity.
- Skip optional contacts, promotions and invitations unless requested. Do not
  assume an editable account has a usable API or publishing permission.

## Record the outcome

Update the owning account inventory with the date, platform, intended brand,
actual visible display name, actual saved handle or Page URL when confirmed,
category, and evidence source. Use these states separately:

- **Planned:** no creation submitted.
- **In progress:** a form is open, no creation submitted.
- **Submitted, uncertain:** creation submitted but outcome not established;
  include the next check and never silently retry.
- **Created:** platform confirms creation; independent identity readback pending.
- **Verified:** the exact account is visible on its own profile or account list;
  state separately if the saved handle is not confirmed.
- **Blocked:** record the exact challenge or error and the next action.

Do not store credential values, codes, recovery details, or raw sensitive screen
dumps in the inventory. A proposed, typed or available handle is not a saved
handle. Reconcile later changes by date, retaining earlier evidence.

The optional local ledger can retain dated status receipts without overwriting
history. It does not inspect the platform or create accounts:

```sh
python3 -B receipts.py add --brand 'Example Brand' --platform Facebook \
  --state submitted-uncertain --next-check 'Check account switcher before retry'
python3 -B receipts.py list
```

Use `--reconcile` only after investigating an uncertain submission; add
`--evidence` when marking an identity verified. See `receipts.py add --help`
for handle and evidence fields. The ledger is private local state. Its limited
secret-pattern checks cannot guarantee that text is safe; never enter secrets.
