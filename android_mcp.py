#!/usr/bin/env python3
"""Serve guarded Android Control tools over local MCP stdio."""
from __future__ import annotations

import argparse
import re
from typing import TYPE_CHECKING

if __package__:
    from . import phone
else:
    import phone

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


SERIAL = re.compile(r"[A-Za-z0-9._:-]+")


def authorized_devices(device: str | None = None) -> list[dict[str, str]]:
    devices = []
    for line in phone.devices().splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) < 2:
            continue
        serial, state = fields[:2]
        if state == "device" and SERIAL.fullmatch(serial) and (device is None or serial == device):
            devices.append({"serial": serial, "state": state})
    return devices


def require_device(requested: str | None, configured: str | None) -> str:
    if configured is not None and requested not in (None, configured):
        raise phone.PhoneError("device is outside the server's configured scope")
    serial = configured if configured is not None else requested
    if not serial or not SERIAL.fullmatch(serial):
        raise phone.PhoneError("provide an explicit valid device serial")
    if not authorized_devices(serial):
        raise phone.PhoneError("device is not connected and authorized in ADB")
    return serial


def create_server(device: str | None = None, *, allow_input: bool = False) -> FastMCP:
    if device is not None and not SERIAL.fullmatch(device):
        raise phone.PhoneError("--device requires an explicit valid device serial")
    if allow_input and device is None:
        raise phone.PhoneError("--allow-input requires --device")

    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ModuleNotFoundError as exc:
        if exc.name not in ("mcp", "mcp.server.fastmcp"):
            raise
        install_command = (
            "python -m pip install 'android-device-control[mcp]'"
            if __package__
            else "python -m pip install -r requirements-mcp.txt"
        )
        raise phone.PhoneError(f"install the optional MCP SDK with {install_command}") from exc

    from pydantic import Field

    server = FastMCP(
        "Android Control",
        instructions=(
            "Inspect a USB/ADB-connected Android phone through guarded, privacy-filtered reads. "
            "Typical flow: list_devices to find a serial, foreground to see which app is open, "
            "snapshot to read the on-screen UI. tap_label exists only when the server was started "
            "with --allow-input and scoped to one --device; it is the only tool that changes the "
            "phone, so re-run snapshot after each tap. Sensitive screens (passwords, sign-in, "
            "recovery, one-time codes) are refused. Without --device, pass an explicit serial."
        ),
    )
    serial_doc = (
        "ADB serial of the target device, as returned by list_devices. Required when the server "
        "has no --device scope; when scoped, omit it or pass the same serial (any other serial "
        "is rejected)."
    )

    def read_only(title: str) -> ToolAnnotations:
        return ToolAnnotations(
            title=title,
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )

    @server.tool(title="List Android devices", annotations=read_only("List Android devices"))
    def list_devices() -> list[dict[str, str]]:
        """List the Android devices ADB reports as connected and authorized, as a list of
        {serial, state} objects (state is always "device"). On a --device scoped server only
        that device can appear, and the list is empty if it is unplugged or unauthorized. Call
        this first to get a serial for foreground and snapshot; unauthorized or offline devices
        are omitted. Does not touch the device screen."""
        return authorized_devices(device)

    @server.tool(title="Get foreground app", annotations=read_only("Get foreground app"))
    def foreground(serial: str | None = Field(default=None, description=serial_doc)) -> str:
        """Return which app has focus on the device, as a "package/activity" string such as
        "org.mozilla.firefox/.App". Use it to confirm which app is open before reading or
        tapping; use snapshot instead to see what is on screen. Read-only. Fails if the device
        is not connected and authorized, the serial is out of scope, or Android exposes no
        focused window (for example while the screen is locked)."""
        return phone.foreground(require_device(serial, device))

    @server.tool(title="Snapshot screen UI", annotations=read_only("Snapshot screen UI"))
    def snapshot(serial: str | None = Field(default=None, description=serial_doc)) -> str:
        """Return a text outline of the current screen's accessibility tree, one line per
        visible element: [ref] Class "label" {click,edit,focused,disabled} (x,y), where x,y is
        the element's center in screen pixels. Use it to find the exact label to pass to
        tap_label and to verify the screen after a tap; use foreground for just the app name.
        Read-only. Password fields are masked, and the call is refused with an error on
        sign-in, password or recovery screens. Output can still contain private on-screen
        text, so treat it as sensitive."""
        target = require_device(serial, device)
        root, _ = phone.stable_tree(target)
        lines, _ = phone.build_refs(root)
        return "\n".join(lines)

    if allow_input:
        @server.tool(
            title="Tap element by label",
            annotations=ToolAnnotations(
                title="Tap element by label",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        def tap_label(
            label: str = Field(
                description=(
                    "Exact, case-sensitive text or content-description of the element, copied from "
                    "snapshot output without the quotes. No partial matching; leading or trailing "
                    "spaces are rejected."
                ),
            ),
        ) -> str:
            """Perform a real tap on the touchscreen of the one configured device at the center
            of the single enabled, clickable element whose label matches exactly. This changes
            phone state and can trigger anything the app does (send, buy, delete, toggle), and
            repeating it may repeat the effect. Available only because the server was started
            with --allow-input --device. Aborts without tapping if zero or several elements
            match, the screen is a sensitive sign-in/recovery screen, or the screen changed
            between checks. Returns a confirmation only, not the new screen: call snapshot
            afterwards to verify. Use snapshot first to find the label."""
            return phone.tap_label(require_device(None, device), label)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="scope the server to one explicit ADB serial")
    parser.add_argument("--allow-input", action="store_true", help="enable exact-label taps on --device")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.device, allow_input=args.allow_input)
    except phone.PhoneError as exc:
        parser.error(str(exc))
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
