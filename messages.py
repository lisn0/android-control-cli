#!/usr/bin/env python3
"""Read SMS inbox messages from one explicitly selected Android device.

This command is read-only: it runs a fixed ``content query`` against the SMS
inbox and never stores results. Message bodies are private content and are
printed to the terminal, so use an exact ``--sender`` filter when possible.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import re
import shlex
import subprocess
import sys

TIMEOUT_SECONDS = 20
SERIAL_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
SENDER_RE = re.compile(r"[^\x00-\x1f\x7f]{1,128}\Z")
ROW_RE = re.compile(
    r"^Row: \d+ _id=(\d+), address=([^,]*), body=(.*), date=(\d+), sub_id=([^,]*)$"
)


class MessagesError(RuntimeError):
    """A safe user-facing error without raw device output."""


@dataclass(frozen=True)
class Message:
    message_id: int
    sender: str
    body: str
    date_ms: int
    subscription_id: str


def sql_string(value: str) -> str:
    """Quote one SQL string literal for the content provider's --where value."""
    return "'" + value.replace("'", "''") + "'"


def build_command(device: str, sender: str | None) -> list[str]:
    if not SERIAL_RE.fullmatch(device):
        raise MessagesError("provide an explicit valid ADB device serial")
    command = [
        "adb", "-s", device, "shell", "content", "query",
        "--uri", "content://sms/inbox",
        "--projection", "_id:address:body:date:sub_id",
    ]
    if sender is not None:
        if not SENDER_RE.fullmatch(sender):
            raise MessagesError("sender must be 1–128 printable characters")
        where = f"address={sql_string(sender)}"
        # adb shell forwards a command through Android's shell; quote this
        # complete SQL expression for that second parsing layer as well.
        command.extend(("--where", shlex.quote(where)))
    return command


def query(device: str, sender: str | None = None) -> list[Message]:
    command = build_command(device, sender)
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MessagesError("SMS inbox query timed out") from exc
    except OSError as exc:
        raise MessagesError("ADB could not be started") from exc
    if result.returncode:
        # Android/provider errors can echo query data; do not relay stdout/stderr.
        raise MessagesError(
            "SMS inbox query failed; check device connection and SMS permission"
        )
    output = result.stdout.decode("utf-8", "replace")
    if output.strip() == "No result found.":
        return []
    messages: list[Message] = []
    rows = re.split(r"(?m)(?=^Row: \d+ _id=\d+, address=)", output)
    for row in rows:
        row = row.rstrip("\r\n")
        if not row.strip():
            continue
        match = re.fullmatch(ROW_RE.pattern, row, re.DOTALL)
        if not match:
            raise MessagesError("SMS provider returned an unsupported row format")
        message_id, address, body, date_ms, sub_id = match.groups()
        if sender is not None and address != sender:
            # Defense in depth: never print rows outside the requested sender.
            continue
        messages.append(Message(int(message_id), address, body, int(date_ms), sub_id))
    return messages


def senders(device: str) -> Counter[str]:
    if not SERIAL_RE.fullmatch(device):
        raise MessagesError("provide an explicit valid ADB device serial")
    try:
        result = subprocess.run(
            ["adb", "-s", device, "shell", "content", "query", "--uri",
             "content://sms/inbox", "--projection", "address"],
            capture_output=True, timeout=TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MessagesError("SMS sender query failed; check device connection") from exc
    if result.returncode:
        raise MessagesError("SMS sender query failed; check device connection and SMS permission")
    output = result.stdout.decode("utf-8", "replace").strip()
    if output == "No result found.":
        return Counter()
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        match = re.fullmatch(r"Row: \d+ address=(.*)", line)
        if not match:
            raise MessagesError("SMS provider returned an unsupported sender format")
        counts[match.group(1)] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="explicit ADB device serial")
    parser.add_argument(
        "--sender", help="exact sender/address filter (recommended for privacy)"
    )
    parser.add_argument("--senders", action="store_true", help="list sender names and counts without message bodies")
    parser.add_argument("--contains", help="show messages containing this text (case-insensitive)")
    parser.add_argument("--subscription", help="show messages for this subscription ID")
    parser.add_argument(
        "--limit", type=int, default=20,
        help="maximum number of returned messages (default: 20; maximum: 100)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    if args.senders and (args.sender or args.contains or args.subscription):
        parser.error("--senders cannot be combined with message filters")
    try:
        if args.senders:
            for name, count in sorted(senders(args.device).items()):
                print(f"{json.dumps(name, ensure_ascii=False)}: {count}")
            return 0
        messages = query(args.device, args.sender)
    except MessagesError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    selected = [item for item in messages
                if (not args.contains or args.contains.casefold() in item.body.casefold())
                and (not args.subscription or args.subscription == item.subscription_id)]
    selected = sorted(selected, key=lambda item: (item.date_ms, item.message_id))[-args.limit:]
    print(f"SMS inbox messages: {len(selected)} shown")
    for message in selected:
        print(f"\nFrom: {json.dumps(message.sender, ensure_ascii=False)}")
        print(f"Date (Unix milliseconds): {message.date_ms}")
        print(f"Subscription ID: {message.subscription_id}")
        print(f"Message: {json.dumps(message.body, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
