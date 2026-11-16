import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import call, patch
import xml.etree.ElementTree as ET

MODULE = Path(__file__).resolve().parents[1] / "phone.py"
spec = importlib.util.spec_from_file_location("phone", MODULE)
phone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone)


def hierarchy(*nodes):
    return ET.fromstring('<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>")


class PhoneControlTests(unittest.TestCase):
    def test_foreground_extracts_only_focused_component(self):
        self.assertEqual(
            phone.foreground_component("  mCurrentFocus=Window{abc u0 org.mozilla.firefox/.BrowserApp}"),
            "org.mozilla.firefox/.BrowserApp",
        )
        with self.assertRaisesRegex(phone.PhoneError, "unavailable"):
            phone.foreground_component("mCurrentFocus=null\nmFocusedApp=ActivityRecord{abc u10 com.example.app/com.example.Main t7}")

    def test_foreground_uses_explicit_serial_and_window_manager(self):
        with patch.object(phone, "adb", return_value=b"mCurrentFocus=Window{a u0 com.example.app/.Main}") as adb:
            self.assertEqual(phone.foreground("serial-1"), "com.example.app/.Main")
        adb.assert_called_once_with("serial-1", "shell", "dumpsys", "window")

    def test_open_launcher_is_scoped_and_verifies_foreground(self):
        safe = hierarchy('<node class="android.widget.TextView" text="Home" bounds="[0,0][100,40]"/>')
        with patch.object(phone, "fetch_tree", return_value=(safe, b"safe")), \
                patch.object(phone, "adb", side_effect=[b"priority=0\r\ncom.example.app/.Main\r\n", b""]) as adb, \
                patch.object(phone, "foreground", return_value="com.example.app/.Main"):
            result = phone.open_target("serial-1", "com.example.app")
        self.assertEqual(result, "foreground com.example.app/.Main")
        launcher = ("-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER")
        self.assertEqual(adb.call_args_list, [
            call("serial-1", "shell", "cmd", "package", "resolve-activity", "--brief",
                 *launcher, "-p", "com.example.app"),
            call("serial-1", "shell", "am", "start", "-W", *launcher,
                 "-n", "com.example.app/.Main", timeout=30),
        ])

    def test_open_launcher_refuses_an_unresolved_or_foreign_component(self):
        safe = hierarchy('<node class="android.widget.TextView" text="Home" bounds="[0,0][100,40]"/>')
        for resolution in (b"No activity found", b"com.other.app/.Main\n"):
            with self.subTest(resolution=resolution), \
                    patch.object(phone, "fetch_tree", return_value=(safe, b"safe")), \
                    patch.object(phone, "adb", return_value=resolution) as adb:
                with self.assertRaisesRegex(phone.PhoneError, "no launcher activity"):
                    phone.open_target("serial-1", "com.example.app")
                self.assertEqual(adb.call_count, 1)

    def test_open_url_uses_package_scoped_view_intent_and_checks_package(self):
        safe = hierarchy('<node class="android.widget.TextView" text="Home" bounds="[0,0][100,40]"/>')
        url = "https://example.org/path?q=one%20two&view=feed#latest"
        with patch.object(phone, "fetch_tree", return_value=(safe, b"safe")), \
                patch.object(phone, "adb", return_value=b"") as adb, \
                patch.object(phone, "foreground", return_value="org.mozilla.firefox/.BrowserApp"):
            phone.open_target("serial-1", "org.mozilla.firefox", url)
        adb.assert_called_once_with(
            "serial-1", "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW",
            "-d", shlex.quote(url), "-p", "org.mozilla.firefox", timeout=30,
        )

    def test_sign_in_copy_does_not_block_but_login_heading_does(self):
        explanatory = hierarchy(
            '<node class="android.widget.TextView" text="Use your normal social account sign-in" bounds="[0,0][300,50]"/>',
            '<node class="android.widget.TextView" text="Choose one sign-in option" bounds="[0,60][300,100]"/>')
        self.assertFalse(phone.sensitive_screen(explanatory))
        login_heading = hierarchy(
            '<node class="android.widget.TextView" text="Log in with Facebook" bounds="[0,0][300,50]"/>')
        self.assertTrue(phone.sensitive_screen(login_heading))
        sign_in_heading = hierarchy(
            '<node class="android.widget.TextView" text="Sign in to Instagram" bounds="[0,0][300,50]"/>')
        self.assertTrue(phone.sensitive_screen(sign_in_heading))

    def test_open_blocks_sensitive_current_screen_before_navigation(self):
        sensitive = hierarchy('<node class="android.widget.TextView" text="Enter verification code" bounds="[0,0][100,40]"/>')
        with patch.object(phone, "fetch_tree", return_value=(sensitive, b"sensitive")), \
                patch.object(phone, "adb") as adb:
            with self.assertRaisesRegex(phone.PhoneError, "sensitive authentication or recovery"):
                phone.open_target("serial-1", "com.example.app")
        adb.assert_not_called()

    def test_firefox_menu_sync_entry_does_not_block_ordinary_controls(self):
        sync_entry = ('<node class="android.widget.Button" package="org.mozilla.firefox" '
                      'content-desc="Sign in Synchronise passwords, bookmarks and more"/>')
        self.assertFalse(phone.sensitive_screen(hierarchy(sync_entry, '<node text="Settings"/>')))
        self.assertTrue(phone.sensitive_screen(hierarchy(sync_entry, '<node password="true"/>')))
        self.assertTrue(phone.sensitive_screen(hierarchy(sync_entry, '<node text="Sign in to Firefox"/>')))

    def test_firefox_settings_sync_row_does_not_block_other_settings(self):
        title = ('<node package="org.mozilla.firefox" resource-id="android:id/title" text="Sign in"/>')
        row = ('<node>' + title + '<node resource-id="android:id/summary" '
               'text="Synchronise tabs, bookmarks, passwords and more."/></node>')
        self.assertFalse(phone.sensitive_screen(hierarchy(row, '<node text="Open links in apps"/>')))
        self.assertTrue(phone.sensitive_screen(hierarchy(title)))
        self.assertTrue(phone.sensitive_screen(hierarchy(row, '<node password="true"/>')))

    def test_open_rejects_bad_packages_urls_and_wrong_foreground(self):
        for package, url in (
            ("bad package", None),
            ("com.example.app", "http://example.org/"),
            ("com.example.app", "https://user:password@example.org/"),
            ("com.example.app", "file:///etc/passwd"),
        ):
            with self.subTest(package=package, url=url), patch.object(phone, "adb") as adb:
                with self.assertRaises(phone.PhoneError):
                    phone.open_target("serial-1", package, url)
                adb.assert_not_called()

        safe = hierarchy('<node class="android.widget.TextView" text="Home" bounds="[0,0][100,40]"/>')
        with patch.object(phone, "fetch_tree", return_value=(safe, b"safe")), \
                patch.object(phone, "adb") as adb, \
                patch.object(phone, "foreground", return_value="com.android.intentresolver/.ResolverActivity"):
            with self.assertRaisesRegex(phone.PhoneError, "did not reach"):
                phone.open_target("serial-1", "org.mozilla.firefox", "https://example.org/")
        self.assertTrue(adb.called)

    def test_snapshot_settle_waits_for_two_matching_trees(self):
        root = hierarchy('<node class="android.widget.Button" text="Ready" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        xml = ET.tostring(root)
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), \
                patch.object(phone, "fetch_tree", side_effect=[(root, b"first"), (root, b"first")]), \
                patch.object(phone.time, "sleep") as sleep:
            result = phone.snapshot("serial-1", 0.5)
        self.assertIn('"Ready"', result)
        sleep.assert_called_once()

    def test_snapshot_settle_is_bounded(self):
        with self.assertRaisesRegex(phone.PhoneError, "settle must be between"):
            phone.snapshot("serial-1", phone.MAX_SETTLE_SECONDS + 1)

    def test_snapshot_settle_timeout_fails_without_accepting_unstable_tree(self):
        root = hierarchy('<node class="android.widget.Button" text="Loading" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        with patch.object(phone, "fetch_tree", return_value=(root, b"changing")), \
                patch.object(phone.time, "monotonic", side_effect=[0.0, 1.0]), \
                patch.object(phone.time, "sleep"):
            with self.assertRaisesRegex(phone.PhoneError, "did not settle"):
                phone.stable_tree("serial-1", 0.5)

    def test_password_text_is_never_printed_or_cached(self):
        root = hierarchy('<node class="android.widget.EditText" text="secret123" password="true" '
                         'bounds="[0,0][100,50]" enabled="true"/>')
        lines, refs = phone.build_refs(root)
        self.assertNotIn("secret123", "\n".join(lines))
        self.assertNotIn("secret123", json.dumps(refs))
        self.assertTrue(phone.has_password(root))

    def test_auth_and_otp_screens_block_snapshot_without_echoing_screen_text(self):
        sensitive = hierarchy(
            '<node class="android.widget.TextView" text="Enter verification code" '
            'bounds="[0,0][200,50]"/><node class="android.widget.TextView" text="928104" '
            'bounds="[0,60][100,100]"/>')
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), \
                patch.object(phone, "fetch_tree", return_value=(sensitive, ET.tostring(sensitive))):
            with self.assertRaisesRegex(phone.PhoneError, "sensitive authentication or recovery") as caught:
                phone.snapshot("serial-1")
        self.assertNotIn("928104", str(caught.exception))
        self.assertEqual(list(Path(temp).glob("*.json")), [])

    def test_auth_screen_blocks_tap_key_and_type(self):
        sensitive = hierarchy(
            '<node class="android.widget.TextView" text="Account recovery" bounds="[0,0][200,50]"/>',
            '<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="[0,60][100,100]"/>',
            '<node class="android.widget.EditText" text="" focused="true" enabled="true" bounds="[0,110][100,150]"/>')
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), \
                patch.object(phone, "fetch_tree", return_value=(sensitive, ET.tostring(sensitive))), \
                patch.object(phone, "adb") as adb:
            # Seed harmless-looking stale state. The fresh screen guard must run first.
            phone.save_state(phone.state_path("serial-1"), "0" * 64,
                             {"1": {"x": 50, "y": 80, "click": True, "edit": False,
                                    "enabled": True, "focused": False}})
            for operation in (lambda: phone.tap("serial-1", "1"),
                              lambda: phone.key("serial-1", "enter"),
                              lambda: phone.type_text("serial-1", "hello")):
                with self.assertRaisesRegex(phone.PhoneError, "sensitive authentication or recovery"):
                    operation()
            adb.assert_not_called()

    def test_tap_checks_cached_target_against_fresh_tree(self):
        button = '<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="[10,20][110,80]"/>'
        root = hierarchy(button)
        raw = b"same screen bytes"
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), \
                patch.object(phone, "fetch_tree", return_value=(root, raw)), \
                patch.object(phone, "adb") as adb:
            phone.snapshot("serial-1")
            path = phone.state_path("serial-1")
            saved = json.loads(path.read_text())
            saved["refs"]["1"]["x"] += 1
            path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(phone.PhoneError, "no longer matches the fresh UI tree"):
                phone.tap("serial-1", "1")
            adb.assert_not_called()

    def test_tap_label_requires_one_fresh_enabled_match(self):
        root = hierarchy(
            '<node class="android.view.View" clickable="true" enabled="true" bounds="[10,20][110,80]">'
            '<node class="android.widget.TextView" text="Continue" bounds="[20,25][100,60]"/></node>')
        with patch.object(phone, "fetch_tree", side_effect=[(root, b"same"), (root, b"same")]), \
                patch.object(phone, "screen_size", return_value=(1080, 2400)), \
                patch.object(phone, "adb") as adb:
            phone.tap_label("serial-1", "Continue")
        self.assertEqual(adb.call_args.args[1:], ("shell", "input", "tap", "60", "50"))

    def test_tap_label_refuses_ambiguous_or_changed_screen(self):
        duplicate = hierarchy(
            '<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="[0,0][80,40]"/>',
            '<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="[0,50][80,90]"/>')
        with patch.object(phone, "fetch_tree", return_value=(duplicate, b"same")), \
                patch.object(phone, "screen_size", return_value=(1080, 2400)), \
                patch.object(phone, "adb") as adb:
            with self.assertRaisesRegex(phone.PhoneError, "found 2"):
                phone.tap_label("serial-1", "Continue")
            adb.assert_not_called()
        one = hierarchy('<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        changed = hierarchy('<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="[0,50][80,90]"/>')
        with patch.object(phone, "fetch_tree", side_effect=[(one, b"one"), (changed, b"changed")]), \
                patch.object(phone, "screen_size", return_value=(1080, 2400)), \
                patch.object(phone, "adb") as adb:
            with self.assertRaisesRegex(phone.PhoneError, "screen changed before tap"):
                phone.tap_label("serial-1", "Continue")
            adb.assert_not_called()

    def test_tap_label_refuses_zero_area_and_offscreen_bounds(self):
        for bounds in ("[5,5][5,30]", "[-1,0][80,40]", "[0,0][1081,40]"):
            root = hierarchy(f'<node class="android.widget.Button" text="Continue" clickable="true" enabled="true" bounds="{bounds}"/>')
            with self.subTest(bounds=bounds), \
                    patch.object(phone, "fetch_tree", return_value=(root, b"same")), \
                    patch.object(phone, "screen_size", return_value=(1080, 2400)), \
                    patch.object(phone, "adb") as adb:
                with self.assertRaisesRegex(phone.PhoneError, "found 0"):
                    phone.tap_label("serial-1", "Continue")
                adb.assert_not_called()

    def test_scroll_and_swipe_are_bounded_and_screen_guarded(self):
        root = hierarchy('<node class="android.widget.Button" text="Home" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        with patch.object(phone, "fetch_tree", side_effect=[(root, b"x")] * 5), \
                patch.object(phone, "screen_size", return_value=(1080, 2400)), \
                patch.object(phone, "adb") as adb:
            phone.scroll("serial-1", "down")
            self.assertEqual(adb.call_args.args[1:], ("shell", "input", "swipe", "540", "1728", "540", "720", "300"))
            with self.assertRaisesRegex(phone.PhoneError, "duration"):
                phone.swipe("serial-1", [0, 0, 100, 100], 9000)
            with self.assertRaisesRegex(phone.PhoneError, "outside"):
                phone.swipe("serial-1", [0, 0, 1081, 100], 300)
            self.assertEqual(adb.call_count, 1)

    def test_screenshot_is_explicit_and_refuses_existing_file(self):
        root = hierarchy('<node class="android.widget.Button" text="Home" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        with patch.object(phone, "fetch_tree", return_value=(root, b"safe")), \
                patch.object(phone.Path, "exists", return_value=True), \
                patch.object(phone, "adb") as adb:
            with self.assertRaisesRegex(phone.PhoneError, "already exists"):
                phone.screenshot("serial-1", "/tmp/existing.png")
            adb.assert_not_called()

    def test_screenshot_does_not_save_if_screen_changes_during_capture(self):
        before = hierarchy('<node class="android.widget.Button" text="Home" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        after = hierarchy('<node class="android.widget.Button" text="Code: 123456" clickable="true" enabled="true" bounds="[0,0][80,40]"/>')
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(phone, "fetch_tree", side_effect=[(before, b"before"), (after, b"after")]), \
                patch.object(phone, "adb", return_value=b"\x89PNG\r\n\x1a\nrest") as adb:
            path = Path(temp) / "capture.png"
            with self.assertRaisesRegex(phone.PhoneError, "screen changed during screenshot"):
                phone.screenshot("serial-1", str(path))
            self.assertFalse(path.exists())
            adb.assert_called_once()

    def test_malformed_cached_state_fails_closed_as_phone_error(self):
        malformed = [
            "not json",
            json.dumps([]),
            json.dumps({"sha256": "x", "refs": {}}),
            json.dumps({"sha256": "0" * 64, "refs": {"1": []}}),
            json.dumps({"sha256": "0" * 64, "refs": {"1": {
                "x": "50", "y": 80, "click": True, "edit": False,
                "enabled": True, "focused": False}}}),
        ]
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}):
            path = phone.state_path("serial-1")
            path.parent.mkdir(parents=True, exist_ok=True)
            for raw in malformed:
                with self.subTest(raw=raw):
                    path.write_text(raw)
                    with self.assertRaisesRegex(phone.PhoneError, "no usable snapshot state"):
                        phone.load_state(path)

    def test_snapshot_persists_only_hash_and_safe_target_metadata(self):
        root = hierarchy('<node class="android.widget.Button" text="Continue" clickable="true" '
                         'enabled="true" bounds="[10,20][110,80]"/>')
        raw = ET.tostring(root)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), \
                patch.object(phone, "fetch_tree", return_value=(root, raw)):
            output = phone.snapshot("serial-1")
            saved = next(Path(temp).glob("*.json")).read_text()
        self.assertIn("Continue", output)
        self.assertNotIn("Continue", saved)
        self.assertEqual(set(json.loads(saved)), {"sha256", "refs"})

    def test_tap_refuses_changed_screen(self):
        root = hierarchy('<node class="android.widget.Button" text="Continue" clickable="true" '
                         'enabled="true" bounds="[10,20][110,80]"/>')
        changed = hierarchy('<node class="android.widget.Button" text="Cancel" clickable="true" '
                            'enabled="true" bounds="[10,20][110,80]"/>')
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), \
                patch.object(phone, "fetch_tree", return_value=(root, ET.tostring(root))), \
                patch.object(phone, "adb") as adb:
            phone.snapshot("serial-1")
            with patch.object(phone, "fetch_tree", return_value=(changed, ET.tostring(changed))):
                with self.assertRaisesRegex(phone.PhoneError, "screen changed"):
                    phone.tap("serial-1", "1")
            adb.assert_not_called()

    def test_tap_requires_clickable_and_enabled_target(self):
        cases = [
            ('<node class="android.widget.TextView" text="label" enabled="true" bounds="[0,0][80,40]"/>', "not an accessible"),
            ('<node class="android.widget.Button" text="Next" clickable="true" enabled="false" bounds="[0,0][80,40]"/>', "disabled"),
        ]
        for xml, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp, \
                    patch.dict(os.environ, {"ANDROID_CONTROL_STATE": temp}), patch.object(phone, "adb") as adb:
                root = hierarchy(xml)
                with patch.object(phone, "fetch_tree", return_value=(root, ET.tostring(root))):
                    phone.snapshot("serial-1")
                    with self.assertRaisesRegex(phone.PhoneError, message):
                        phone.tap("serial-1", "1")
                adb.assert_not_called()

    def test_type_requires_one_empty_focused_editable_and_verifies(self):
        before = hierarchy('<node class="android.widget.EditText" hint="Page name" text="" focused="true" enabled="true" '
                           'bounds="[0,0][100,50]"/>')
        after = hierarchy('<node class="android.widget.EditText" hint="Page name" text="Example Studio" focused="true" enabled="true" '
                          'bounds="[0,0][100,50]"/>')
        with patch.object(phone, "fetch_tree", side_effect=[(before, b"before"), (before, b"before"), (after, b"after")]), \
                patch.object(phone, "adb") as adb:
            result = phone.type_text("serial-1", "Example Studio")
        self.assertIn("14 characters", result)
        self.assertEqual(adb.call_args.args[1:], ("shell", "input", "text", shlex.quote("Example%sStudio")))

    def test_type_accepts_only_explicit_exact_text_and_hint_placeholder(self):
        placeholder = hierarchy('<node class="android.widget.EditText" hint="Page name" text="Page name" focused="true" enabled="true" bounds="[0,0][100,50]"/>')
        after = hierarchy('<node class="android.widget.EditText" hint="Page name" text="Example Studio" focused="true" enabled="true" bounds="[0,0][100,50]"/>')
        with patch.object(phone, "fetch_tree", side_effect=[(placeholder, b"before"), (placeholder, b"before"), (after, b"after")]), \
                patch.object(phone, "adb") as adb:
            phone.type_text("serial-1", "Example Studio", placeholder="Page name")
        adb.assert_called_once()

    def test_type_placeholder_is_opt_in_and_requires_exact_hint_match(self):
        cases = [
            ('<node class="android.widget.EditText" hint="Page name" text="Page name" focused="true" enabled="true" bounds="[0,0][100,50]"/>', None),
            ('<node class="android.widget.EditText" hint="Page title" text="Page name" focused="true" enabled="true" bounds="[0,0][100,50]"/>', "Page name"),
            ('<node class="android.widget.EditText" hint="Page name" text="Other" focused="true" enabled="true" bounds="[0,0][100,50]"/>', "Page name"),
        ]
        for xml, opt_in in cases:
            with self.subTest(opt_in=opt_in, xml=xml), patch.object(phone, "fetch_tree", return_value=(hierarchy(xml), b"same")), \
                    patch.object(phone, "adb") as adb:
                with self.assertRaisesRegex(phone.PhoneError, "not empty"):
                    phone.type_text("serial-1", "Example Studio", placeholder=opt_in)
                adb.assert_not_called()

    def test_type_refuses_if_screen_or_focus_changes_before_send(self):
        before = hierarchy('<node class="android.widget.EditText" hint="Username" text="" focused="true" enabled="true" bounds="[0,0][80,40]"/>')
        changed = hierarchy('<node class="android.widget.EditText" hint="Email address" text="" focused="true" enabled="true" bounds="[0,0][80,40]"/>')
        with patch.object(phone, "fetch_tree", side_effect=[(before, b"before"), (changed, b"changed")]), \
                patch.object(phone, "adb") as adb:
            with self.assertRaisesRegex(phone.PhoneError, "changed before text entry"):
                phone.type_text("serial-1", "ordinary-text")
            adb.assert_not_called()

    def test_type_refuses_password_non_ascii_nonempty_and_unfocused(self):
        cases = [
            ('<node class="android.widget.EditText" text="" focused="true" password="true" bounds="[0,0][80,40]"/>', "sensitive authentication or recovery"),
            ('<node class="android.widget.EditText" text="already" focused="true" bounds="[0,0][80,40]"/>', "not empty"),
            ('<node class="android.widget.EditText" text="" focused="false" bounds="[0,0][80,40]"/>', "focused editable"),
        ]
        for xml, message in cases:
            with self.subTest(message=message), patch.object(phone, "fetch_tree", return_value=(hierarchy(xml), b"x")), \
                    patch.object(phone, "adb") as adb:
                with self.assertRaisesRegex(phone.PhoneError, message):
                    phone.type_text("serial-1", "hello")
                adb.assert_not_called()
        with self.assertRaisesRegex(phone.PhoneError, "ASCII"):
            phone.type_text("serial-1", "こんにちは")

    def test_type_refuses_unlabeled_focused_field(self):
        root = hierarchy('<node class="android.widget.EditText" text="" focused="true" enabled="true" bounds="[0,0][80,40]"/>')
        with patch.object(phone, "fetch_tree", return_value=(root, b"x")), patch.object(phone, "adb") as adb:
            with self.assertRaisesRegex(phone.PhoneError, "recognized non-sensitive label"):
                phone.type_text("serial-1", "123456")
            adb.assert_not_called()

    def test_device_serial_required_for_mutations(self):
        with self.assertRaisesRegex(phone.PhoneError, "explicit valid device serial"):
            phone.adb("", "shell", "input", "keyevent", "4")

    def test_type_verification_failure_is_explicit(self):
        before = hierarchy('<node class="android.widget.EditText" hint="Username" text="" focused="true" enabled="true" bounds="[0,0][80,40]"/>')
        after = hierarchy('<node class="android.widget.EditText" hint="Username" text="wrong" focused="true" enabled="true" bounds="[0,0][80,40]"/>')
        with patch.object(phone, "fetch_tree", side_effect=[(before, b"before"), (before, b"before"), (after, b"after")]), patch.object(phone, "adb"):
            with self.assertRaisesRegex(phone.PhoneError, "did not verify"):
                phone.type_text("serial-1", "hello")


if __name__ == "__main__":
    unittest.main()
