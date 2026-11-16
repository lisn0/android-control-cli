# Inspecting a phone without using its screen

ADB can answer many device questions without opening an app or changing the
foreground screen. Start with the selected device and the curated diagnostic:

```sh
python3 -B phone.py devices
python3 -B device_info.py --device DEVICE_SERIAL
```

The diagnostic uses read-only commands and reports supported, unavailable or
failed values honestly. Its output is limited to device model and Android
version, battery, data-partition storage, and high-level SIM/telephony
capabilities. It does not establish that a SIM has a usable number, SMS service,
or a particular owner. A SIM may report `LOADED` while its phone number is
unknown. Do not infer a current SIM's number from an old account record.

Other useful checks, when the task calls for them, include:

- **Installed apps:** package names and versions can show whether a needed app is
  installed; presence does not prove an account is signed in.
- **Foreground app and UI:** the window or accessibility tree can identify the
  current app and visible controls; it may contain private content. The guarded
  `phone.py snapshot` is for authorized UI inspection.
- **Battery and charging:** current level and charging status help plan longer
  work; they are a momentary reading, not an estimate of battery health.
- **Storage and memory:** free space and available memory help diagnose failures;
  do not remove files or clear apps as a side effect of an inspection.
- **Network and SIM:** radio/SIM state and hardware features help identify
  connectivity options. Network registration, a SIM's own number, SMS delivery
  and carrier support are separate checks and may be unavailable to ADB.
- **OS and hardware:** model, Android version, screen size and system features
  help determine compatibility; available features depend on model and region.
- **App diagnostics:** scoped logs can help investigate a specific failure, but
  may contain credentials, messages, identifiers or private URLs. Capture only
  when necessary and sanitize before sharing.
- **SMS:** `messages.py --device DEVICE_SERIAL --senders` lists inbox senders;
  `messages.py --device DEVICE_SERIAL --sender 'Example Carrier'` reads that sender's messages.
  Add `--contains TEXT` or `--subscription ID` to filter results.
  `--limit` controls how many matching messages are displayed; the query reads
  the matching inbox before sorting. Message contents and codes are shown when
  requested. A carrier message or historical number alone does not establish
  which SIM is currently using a number. Android's text-based provider format
  may be ambiguous if a message contains a line resembling a provider row;
  the reader reports unsupported formats rather than guessing.

Use the least sensitive query that answers the request. Raw device and app
dumps can include SIM identifiers, phone numbers, IMEIs, network addresses,
account details, messages, verification codes or tokens; don't print or save
them by default. Do not infer eSIM support solely from a missing feature flag.
Do not enable permissions, change SIMs or network settings, send messages, clear
data or install packages as part of a read-only inspection.
