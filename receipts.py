#!/usr/bin/env python3
"""Append and inspect a local social account status ledger."""
from __future__ import annotations

import argparse
import fcntl
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

STATES = ("planned", "in-progress", "submitted-uncertain", "created", "verified", "blocked")
HANDLE_STATES = ("unknown", "proposed", "unverified", "verified", "not-applicable")
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:password|passcode|token|secret|otp|verification code)\s*[:=]\s*\S+"),
    re.compile(r"\b\d{6,8}\b"),
    re.compile(r"(?i)\b(?:bearer\s+)[A-Za-z0-9._~+/-]{12,}"),
)


class ReceiptError(ValueError):
    """Invalid or unsafe receipt operation."""


def default_ledger() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "android-control" / "social-accounts.jsonl"


def clean_text(value: str, field: str, *, limit: int = 500) -> str:
    value = value.strip()
    if len(value) > limit or "\n" in value or "\r" in value or "\0" in value:
        raise ReceiptError(f"{field} must be one line and at most {limit} characters")
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        raise ReceiptError(f"{field} appears to contain a secret or verification code")
    return value


def parse_ledger(contents: str) -> list[dict[str, Any]]:
    records = []
    for number, line in enumerate(contents.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReceiptError(f"ledger line {number} is invalid JSON") from exc
        required = {"brand", "platform", "state", "recorded_at"}
        if not isinstance(item, dict) or not required <= item.keys():
            raise ReceiptError(f"ledger line {number} has an invalid record shape")
        if (not isinstance(item["brand"], str) or not item["brand"].strip()
                or not isinstance(item["platform"], str) or not item["platform"].strip()
                or item["state"] not in STATES
                or not isinstance(item["recorded_at"], str) or not item["recorded_at"].strip()):
            raise ReceiptError(f"ledger line {number} has invalid brand, platform, state, or timestamp")
        if "handle_state" in item and item["handle_state"] not in HANDLE_STATES:
            raise ReceiptError(f"ledger line {number} has invalid handle state")
        for field in ("display_name", "handle", "evidence", "note", "next_check"):
            if field in item and item[field] is not None and not isinstance(item[field], str):
                raise ReceiptError(f"ledger line {number} has an invalid {field}")
        if item["state"] == "verified" and not item.get("evidence"):
            raise ReceiptError(f"ledger line {number} marks verified without identity evidence")
        if item.get("handle_state") == "verified" and not item.get("handle"):
            raise ReceiptError(f"ledger line {number} marks a handle verified without a handle")
        if item["state"] == "submitted-uncertain" and not item.get("next_check"):
            raise ReceiptError(f"ledger line {number} marks an uncertain submission without a next check")
        records.append(item)
    return records


def latest_records(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["brand"].casefold(), record["platform"].casefold())
        latest[key] = record
    return latest


def make_record(*, brand: str, platform: str, state: str, note: str = "",
                evidence: str = "", display_name: str = "", handle: str = "",
                handle_state: str = "unknown", next_check: str = "",
                existing: list[dict[str, Any]] | None = None,
                reconcile: bool = False, now: datetime | None = None) -> dict[str, Any]:
    brand = clean_text(brand, "brand", limit=100)
    platform = clean_text(platform, "platform", limit=60)
    if not brand or not platform:
        raise ReceiptError("brand and platform are required")
    if state not in STATES:
        raise ReceiptError(f"state must be one of: {', '.join(STATES)}")
    if handle_state not in HANDLE_STATES:
        raise ReceiptError(f"handle state must be one of: {', '.join(HANDLE_STATES)}")
    note = clean_text(note, "note")
    evidence = clean_text(evidence, "evidence", limit=300)
    display_name = clean_text(display_name, "display name", limit=100)
    handle = clean_text(handle, "handle", limit=120)
    next_check = clean_text(next_check, "next check", limit=300)
    current = latest_records(existing or []).get((brand.casefold(), platform.casefold()))
    if (current and current["state"] == "submitted-uncertain"
            and state in ("planned", "in-progress", "created", "verified")
            and not reconcile):
        raise ReceiptError("an uncertain submission must be reconciled before this transition; use --reconcile with new evidence")
    if current and current["state"] == "verified" and state in ("created", "verified") and not reconcile:
        raise ReceiptError("this brand/platform is already verified; use --reconcile only for a documented follow-up")
    if state == "verified" and not evidence:
        raise ReceiptError("verified status requires --evidence describing the identity readback")
    if state == "submitted-uncertain" and not next_check:
        raise ReceiptError("submitted-uncertain requires --next-check describing the reconciliation step")
    if handle_state == "verified" and not handle:
        raise ReceiptError("a verified handle must include the visible handle")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    return {
        "recorded_at": stamp,
        "brand": brand,
        "platform": platform,
        "state": state,
        "display_name": display_name or None,
        "handle": handle or None,
        "handle_state": handle_state,
        "evidence": evidence or None,
        "note": note or None,
        "next_check": next_check or None,
    }


def open_ledger(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock the ledger inode itself so concurrent commands cannot race, and
    # avoid a separate lock file or any truncating open mode.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def read_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptError("ledger contains invalid UTF-8") from exc


def write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("ledger append made no progress")
        view = view[written:]


def record_receipt(path: Path, **fields: Any) -> dict[str, Any]:
    fd = open_ledger(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        records = parse_ledger(read_fd(fd))
        record = make_record(existing=records, **fields)
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        os.lseek(fd, 0, os.SEEK_END)
        write_all(fd, payload)
        os.fsync(fd)
        return record
    finally:
        # Closing releases flock even if validation or the append fails.
        os.close(fd)


def read_records(path: Path) -> list[dict[str, Any]]:
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return []
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        return parse_ledger(read_fd(fd))
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=default_ledger(), help="ledger path (default: private user state directory)")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="append one status receipt")
    add.add_argument("--brand", required=True)
    add.add_argument("--platform", required=True)
    add.add_argument("--state", required=True, choices=STATES)
    add.add_argument("--display-name", default="")
    add.add_argument("--handle", default="")
    add.add_argument("--handle-state", default="unknown", choices=HANDLE_STATES)
    add.add_argument("--evidence", default="", help="brief evidence description; do not include secrets")
    add.add_argument("--note", default="", help="brief note; do not include secrets")
    add.add_argument("--next-check", default="", help="required when state is submitted-uncertain")
    add.add_argument("--reconcile", action="store_true", help="document new evidence resolving an uncertain submission or following a verified record")
    sub.add_parser("list", help="show the latest status per brand and platform")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            records = read_records(args.ledger)
            for (brand, platform), record in sorted(latest_records(records).items()):
                print(f"{brand}\t{platform}\t{record['state']}\thandle={record.get('handle') or '-'} ({record.get('handle_state', 'unknown')})")
            return 0
        item = record_receipt(args.ledger, brand=args.brand, platform=args.platform, state=args.state,
                              note=args.note, evidence=args.evidence,
                              display_name=args.display_name, handle=args.handle,
                              handle_state=args.handle_state, next_check=args.next_check,
                              reconcile=args.reconcile)
        print(f"recorded {item['brand']} / {item['platform']}: {item['state']}")
        return 0
    except (ReceiptError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
