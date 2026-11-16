# Android Control

A local command-line tool for inspecting and operating an authorized Android
device through ADB. Inspect the current app, select visible controls, and verify
the result after each action. The repository includes read-only device and SMS
diagnostics and a guide for manually setting up social accounts.

## Get started

1. Install Python 3.10 or newer and put `adb` from
   [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)
   on your PATH. The tool uses the Python standard library.
2. Connect your test device over USB, enable USB debugging, and approve your
   computer on the device. See Google's [ADB setup guide](https://developer.android.com/tools/adb)
   for USB troubleshooting and wireless pairing.
3. From this checkout, list devices and run a read-only check. Replace
   `DEVICE_SERIAL` with the serial shown for your authorized device.

   ```sh
   python3 -B phone.py devices
   python3 -B device_info.py --device DEVICE_SERIAL
   python3 -B phone.py foreground --device DEVICE_SERIAL
   ```

Open an ordinary test screen on the device and run the following command to read
its accessibility tree.

```sh
python3 -B phone.py snapshot --device DEVICE_SERIAL
python3 -B phone.py --help
```

## Optional MCP server

An MCP client can launch Android Control locally over stdio. The server only
needs the optional MCP SDK and access to `adb` on its PATH. From the checkout,
create an environment for the server.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

For a client that uses `mcpServers` JSON, replace the example paths with the
absolute paths to the environment's Python executable and `android_mcp.py`.

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

The default tools are `list_devices` (authorized serials and ADB states),
`foreground` (focused package and activity), and `snapshot` (guarded UI summary).
Supply the target `serial` to `foreground` and `snapshot`. To scope every tool
to one phone, append `"--device", "DEVICE_SERIAL"` to `args`, using the serial
shown by `phone.py devices`. The scoped read tools can then omit `serial`.

To enable `tap_label`, append `"--allow-input"` after the device arguments. The
tool acts only on that configured phone and requires a single enabled clickable
match for the exact accessibility label. Inspect the screen again after a tap
to verify its effect.

Use a trusted local client and a test screen. Snapshots can contain private app
content. Handle sensitive prompts manually and keep login, messaging, and
payment screens out of the MCP workflow.

## Workflows

- [App navigation and UI interaction](docs/workflows.md)
- [Device diagnostics and SMS inspection](docs/device-information.md)
- [Manual social-account setup and receipts](docs/social-account-setup.md)
- [Design references](docs/research.md)

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

[MIT](LICENSE)
