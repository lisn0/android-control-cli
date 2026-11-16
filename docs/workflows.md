# Android workflows

These workflows use the current `phone.py` command set. The controller can list
ADB devices, read the foreground UI hierarchy, tap a clickable element from the
latest unchanged hierarchy, tap a unique exact label, scroll or swipe within the
device bounds, take an explicit screenshot, open an app or browser URL, report
the focused component, send an allowlisted key, and type verified ASCII into
one empty focused text field.

Set the connected device serial in your shell for convenience. Keep the serial
specific to the device the user asked to operate:

```sh
python3 -B phone.py devices
DEVICE='the-serial-shown-above'
python3 -B phone.py snapshot --device "$DEVICE"
```

On loading screens, `snapshot --device "$DEVICE" --settle 2` samples for
consecutive matching trees with a two-second sampling budget. ADB dump latency
can extend the elapsed time. A screen that never settles stops with an error;
matching trees do not prove that a network request completed.

`DEVICE` is a shell variable for the examples below; replace it with the real
serial in your own session. Do not write private serials into shared docs,
commits, screenshots, or reports.

## Inspect the current app

Take a fresh snapshot and read its header and labeled controls:

```sh
python3 -B phone.py snapshot --device "$DEVICE"
```

The header identifies the selected device and a short screen hash. The numbered
refs belong only to this exact screen. Stop if the foreground app, account
identity, or page is not the one expected. A new snapshot replaces the refs;
never carry a ref across navigation or a visible UI change.

Use `foreground` for a targeted diagnostic that prints only the Android window's
package and component:

```sh
python3 -B phone.py foreground --device "$DEVICE"
```

## Open an app or browser page

Use `open` with the exact package for the requested device. To open a URL,
choose the requested browser package and pass an HTTPS address or a loopback
HTTP address for a local fixture:

```sh
python3 -B phone.py open --device "$DEVICE" org.mozilla.firefox
python3 -B phone.py open --device "$DEVICE" org.mozilla.firefox --url 'https://example.org/'
python3 -B phone.py snapshot --device "$DEVICE" --settle 2
```

Use non-secret URLs only. The URL can appear in process arguments. The browser
may redirect or ask for a choice after it opens, so use the snapshot to confirm
the intended page instead of treating package focus as proof of navigation.
If a protected authentication screen is already foreground, leave it with the
operator before continuing.

## Debug a UI flow

1. Snapshot and identify the visible screen and intended control.
2. For a clickable element, use its ref from that snapshot:

   ```sh
   python3 -B phone.py tap --device "$DEVICE" REF
   ```

3. Snapshot again and verify the expected next screen before continuing.
4. If tap reports a changed screen, disabled control, or unusable target, stop
   and inspect the new screen. Do not retry the same ref.
5. To go back from an ordinary screen, take a snapshot, then send the allowlisted
   back key:

   ```sh
   python3 -B phone.py key --device "$DEVICE" back
   ```

The CLI guards English-labeled authentication/recovery screens and Android
password fields, but cannot recognize every unlabeled or non-English challenge.
Look at the actual device too. The owner handles passwords, PINs, verification
codes, recovery, identity and payment prompts directly. If such a screen appears,
stop automation.

## Check accessibility support

Run a snapshot on the screen in question. If it reports that a fresh hierarchy is
unavailable or returns no useful controls, this app or screen may not expose a
usable Android accessibility tree. Do not compensate with guessed coordinates
or arbitrary shell taps. An explicitly requested capture to a new path is
available with `phone.py screenshot --device "$DEVICE" PATH`; inspect it before
acting. Screenshots can contain sensitive information, and the guard cannot
recognize every sensitive screen. Never share a capture without reviewing it.

For an app-specific bug report, record only the app name/version if visibly
available, Android version, a sanitized error message, and which expected
control was absent. Do not attach raw UI XML: it may include account details or
other personal screen text. A screenshot can expose the same information, so
review and redact it before sharing.

## Build an account inventory

Inventory collection is a read-and-record workflow; `phone.py` does not create
the inventory automatically.

1. Read the existing project inventory before opening apps. Preserve its account
   states and unresolved signup attempts.
2. Inspect the platform's visible account switcher or profile. Record platform,
   brand, visible display name and handle, evidence date, and whether the
   identity is directly verified or only proposed.
3. Keep account existence, handle, sign-in state, linked Page, and publishing/API
   access as separate facts. A Page in a switcher does not establish a linked
   Instagram profile or an API grant.
4. Never record passwords, recovery addresses or codes, tokens, raw UI dumps, or
   full contact details. Avoid recording follower counts unless that is the
   explicit purpose of the audit.
5. For account creation states and duplicate prevention, follow
   [social account setup](social-account-setup.md).

Suggested status values are **planned**, **in progress**, **submitted /
uncertain**, **created / identity pending**, **verified**, and **blocked**. Keep
an uncertain result visible until an account switcher or profile readback
resolves it.

## Verify one UI action

The controller already re-reads the UI hierarchy before a tap and refuses the
tap if the hierarchy hash differs from the saved snapshot. It also checks that
the ref still points to the same clickable target. After the tap, verification
is still the caller's responsibility: take a new snapshot and confirm the
result. A successful command only means Android accepted the input event.

For ordinary non-secret text entry, focus one empty field, snapshot to check its
label and screen, then type and verify the displayed value:

```sh
python3 -B phone.py type --device "$DEVICE" 'Example Studio'
python3 -B phone.py snapshot --device "$DEVICE"
```

Typing is ASCII-only, requires one empty focused editable field, and passes the
text as a local process argument. Never use it for a secret, verification code,
or personal data that should not appear in process inspection. Type only details
the user requested and verify the displayed result before submitting a form.
If an empty field exposes its placeholder as both its text and hint, pass
`--placeholder 'Exact placeholder'` only after inspecting that field; normal
entry remains strict. Android accessibility does not prove whether matching
text is a hint or real content, so confirm visually when in doubt.

## Device state and interaction boundaries

The tool does not change global Android settings, clear app data, install or
uninstall packages, manage permissions, or reset the device. It has no general
shell passthrough. Device-specific diagnostics can use ordinary ADB commands,
but inspect each command before running it and prefer read-only queries. Do not
use device commands to route around a refusal or authentication challenge.
