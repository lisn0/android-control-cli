#!/usr/bin/env python3
"""Read a small, privacy-conscious set of facts from one Android device.

Only fixed, read-only ADB queries are issued. The report intentionally never
queries subscriber identifiers, phone numbers, network addresses or accounts.
Battery dump output is parsed transiently and only selected fields are printed;
raw diagnostic output is not saved or included in the report.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass

TIMEOUT_SECONDS = 12
SERIAL_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class DeviceInfoError(RuntimeError):
    """A safe, user-facing error that contains no device output."""


@dataclass(frozen=True)
class Result:
    value: str
    state: str = "available"


def adb(device: str, *args: str) -> str:
    """Run one fixed ADB argument vector; never invoke a local shell."""
    try:
        result = subprocess.run(
            ["adb", "-s", device, *args],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeviceInfoError("ADB query timed out") from exc
    except OSError as exc:
        raise DeviceInfoError("ADB could not be started") from exc
    if result.returncode:
        raise DeviceInfoError(f"ADB query failed (exit {result.returncode})")
    return result.stdout.decode("utf-8", "replace").strip()


def query_property(device: str, key: str) -> Result:
    value = adb(device, "shell", "getprop", key).strip()
    # Device properties are untrusted terminal input. Keep values bounded and
    # single-line so control sequences cannot alter the report presentation.
    if (not value or len(value) > 128
            or any(unicodedata.category(char).startswith("C")
                   or unicodedata.category(char) in ("Zl", "Zp") for char in value)
            or value == "[ro.boot.unknown]"):
        return Result("unavailable", "unavailable")
    return Result(value)


def battery(device: str) -> dict[str, Result]:
    text = adb(device, "shell", "dumpsys", "battery")
    values = dict(re.findall(r"^\s*(level|status):\s*(\d+)\s*$", text, re.MULTILINE))
    level = values.get("level", "")
    status = values.get("status", "")
    if level and not 0 <= int(level) <= 100:
        level = ""
    status_names = {"1": "unknown", "2": "charging", "3": "discharging",
                    "4": "not charging", "5": "full"}
    return {
        "Battery level": Result(f"{level}%") if level else Result("unavailable", "unavailable"),
        "Battery status": Result(status_names[status]) if status in status_names
        else Result("unavailable", "unavailable"),
    }


def data_storage(device: str) -> Result:
    text = adb(device, "shell", "df", "-k", "/data")
    lines = [line.split() for line in text.splitlines() if line.strip()]
    # Android may report a storage alias (for example an emulated-storage
    # mount) for the explicitly requested /data path. Accept one numeric row;
    # don't require its displayed mount label to literally be /data.
    rows = []
    for fields in lines[1:]:
        if len(fields) >= 6 and all(re.fullmatch(r"\d+", field) for field in fields[1:4]):
            rows.append(fields)
    if len(rows) != 1:
        return Result("unavailable", "unavailable")
    total, used, free = (int(rows[0][i]) for i in (1, 2, 3))
    if total <= 0 or used > total or free > total:
        return Result("unavailable", "unavailable")
    return Result(
        f"{used / 1024:.0f} MiB used / {free / 1024:.0f} MiB free / "
        f"{total / 1024:.0f} MiB total"
    )


def hardware_features(device: str) -> Result:
    text = adb(device, "shell", "pm", "list", "features")
    features = set(re.findall(r"^feature:([A-Za-z0-9_.-]+)\s*$", text, re.MULTILINE))
    prefixes = ("android.hardware.telephony", "android.hardware.telephony.euicc")
    selected = sorted(feature for feature in features if feature.startswith(prefixes))
    if not text:
        return Result("unavailable", "unavailable")
    return Result(", ".join(selected) if selected else "none reported")


def sim_info(device: str) -> dict[str, Result]:
    # This property carries SIM state tokens only, never phone numbers or IDs.
    value = adb(device, "shell", "getprop", "gsm.sim.state").strip()
    states = [token.strip().upper() for token in value.split(",") if token.strip()]
    if not states or any(not re.fullmatch(r"[A-Z_ -]{1,32}", state) for state in states):
        return {"SIM state entries": Result("unavailable", "unavailable"),
                "SIM states": Result("unavailable", "unavailable")}
    return {"SIM state entries": Result(str(len(states))),
            "SIM states": Result(", ".join(states))}


def error_result(error: DeviceInfoError) -> Result:
    """Preserve failure versus absence without exposing command output."""
    return Result(str(error), "error")


def collect(device: str) -> list[tuple[str, Result]]:
    fields: list[tuple[str, Result]] = []
    for label, key in (
        ("Manufacturer", "ro.product.manufacturer"),
        ("Model", "ro.product.model"),
        ("Android version", "ro.build.version.release"),
        ("Android SDK", "ro.build.version.sdk"),
    ):
        try:
            fields.append((label, query_property(device, key)))
        except DeviceInfoError as exc:
            fields.append((label, error_result(exc)))
    try:
        fields.extend(battery(device).items())
    except DeviceInfoError as exc:
        fields.extend((label, error_result(exc)) for label in ("Battery level", "Battery status"))
    for label, query in (("/data storage", data_storage),
                         ("Telephony/eSIM hardware features", hardware_features)):
        try:
            fields.append((label, query(device)))
        except DeviceInfoError as exc:
            fields.append((label, error_result(exc)))
    try:
        fields.extend(sim_info(device).items())
    except DeviceInfoError as exc:
        fields.extend((label, error_result(exc))
                      for label in ("SIM state entries", "SIM states"))
    # Sensitive values are never queried or displayed. In particular, don't
    # add getprop ro.serialno, dumpsys iphonesubinfo/account, or ip/mac commands.
    for label in ("Hardware serial", "IMEI", "ICCID", "MAC/IP addresses",
                  "Account details", "Raw device dumps"):
        fields.append((label, Result("withheld for privacy", "withheld")))
    return fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="explicit ADB device serial")
    args = parser.parse_args(argv)
    if not SERIAL_RE.fullmatch(args.device):
        parser.error("--device must be an explicit ADB serial using letters, digits, '.', '_', ':', or '-'")
    try:
        fields = collect(args.device)
    except DeviceInfoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Android device information (read-only; sensitive identifiers withheld)")
    # The selected ADB serial can itself identify a device, so expose only that
    # one was explicitly selected; don't echo the serial into terminal logs.
    print("ADB device: explicitly selected")
    for label, result in fields:
        suffix = f" [{result.state}]" if result.state != "available" else ""
        print(f"{label}: {result.value}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
