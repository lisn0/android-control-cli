#!/usr/bin/env python3
"""Small fail-closed ADB controller for an explicitly selected Android device.

Authentication-screen detection uses English accessibility labels and password
field metadata. It is a conservative guard, not a complete OTP detector: an
unlabeled or non-English recovery screen can go undetected, so callers must
still inspect the live UI and handle sensitive screens manually.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

KEYS = {"back": 4, "home": 3, "enter": 66, "tab": 61,
        "delete": 67, "escape": 111, "up": 19, "down": 20,
        "left": 21, "right": 22}
EDIT_CLASS = "android.widget.EditText"
MAX_SETTLE_SECONDS = 10.0
SETTLE_INTERVAL_SECONDS = 0.35
MIN_SWIPE_MS = 100
MAX_SWIPE_MS = 1500
SENSITIVE_SCREEN = re.compile(
    r"\b(?:password|passcode|one[ -]?time(?: password)?|"
    r"verification(?: code)?|security code|authentication|authenticator|otp|2fa|"
    r"two[ -]?factor|account recovery|recover account|forgot password|reset password|"
    r"enter code|confirm your identity|identity check)\b", re.IGNORECASE)
SENSITIVE_AUTH_HEADING = re.compile(r"^\s*(?:sign[ -]?in|log[ -]?in)\b", re.IGNORECASE)
SAFE_FIELD_LABEL = re.compile(
    r"\b(?:name|first name|last name|full name|display name|page name|username|"
    r"user name|handle|email|email address|bio|description|website|search|title)\b",
    re.IGNORECASE)
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


class PhoneError(RuntimeError):
    pass


def run(args: list[str], *, timeout: int = 15) -> bytes:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PhoneError(f"command failed: {type(exc).__name__}") from exc
    if result.returncode:
        # Never relay stdout/stderr from the device: applications can include secrets.
        raise PhoneError(f"ADB command failed (exit {result.returncode})")
    return result.stdout


def adb(device: str, *args: str, timeout: int = 15) -> bytes:
    if not device or not re.fullmatch(r"[A-Za-z0-9._:-]+", device):
        raise PhoneError("provide an explicit valid device serial")
    return run(["adb", "-s", device, *args], timeout=timeout)


def devices() -> str:
    data = run(["adb", "devices", "-l"]).decode("utf-8", "replace")
    return "\n".join(data.splitlines()[:])


def foreground_component(output: str) -> str:
    """Extract only the focused package/component from a window-manager dump."""
    match = re.search(
        r"^\s*mCurrentFocus=Window\{[^}\r\n]*?\bu\d+\s+"
        r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
        r"([A-Za-z.][A-Za-z0-9_$.]*)\s*\}",
        output,
        re.MULTILINE,
    )
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    raise PhoneError("foreground package/component unavailable from Android window manager")


def foreground(device: str) -> str:
    output = adb(device, "shell", "dumpsys", "window").decode("utf-8", "replace")
    return foreground_component(output)


def validate_target_package(package: str) -> None:
    if not PACKAGE_NAME.fullmatch(package):
        raise PhoneError("package must be an explicit Android package name")


def validate_public_url(url: str) -> None:
    # Keep the URL out of diagnostics and support only ordinary, non-secret URLs.
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port  # Reject malformed ports.
    except ValueError as exc:
        raise PhoneError("URL is malformed; provide a non-secret HTTPS or loopback HTTP URL") from exc
    if (parsed.scheme not in ("https", "http") or not hostname
            or parsed.username is not None or parsed.password is not None
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in url)):
        raise PhoneError("URL must be a non-secret HTTPS URL or loopback HTTP URL without credentials")
    if parsed.scheme == "http":
        normalized_host = hostname.rstrip(".").lower()
        try:
            is_loopback = ipaddress.ip_address(normalized_host).is_loopback
        except ValueError:
            is_loopback = normalized_host == "localhost"
        if not is_loopback:
            raise PhoneError("HTTP is allowed only for a loopback URL")


def open_target(device: str, package: str, url: str | None = None) -> str:
    validate_target_package(package)
    if url is not None:
        validate_public_url(url)

    # Refuse to issue navigation from a visible login, recovery, OTP, or
    # authenticator screen. This checks accessibility metadata only.
    stable_tree(device)
    if url is None:
        launcher = ("-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER")
        result = adb(device, "shell", "cmd", "package", "resolve-activity", "--brief", *launcher, "-p", package)
        component = re.search(rf"^{re.escape(package)}/[A-Za-z.][A-Za-z0-9_$.]*\r?$",
                              result.decode("utf-8", "replace"), re.MULTILINE)
        if not component:
            raise PhoneError("no launcher activity found for the requested package")
        adb(device, "shell", "am", "start", "-W", *launcher,
            "-n", shlex.quote(component[0].strip()), timeout=30)
    else:
        adb(device, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW",
            "-d", shlex.quote(url), "-p", package, timeout=30)

    focused = foreground(device)
    focused_package = focused.split("/", 1)[0]
    if focused_package != package:
        raise PhoneError("navigation did not reach the requested foreground package")
    return f"foreground {focused}"


def fetch_tree(device: str) -> tuple[ET.Element, bytes]:
    # exec-out captures XML from /dev/tty without leaving screen data on device.
    raw = adb(device, "exec-out", "uiautomator", "dump", "/dev/tty", timeout=20)
    start = raw.find(b"<?xml")
    end_marker = raw.find(b"</hierarchy>", start)
    if start < 0 or end_marker < 0:
        raise PhoneError("fresh UI hierarchy unavailable; no action taken")
    xml = raw[start:end_marker + len(b"</hierarchy>")]
    try:
        return ET.fromstring(xml), xml
    except ET.ParseError as exc:
        raise PhoneError("UI hierarchy was malformed; no action taken") from exc


def has_password(root: ET.Element) -> bool:
    return any(node.get("password", "false").lower() == "true" for node in root.iter())


def sensitive_screen(root: ET.Element) -> bool:
    if has_password(root):
        return True
    # Sync is an entry in Firefox settings, separate from the account form.
    sync_titles = {child for node in root.iter()
                   if any(child.get("resource-id") == "android:id/summary"
                          and child.get("text") == "Synchronise tabs, bookmarks, passwords and more."
                          for child in node)
                   for child in node if child.get("package") == "org.mozilla.firefox"
                   and child.get("resource-id") == "android:id/title"}
    # Match only accessibility labels and hints; never put entered field values
    # in diagnostics. A conservative false positive is safer than exposing an
    # authentication, recovery, or one-time-code screen.
    labels = [value for node in root.iter()
              for key in ("text", "content-desc", "hint")
              if (value := node.get(key, ""))
              if not (key == "text" and node in sync_titles and value == "Sign in")
              # Firefox's menu advertises Sync before any login UI is open.
              if not (key == "content-desc" and node.get("package") == "org.mozilla.firefox"
                      and value == "Sign in Synchronise passwords, bookmarks and more")]
    return any(SENSITIVE_SCREEN.search(label) or SENSITIVE_AUTH_HEADING.match(label)
               for label in labels)


def guard_screen(root: ET.Element) -> None:
    if sensitive_screen(root):
        raise PhoneError("sensitive authentication or recovery screen; snapshot/action blocked")


def build_refs(root: ET.Element) -> tuple[list[str], dict[str, dict[str, object]]]:
    lines: list[str] = []
    refs: dict[str, dict[str, object]] = {}

    def visit(node: ET.Element, depth: int = 0) -> None:
        attrs = node.attrib
        bounds = attrs.get("bounds", "")
        if bounds and bounds != "[0,0][0,0]":
            text = attrs.get("text", "")
            desc = attrs.get("content-desc", "")
            cls = attrs.get("class", "").rsplit(".", 1)[-1]
            click = attrs.get("clickable") == "true"
            edit = attrs.get("class") == EDIT_CLASS
            # Edit fields may expose OTPs or other private input as node text.
            label = (desc or attrs.get("hint", "")) if edit else (text or desc)
            if click or edit or label:
                ref = str(len(refs) + 1)
                nums = [int(n) for n in re.findall(r"-?\d+", bounds)]
                if len(nums) != 4:
                    raise PhoneError("invalid accessibility bounds")
                x, y = (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2
                safe_label = "[hidden sensitive field]" if attrs.get("password") == "true" else label
                flags = []
                if click:
                    flags.append("click")
                if edit:
                    flags.append("edit")
                if attrs.get("focused") == "true":
                    flags.append("focused")
                if attrs.get("enabled") == "false":
                    flags.append("disabled")
                label_part = f" {json.dumps(safe_label, ensure_ascii=False)}" if safe_label else ""
                lines.append(f"[{ref}] {cls}{label_part} {{{','.join(flags)}}} ({x},{y})")
                refs[ref] = {"x": x, "y": y, "click": click, "edit": edit,
                             "enabled": attrs.get("enabled") != "false",
                             "focused": attrs.get("focused") == "true"}
        for child in node:
            visit(child, depth + 1)

    visit(root)
    return lines, refs


def state_path(device: str) -> Path:
    base = Path(os.environ.get("ANDROID_CONTROL_STATE", Path.home() / ".cache" / "android-control"))
    return base / (hashlib.sha256(device.encode()).hexdigest()[:20] + ".json")


def save_state(path: Path, digest: str, refs: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"sha256": digest, "refs": refs}, separators=(",", ":"))
    # Direct write avoids creating and then deleting temporary files. A partial
    # write fails schema validation on the next action, which stops safely.
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(payload)


def load_state(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict)
                or set(data) != {"sha256", "refs"}
                or not isinstance(data["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"])
                or not isinstance(data["refs"], dict)):
            raise ValueError("schema")
        for ref, target in data["refs"].items():
            if (not isinstance(ref, str) or not ref.isdigit()
                    or not isinstance(target, dict)
                    or set(target) != {"x", "y", "click", "edit", "enabled", "focused"}
                    or type(target["x"]) is not int
                    or type(target["y"]) is not int
                    or any(type(target[key]) is not bool
                           for key in ("click", "edit", "enabled", "focused"))):
                raise ValueError("target schema")
        return data
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PhoneError("no usable snapshot state; run snapshot first") from exc


def stable_tree(device: str, settle: float = 0.0) -> tuple[ET.Element, bytes]:
    if not 0 <= settle <= MAX_SETTLE_SECONDS:
        raise PhoneError(f"settle must be between 0 and {MAX_SETTLE_SECONDS:g} seconds")
    root, xml = fetch_tree(device)
    guard_screen(root)
    if settle == 0:
        return root, xml
    deadline = time.monotonic() + settle
    prior = hashlib.sha256(xml).digest()
    while time.monotonic() < deadline:
        time.sleep(min(SETTLE_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
        root, xml = fetch_tree(device)
        guard_screen(root)
        current = hashlib.sha256(xml).digest()
        if current == prior:
            return root, xml
        prior = current
    raise PhoneError("UI did not settle before timeout; no snapshot state saved")


def snapshot(device: str, settle: float = 0.0) -> str:
    root, xml = stable_tree(device, settle)
    lines, refs = build_refs(root)
    digest = hashlib.sha256(xml).hexdigest()
    save_state(state_path(device), digest, refs)
    return "\n".join([f"# device={device} refs={len(refs)} sha256={digest[:12]}", *lines])


def screen_size(device: str) -> tuple[int, int]:
    output = adb(device, "shell", "wm", "size").decode("ascii", "replace")
    sizes = re.findall(r"(\d+)x(\d+)", output)
    if not sizes:
        raise PhoneError("could not read screen dimensions")
    width, height = map(int, sizes[-1])
    if width <= 0 or height <= 0:
        raise PhoneError("invalid screen dimensions")
    return width, height


def checked_screen(device: str) -> tuple[ET.Element, bytes, int, int]:
    root, xml = fetch_tree(device)
    guard_screen(root)
    width, height = screen_size(device)
    return root, xml, width, height


def checked_gesture(device: str, action: list[str]) -> None:
    root, xml, _, _ = checked_screen(device)
    # Recheck immediately before input; refuse if the visible screen changed.
    again, again_xml = fetch_tree(device)
    guard_screen(again)
    if hashlib.sha256(xml).digest() != hashlib.sha256(again_xml).digest():
        raise PhoneError("screen changed before gesture; no action taken")
    adb(device, *action)


def scroll(device: str, direction: str) -> str:
    vectors = {
        "down": (0.50, 0.72, 0.50, 0.30),
        "up": (0.50, 0.30, 0.50, 0.72),
        "left": (0.80, 0.50, 0.20, 0.50),
        "right": (0.20, 0.50, 0.80, 0.50),
    }
    if direction not in vectors:
        raise PhoneError("direction must be up, down, left, or right")
    _, _, width, height = checked_screen(device)
    points = vectors[direction]
    x1, y1 = round(points[0] * width), round(points[1] * height)
    x2, y2 = round(points[2] * width), round(points[3] * height)
    checked_gesture(device, ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "300"])
    return f"scrolled {direction}"


def swipe(device: str, coords: list[int], duration_ms: int = 300) -> str:
    if len(coords) != 4 or any(type(value) is not int for value in coords):
        raise PhoneError("swipe requires four integer screen coordinates")
    if not MIN_SWIPE_MS <= duration_ms <= MAX_SWIPE_MS:
        raise PhoneError(f"swipe duration must be {MIN_SWIPE_MS}–{MAX_SWIPE_MS} ms")
    _, _, width, height = checked_screen(device)
    x1, y1, x2, y2 = coords
    if not all(0 <= x <= width and 0 <= y <= height for x, y in ((x1, y1), (x2, y2))):
        raise PhoneError("swipe coordinates fall outside the current screen")
    checked_gesture(device, ["shell", "input", "swipe", *(str(value) for value in coords), str(duration_ms)])
    return "swiped"


def tap_label(device: str, label: str) -> str:
    if not label or label.strip() != label:
        raise PhoneError("provide a non-empty exact accessibility label")
    root, xml = fetch_tree(device)
    guard_screen(root)
    parents = {child: parent for parent in root.iter() for child in parent}
    targets: dict[int, tuple[int, int]] = {}
    width, height = screen_size(device)
    for node in root.iter():
        if label not in (node.get("text", ""), node.get("content-desc", "")):
            continue
        target = node
        while target is not None and target.get("clickable") != "true":
            target = parents.get(target)
        if target is None or target.get("enabled") == "false":
            continue
        bounds = target.get("bounds", "")
        nums = [int(value) for value in re.findall(r"-?\d+", bounds)]
        if len(nums) != 4:
            continue
        left, top, right, bottom = nums
        if (left < 0 or top < 0 or right > width or bottom > height
                or left >= right or top >= bottom):
            continue
        x, y = (left + right) // 2, (top + bottom) // 2
        targets[id(target)] = (x, y)
    if len(targets) != 1:
        raise PhoneError(f"expected one enabled clickable match for label; found {len(targets)}")
    (x, y), = targets.values()
    again, again_xml = fetch_tree(device)
    guard_screen(again)
    if hashlib.sha256(xml).digest() != hashlib.sha256(again_xml).digest():
        raise PhoneError("screen changed before tap; no action taken")
    adb(device, "shell", "input", "tap", str(x), str(y))
    return f"tapped exact label {json.dumps(label, ensure_ascii=False)}"


def screenshot(device: str, output: str) -> str:
    if not output:
        raise PhoneError("choose a new screenshot path explicitly")
    root, before_xml = fetch_tree(device)
    guard_screen(root)
    path = Path(output).expanduser()
    if path.exists():
        raise PhoneError("screenshot path already exists; refusing to overwrite")
    if not path.parent.is_dir():
        raise PhoneError("screenshot parent directory must already exist")
    image = adb(device, "exec-out", "screencap", "-p", timeout=20)
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PhoneError("device did not return a PNG screenshot")
    # Accessibility read and framebuffer capture are separate operations. A
    # matching post-capture tree catches navigation/race changes, but cannot
    # prove that the image contains no sensitive pixels unsupported by labels.
    after, after_xml = fetch_tree(device)
    guard_screen(after)
    if hashlib.sha256(after_xml).digest() != hashlib.sha256(before_xml).digest():
        raise PhoneError("screen changed during screenshot; image was not saved")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PhoneError("screenshot path already exists; refusing to overwrite") from exc
    with os.fdopen(fd, "wb") as stream:
        stream.write(image)
    return str(path.resolve())


def checked_target(device: str, ref: str) -> dict[str, object]:
    path = state_path(device)
    old = load_state(path)
    root, xml = fetch_tree(device)
    guard_screen(root)
    if hashlib.sha256(xml).hexdigest() != old["sha256"]:
        raise PhoneError("screen changed since snapshot; no action taken; snapshot again")
    target = old["refs"].get(ref)
    if not target:
        raise PhoneError(f"unknown ref {ref}; no action taken")
    if not target["enabled"]:
        raise PhoneError("target is disabled; no action taken")
    _, current_refs = build_refs(root)
    current = current_refs.get(ref)
    keys = ("x", "y", "click", "edit", "enabled", "focused")
    if not current or any(current.get(key) != target.get(key) for key in keys):
        raise PhoneError("target no longer matches the fresh UI tree; no action taken")
    return target


def tap(device: str, ref: str) -> str:
    target = checked_target(device, ref)
    if not target["click"]:
        raise PhoneError("target is not an accessible clickable control")
    adb(device, "shell", "input", "tap", str(target["x"]), str(target["y"]))
    return f"tapped ref {ref}"


def key(device: str, key_name: str) -> str:
    code = KEYS.get(key_name.lower())
    if code is None:
        raise PhoneError(f"key must be one of: {', '.join(KEYS)}")
    root, _ = fetch_tree(device)
    guard_screen(root)
    adb(device, "shell", "input", "keyevent", str(code))
    return f"key {key_name.lower()}"


def focused_safe_field(root: ET.Element, placeholder: str | None = None) -> ET.Element:
    guard_screen(root)
    focused = [n for n in root.iter() if n.get("class") == EDIT_CLASS and n.get("focused") == "true"]
    if len(focused) != 1:
        raise PhoneError("exactly one focused editable field is required")
    node = focused[0]
    if node.get("enabled") == "false":
        raise PhoneError("focused field is disabled")
    value = node.get("text", "")
    is_exact_placeholder = (placeholder is not None and placeholder != ""
                            and value == placeholder and node.get("hint", "") == placeholder)
    if value and not is_exact_placeholder:
        raise PhoneError("focused field is not empty; refusing to append or replace")
    field_label = " ".join((node.get("hint", ""), node.get("content-desc", "")))
    if not SAFE_FIELD_LABEL.search(field_label):
        raise PhoneError("focused field lacks a recognized non-sensitive label; refusing text entry")
    return node


def type_text(device: str, text: str, placeholder: str | None = None) -> str:
    if not text or not text.isascii() or any(c in "\r\n\t" for c in text):
        raise PhoneError("type accepts non-empty, single-line ASCII text only")
    root, original_xml = fetch_tree(device)
    focused_safe_field(root, placeholder)
    # The focused field or screen may have changed while this request was
    # prepared. Require an identical fresh hierarchy immediately before input.
    before_send, before_send_xml = fetch_tree(device)
    focused_safe_field(before_send, placeholder)
    if hashlib.sha256(original_xml).digest() != hashlib.sha256(before_send_xml).digest():
        raise PhoneError("screen or focused field changed before text entry; no text sent")
    escaped = shlex.quote(text.replace(" ", "%s"))
    adb(device, "shell", "input", "text", escaped)
    after, _ = fetch_tree(device)
    guard_screen(after)
    matches = [n for n in after.iter() if n.get("class") == EDIT_CLASS and n.get("focused") == "true"]
    if (len(matches) != 1 or matches[0].get("enabled") == "false"
            or not SAFE_FIELD_LABEL.search(" ".join((matches[0].get("hint", ""),
                                                       matches[0].get("content-desc", ""))))
            or matches[0].get("text", "") != text):
        raise PhoneError("typed value did not verify in the focused field")
    return f"typed and verified {len(text)} characters"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("devices")
    for name in ("snapshot", "tap", "tap-label", "key", "type", "scroll", "swipe", "screenshot",
                 "foreground", "open"):
        sub = subs.add_parser(name)
        sub.add_argument("--device", required=True, help="explicit adb serial")
        if name == "open":
            sub.add_argument("package", help="explicit Android package name")
            sub.add_argument("--url", help="non-secret HTTPS URL, or HTTP on loopback only")
        elif name == "snapshot":
            sub.add_argument("--settle", type=float, default=0.0,
                             help=f"wait for a stable tree, at most {MAX_SETTLE_SECONDS:g} seconds")
        elif name == "tap":
            sub.add_argument("ref")
        elif name == "tap-label":
            sub.add_argument("label")
        elif name == "key":
            sub.add_argument("key")
        elif name == "type":
            sub.add_argument("--placeholder", help="exact accessibility text and hint treated as an empty field")
            sub.add_argument("text")
        elif name == "scroll":
            sub.add_argument("direction", choices=("up", "down", "left", "right"))
        elif name == "swipe":
            sub.add_argument("coords", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
            sub.add_argument("--ms", type=int, default=300)
        elif name == "screenshot":
            sub.add_argument("output", help="new PNG path; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        if args.command == "devices":
            print(devices())
        elif args.command == "snapshot":
            print(snapshot(args.device, args.settle))
        elif args.command == "tap":
            print(tap(args.device, args.ref))
        elif args.command == "tap-label":
            print(tap_label(args.device, args.label))
        elif args.command == "key":
            print(key(args.device, args.key))
        elif args.command == "type":
            print(type_text(args.device, args.text, args.placeholder))
        elif args.command == "scroll":
            print(scroll(args.device, args.direction))
        elif args.command == "swipe":
            print(swipe(args.device, args.coords, args.ms))
        elif args.command == "screenshot":
            print(screenshot(args.device, args.output))
        elif args.command == "foreground":
            print(foreground(args.device))
        elif args.command == "open":
            print(open_target(args.device, args.package, args.url))
        return 0
    except PhoneError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
