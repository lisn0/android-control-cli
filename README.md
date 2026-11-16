# Android Control

A local command-line tool for inspecting and operating an authorized Android
device through ADB. Inspect the current app, select visible controls, and verify
the result after each action. The repository includes read-only device and SMS
diagnostics and a guide for manually setting up social accounts.

## Install

Python 3.10 or newer is required. Install `adb` from
[Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)
and put it on your PATH. In your Python environment, install the CLI.

```sh
python -m pip install android-device-control
```

The CLI uses the Python standard library. If you are working from a clone, you
can run the scripts directly with `python3 -B phone.py`, `device_info.py`,
`messages.py`, and `receipts.py`.

## Get started

1. Connect your test device over USB, enable USB debugging, and approve your
   computer on the device. See Google's [ADB setup guide](https://developer.android.com/tools/adb)
   for USB troubleshooting and wireless pairing.
2. List devices and run a read-only check. Replace
   `DEVICE_SERIAL` with the serial shown for your authorized device.

   ```sh
   android-control devices
   android-control-device-info --device DEVICE_SERIAL
   android-control foreground --device DEVICE_SERIAL
   ```

Open an ordinary test screen on the device to read its accessibility tree.

```sh
android-control snapshot --device DEVICE_SERIAL
android-control --help
```

Use `android-control-sms --help` for SMS diagnostics and
`android-control-receipts --help` for the local setup receipt ledger.

## Optional MCP server

An MCP client can launch Android Control locally over stdio. The server only
needs the optional MCP SDK and access to `adb` on its PATH.

```sh
python -m pip install 'android-device-control[mcp]'
```

For a client that uses `mcpServers` JSON, use the installed command on its PATH
or set `command` to the full path to the executable.

```json
{
  "mcpServers": {
    "android-control": {
      "command": "android-control-mcp"
    }
  }
}
```

With `uvx` on the client's PATH, use `"command": "uvx"` and
`"args": ["--from", "android-device-control[mcp]", "android-control-mcp"]`.

### Run with npx

With Node.js and `uvx` on PATH, launch the same MCP server from GitHub:

```sh
npx --yes --allow-git=root --package=github:lisn0/android-device-control android-device-control
```

The launcher uses the published PyPI package. It forwards options such as
`--device DEVICE_SERIAL` to the MCP server. ADB must still be on PATH.

### Run from a checkout

Create a Python environment with the MCP SDK.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

Replace the example paths with the absolute paths to the environment's Python
executable and `android_mcp.py`.

```json
{
  "mcpServers": {
    "android-control": {
      "command": "/absolute/path/to/android-control/.venv/bin/python",
      "args": ["/absolute/path/to/android-control/android_mcp.py"]
    }
  }
}
```

### Scope tools to a device

The default tools are `list_devices` (authorized serials and ADB states),
`foreground` (focused package and activity), and `snapshot` (guarded UI summary).
Supply the target `serial` to `foreground` and `snapshot`. To scope every tool
to one phone, append `"--device", "DEVICE_SERIAL"` to `args`, using the serial
shown by `android-control devices` or `python3 -B phone.py devices`. The scoped
read tools can then omit `serial`.

To enable `tap_label`, append `"--allow-input"` after the device arguments. The
tool acts only on that configured phone and requires a single enabled clickable
match for the exact accessibility label. Inspect the screen again after a tap
to verify its effect.

Use a trusted local client and a test screen. Snapshots can contain private app
content. Handle sensitive prompts manually and keep login, messaging, and
payment screens out of the MCP workflow.

## Workflows

- [App navigation and UI interaction](https://github.com/lisn0/android-device-control/blob/main/docs/workflows.md)
- [Device diagnostics and SMS inspection](https://github.com/lisn0/android-device-control/blob/main/docs/device-information.md)
- [Manual social-account setup and receipts](https://github.com/lisn0/android-device-control/blob/main/docs/social-account-setup.md)
- [Design references](https://github.com/lisn0/android-device-control/blob/main/docs/research.md)

The authentication guard uses English accessibility labels and password-field
metadata. It can miss unlabeled or non-English screens. Handle passwords,
verification codes, recovery, identity, and payment prompts directly on the device.
Keep secrets out of command arguments and receipts.

## Tests and bug reports

```sh
python3 -B -m unittest discover -s tests -v
```

The tests use mock ADB responses. For a bug report, provide the command, Android
and ADB versions, and a sanitized error or synthetic reproduction. Device lists,
snapshots, screenshots, and SMS output can contain serials, account identifiers,
private messages, or codes. Remove those details before sharing.

## License

[MIT](https://github.com/lisn0/android-device-control/blob/main/LICENSE)
