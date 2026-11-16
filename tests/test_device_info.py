"""Mock-only tests for the read-only device inventory CLI."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import device_info


def completed(output: str = "", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, output.encode(), b"private device output")


class AdbTests(unittest.TestCase):
    @patch("device_info.subprocess.run")
    def test_uses_explicit_device_and_fixed_read_only_command(self, run):
        run.return_value = completed("Samsung\n")
        self.assertEqual(device_info.adb("serial-1", "shell", "getprop", "ro.product.model"), "Samsung")
        run.assert_called_once_with(
            ["adb", "-s", "serial-1", "shell", "getprop", "ro.product.model"],
            capture_output=True,
            timeout=device_info.TIMEOUT_SECONDS,
            check=False,
        )

    @patch("device_info.subprocess.run")
    def test_timeout_is_reported_without_device_output(self, run):
        run.side_effect = subprocess.TimeoutExpired(["adb"], device_info.TIMEOUT_SECONDS)
        with self.assertRaisesRegex(device_info.DeviceInfoError, "timed out"):
            device_info.adb("serial-1", "shell", "getprop", "ro.product.model")

    @patch("device_info.subprocess.run")
    def test_failure_does_not_forward_stderr(self, run):
        run.return_value = completed("", returncode=1)
        with self.assertRaisesRegex(device_info.DeviceInfoError, "exit 1") as error:
            device_info.adb("serial-1", "shell", "getprop", "gsm.sim.state")
        self.assertNotIn("private device output", str(error.exception))


class ParsingTests(unittest.TestCase):
    @patch("device_info.adb")
    def test_battery(self, adb):
        adb.return_value = "level: 73\nstatus: 2\n"
        values = device_info.battery("serial-1")
        self.assertEqual(values["Battery level"].value, "73%")
        self.assertEqual(values["Battery status"].value, "charging")

    @patch("device_info.adb")
    def test_storage_parses_df_and_rejects_unexpected_output(self, adb):
        adb.return_value = "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/x 100000 40000 60000 40% /storage/emulated/0/Android/obb"
        self.assertEqual(device_info.data_storage("serial-1").value,
                         "39 MiB used / 59 MiB free / 98 MiB total")
        adb.return_value = "unexpected output"
        self.assertEqual(device_info.data_storage("serial-1").state, "unavailable")

    @patch("device_info.adb")
    def test_feature_listing_is_limited_to_telephony_and_euicc(self, adb):
        adb.return_value = (
            "feature:android.hardware.telephony\n"
            "feature:android.hardware.telephony.euicc\n"
            "feature:android.hardware.camera\n"
        )
        value = device_info.hardware_features("serial-1").value
        self.assertEqual(value, "android.hardware.telephony, android.hardware.telephony.euicc")

    @patch("device_info.adb")
    def test_sim_count_contains_states_but_not_identifiers(self, adb):
        adb.return_value = "READY,ABSENT"
        values = device_info.sim_info("serial-1")
        self.assertEqual(values["SIM state entries"].value, "2")
        self.assertEqual(values["SIM states"].value, "READY, ABSENT")

    @patch("device_info.adb")
    def test_empty_sim_property_is_unavailable(self, adb):
        adb.return_value = ""
        self.assertEqual(device_info.sim_info("serial-1")["SIM state entries"].state, "unavailable")

    @patch("device_info.adb")
    def test_property_rejects_control_characters_and_unbounded_values(self, adb):
        adb.return_value = "Model\x1b[31m"
        self.assertEqual(device_info.query_property("serial-1", "ro.product.model").state,
                         "unavailable")
        adb.return_value = "Model\u202eSpoof"
        self.assertEqual(device_info.query_property("serial-1", "ro.product.model").state,
                         "unavailable")
        adb.return_value = "M" * 129
        self.assertEqual(device_info.query_property("serial-1", "ro.product.model").state,
                         "unavailable")

    @patch("device_info.adb")
    def test_sensitive_information_is_not_queried(self, adb):
        adb.side_effect = lambda _device, *args: {
            ("shell", "getprop", "ro.product.manufacturer"): "Samsung",
            ("shell", "getprop", "ro.product.model"): "Model",
            ("shell", "getprop", "ro.build.version.release"): "15",
            ("shell", "getprop", "ro.build.version.sdk"): "35",
            ("shell", "dumpsys", "battery"): "level: 50\nstatus: 1",
            ("shell", "df", "-k", "/data"): "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/x 100000 40000 60000 40% /storage/emulated/0/Android/obb",
            ("shell", "pm", "list", "features"): "feature:android.hardware.telephony",
            ("shell", "getprop", "gsm.sim.state"): "READY",
        }[args]
        fields = dict(device_info.collect("serial-1"))
        self.assertEqual(fields["IMEI"].state, "withheld")
        self.assertEqual(fields["Account details"].state, "withheld")
        self.assertEqual(adb.call_count, 8)
        self.assertFalse(any("iphonesubinfo" in call.args for call in adb.call_args_list))

    @patch("device_info.adb")
    def test_one_failed_query_does_not_abort_other_fields(self, adb):
        responses = {
            ("shell", "getprop", "ro.product.model"): "Model",
            ("shell", "getprop", "ro.build.version.release"): "15",
            ("shell", "getprop", "ro.build.version.sdk"): "35",
            ("shell", "dumpsys", "battery"): "level: 50\nstatus: 1",
            ("shell", "df", "-k", "/data"): "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/x 100000 40000 60000 40% /data",
            ("shell", "pm", "list", "features"): "feature:android.hardware.telephony",
            ("shell", "getprop", "gsm.sim.state"): "READY",
        }

        def fake_adb(_device, *args):
            if args == ("shell", "getprop", "ro.product.manufacturer"):
                raise device_info.DeviceInfoError("ADB query failed (exit 1)")
            return responses[args]

        adb.side_effect = fake_adb
        fields = dict(device_info.collect("serial-1"))
        self.assertEqual(fields["Manufacturer"].state, "error")
        self.assertEqual(fields["Model"].value, "Model")


if __name__ == "__main__":
    unittest.main()
