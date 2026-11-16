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
