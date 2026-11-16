"""Mock-only tests for the read-only SMS inbox command."""
from __future__ import annotations

import subprocess
import shlex
import unittest
from unittest.mock import patch

import messages


def completed(output: str = "", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, output.encode(), b"private provider error")


class CommandTests(unittest.TestCase):
    def test_query_uses_fixed_inbox_projection_and_exact_sender_filter(self):
        command = messages.build_command("serial-1", "TEST-CARRIER")
        self.assertEqual(command, [
            "adb", "-s", "serial-1", "shell", "content", "query",
            "--uri", "content://sms/inbox",
            "--projection", "_id:address:body:date:sub_id",
            "--where", shlex.quote("address='TEST-CARRIER'"),
        ])

    def test_sender_quotes_are_escaped_as_sql_literal_not_shell_text(self):
        command = messages.build_command("serial-1", "A' OR 1=1 --")
        self.assertEqual(command[-2:], [
            "--where", shlex.quote("address='A'' OR 1=1 --'")
        ])

    def test_invalid_device_or_sender_is_rejected(self):
        for device, sender in (("", "TEST-CARRIER"), ("serial-1;bad", "TEST-CARRIER"),
                               ("serial-1", "TEST-CARRIER\nother")):
            with self.subTest(device=device, sender=sender):
                with self.assertRaises(messages.MessagesError):
                    messages.build_command(device, sender)


class ParsingTests(unittest.TestCase):
    @patch("messages.subprocess.run")
    def test_sender_listing_uses_address_only(self, run):
        run.return_value = completed("Row: 0 address=TEST-CARRIER\nRow: 1 address=TEST-CARRIER\nRow: 2 address=Meta\n")
        self.assertEqual(messages.senders("serial-1"), {"TEST-CARRIER": 2, "Meta": 1})
        self.assertEqual(run.call_args.args[0][-1], "address")

    @patch("messages.subprocess.run")
    def test_multiline_message_keeps_body(self, run):
        run.return_value = completed("Row: 0 _id=1, address=TEST-CARRIER, body=first\nsecond, date=10, sub_id=3\n")
        self.assertEqual(messages.query("serial-1", "TEST-CARRIER")[0].body, "first\nsecond")

    @patch("messages.subprocess.run")
    def test_cli_escapes_terminal_controls_without_losing_body(self, run):
        run.return_value = completed("Row: 0 _id=1, address=TEST-CARRIER, body=hello\x1b[31m, date=10, sub_id=3\n")
        with patch("sys.stdout") as stdout:
            self.assertEqual(messages.main(["--device", "serial-1", "--sender", "TEST-CARRIER"]), 0)
        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("\\u001b", output)
        self.assertNotIn("\x1b", output)

    @patch("messages.subprocess.run")
    def test_cli_filters_body_and_subscription(self, run):
        run.return_value = completed(
            "Row: 0 _id=1, address=TEST-CARRIER, body=Welcome, date=10, sub_id=3\n"
            "Row: 1 _id=2, address=TEST-CARRIER, body=welcome again, date=11, sub_id=2\n")
        with patch("sys.stdout") as stdout:
            self.assertEqual(messages.main(["--device", "serial-1", "--sender", "TEST-CARRIER", "--contains", "WELCOME", "--subscription", "3"]), 0)
        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("Welcome", output)
        self.assertNotIn("welcome again", output)

    @patch("messages.subprocess.run")
    def test_parses_message_body_and_metadata(self, run):
        run.return_value = completed(
            "Row: 0 _id=45, address=TEST-CARRIER, body=Bem-vindo, número da linha: +1-202-555-0101, "
            "date=1763000000000, sub_id=1\n"
        )
        result = messages.query("serial-1", "TEST-CARRIER")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], messages.Message(
            45, "TEST-CARRIER", "Bem-vindo, número da linha: +1-202-555-0101", 1763000000000, "1"
        ))
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["check"], False)

    @patch("messages.subprocess.run")
    def test_exact_sender_filter_is_enforced_even_if_provider_returns_other_rows(self, run):
        run.return_value = completed(
            "Row: 0 _id=1, address=UZ0, body=other, date=10, sub_id=1\n"
            "Row: 1 _id=2, address=TEST-CARRIER, body=selected, date=11, sub_id=1\n"
        )
        result = messages.query("serial-1", "TEST-CARRIER")
        self.assertEqual([item.body for item in result], ["selected"])

    @patch("messages.subprocess.run")
    def test_empty_inbox_is_supported(self, run):
        run.return_value = completed("No result found.\n")
        self.assertEqual(messages.query("serial-1", "TEST-CARRIER"), [])

    @patch("messages.subprocess.run")
    def test_failure_does_not_forward_private_provider_output(self, run):
        run.return_value = completed("private message body", returncode=1)
        with self.assertRaisesRegex(messages.MessagesError, "permission") as caught:
            messages.query("serial-1", "TEST-CARRIER")
        self.assertNotIn("private provider error", str(caught.exception))
        self.assertNotIn("private message body", str(caught.exception))

    @patch("messages.subprocess.run")
    def test_cli_limits_printed_messages_and_keeps_bodies_for_user(self, run):
        run.return_value = completed(
            "Row: 0 _id=1, address=TEST-CARRIER, body=older, date=10, sub_id=1\n"
            "Row: 1 _id=2, address=TEST-CARRIER, body=newer, date=11, sub_id=1\n"
        )
        with patch("sys.stdout") as stdout:
            status = messages.main(["--device", "serial-1", "--sender", "TEST-CARRIER", "--limit", "1"])
        self.assertEqual(status, 0)
        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("newer", output)
        self.assertNotIn("older", output)


if __name__ == "__main__":
    unittest.main()
