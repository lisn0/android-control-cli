from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import android_mcp
import phone


try:
    mcp_major, mcp_minor, _ = version("mcp").split(".", 2)
    HAS_SUPPORTED_MCP = mcp_major == "1" and int(mcp_minor) >= 28
except (PackageNotFoundError, ValueError):
    HAS_SUPPORTED_MCP = False


ADB_DEVICES = """List of devices attached
primary device product:synthetic model:example transport_id:1
secondary device transport_id:2
revoked unauthorized usb:1-2 transport_id:3
disconnected offline transport_id:4
restricted no permissions usb:1-1
invalid/serial device
"""


class DeviceScopeTests(unittest.TestCase):
    @patch.object(phone, "devices", return_value=ADB_DEVICES)
    def test_list_filters_to_authorized_serial_and_state(self, _devices):
        self.assertEqual(android_mcp.authorized_devices(), [
            {"serial": "primary", "state": "device"},
            {"serial": "secondary", "state": "device"},
        ])
        self.assertEqual(android_mcp.authorized_devices("secondary"), [
            {"serial": "secondary", "state": "device"},
        ])

    @patch.object(phone, "devices")
    def test_device_must_be_explicit_and_match_scope(self, devices):
        with self.assertRaisesRegex(phone.PhoneError, "explicit valid device serial"):
            android_mcp.require_device(None, None)
        with self.assertRaisesRegex(phone.PhoneError, "outside the server's configured scope"):
            android_mcp.require_device("secondary", "primary")
        devices.assert_not_called()

    @patch.object(phone, "devices", side_effect=[ADB_DEVICES, ADB_DEVICES.replace("primary device", "primary offline")])
    def test_device_authorization_is_checked_each_time(self, _devices):
        self.assertEqual(android_mcp.require_device(None, "primary"), "primary")
        with self.assertRaisesRegex(phone.PhoneError, "not connected and authorized"):
            android_mcp.require_device(None, "primary")

    def test_input_requires_a_startup_device(self):
        with self.assertRaisesRegex(phone.PhoneError, "--allow-input requires --device"):
            android_mcp.create_server(allow_input=True)


@unittest.skipUnless(HAS_SUPPORTED_MCP, "optional MCP SDK mcp>=1.28,<2 required")
class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_and_input_profiles_over_stdio(self):
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        checkout = Path(__file__).resolve().parents[1]
        for args, expected_tools in (
            ([], {"list_devices", "foreground", "snapshot"}),
            (["--device", "primary", "--allow-input"], {"list_devices", "foreground", "snapshot", "tap_label"}),
        ):
            with self.subTest(args=args):
                server = StdioServerParameters(
                    command=sys.executable,
                    args=["-B", str(checkout / "android_mcp.py"), *args],
                    cwd=checkout,
                )
                with anyio.fail_after(15):
                    async with stdio_client(server) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                            self.assertEqual(set(tools), expected_tools)
                            self.assertTrue(tools["snapshot"].annotations.readOnlyHint)
                            if "tap_label" in tools:
                                self.assertTrue(tools["tap_label"].annotations.destructiveHint)
                                arguments = {"serial": "secondary"}
                                reason = "outside the server's configured scope"
                            else:
                                arguments = {}
                                reason = "explicit valid device serial"
                            result = await session.call_tool("foreground", arguments)
                            self.assertTrue(result.isError)
                            self.assertIn(reason, result.content[0].text)

    async def test_guarded_snapshot_and_tap_use_existing_phone_operations(self):
        from mcp.server.fastmcp.exceptions import ToolError

        safe = ET.fromstring(
            '<hierarchy><node class="android.widget.Button" text="Continue" enabled="true" '
            'clickable="true" bounds="[10,20][110,80]"/></hierarchy>',
        )
        sensitive = ET.fromstring('<hierarchy><node password="true" text="secret"/></hierarchy>')
        server = android_mcp.create_server("primary", allow_input=True)
        with patch.object(phone, "devices", return_value=ADB_DEVICES), \
                patch.object(phone, "fetch_tree", side_effect=[(safe, b"safe")] * 3 + [(sensitive, b"secret")] * 2), \
                patch.object(phone, "screen_size", return_value=(1080, 2400)), \
                patch.object(phone, "adb") as adb, patch.object(phone, "save_state") as save_state:
            content, _ = await server.call_tool("snapshot", {})
            self.assertIn('"Continue"', content[0].text)
            save_state.assert_not_called()
            await server.call_tool("tap_label", {"label": "Continue"})
            for tool, arguments in (("snapshot", {}), ("tap_label", {"label": "Continue"})):
                with self.assertRaisesRegex(ToolError, "sensitive authentication or recovery") as error:
                    await server.call_tool(tool, arguments)
                self.assertNotIn("secret", str(error.exception))
        adb.assert_called_once_with("primary", "shell", "input", "tap", "60", "50")
