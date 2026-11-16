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

    server = FastMCP("Android Control")
    read_only = ToolAnnotations(readOnlyHint=True)

    @server.tool(annotations=read_only)
    def list_devices() -> list[dict[str, str]]:
        """List connected, ADB-authorized devices by serial and state within the server scope."""
        return authorized_devices(device)

    @server.tool(annotations=read_only)
    def foreground(serial: str | None = None) -> str:
        """Read the focused Android package and activity. Supply a serial on an unscoped server."""
        return phone.foreground(require_device(serial, device))

    @server.tool(annotations=read_only)
    def snapshot(serial: str | None = None) -> str:
        """Read the guarded UI summary. Supply a serial on an unscoped server."""
        target = require_device(serial, device)
        root, _ = phone.stable_tree(target)
        lines, _ = phone.build_refs(root)
        return "\n".join(lines)

    if allow_input:
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
        def tap_label(label: str) -> str:
            """Tap one exact accessibility label on the configured device. Inspect the result separately."""
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
