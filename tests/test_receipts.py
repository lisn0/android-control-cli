import json
from datetime import datetime, timezone
import unittest
from pathlib import Path
from unittest.mock import call, patch

import receipts
from receipts import ReceiptError, latest_records, make_record, parse_ledger


class ReceiptLedgerTests(unittest.TestCase):
    def test_uncertain_submission_requires_reconciliation_action(self):
        with self.assertRaisesRegex(ReceiptError, "requires --next-check"):
            make_record(brand="Example Studio", platform="Facebook", state="submitted-uncertain")
        item = make_record(brand="Example Studio", platform="Facebook", state="submitted-uncertain",
                           next_check="Check Page switcher before retrying", now=datetime(2026, 11, 15, tzinfo=timezone.utc))
        self.assertEqual(item["next_check"], "Check Page switcher before retrying")
        self.assertEqual(item["recorded_at"], "2026-11-15T00:00:00+00:00")

    def test_handle_verified_is_separate_from_account_verified(self):
        item = make_record(brand="Sample Films", platform="Facebook", state="created",
                           handle="sample.films", handle_state="proposed")
        self.assertEqual(item["state"], "created")
        self.assertEqual(item["handle_state"], "proposed")
        with self.assertRaisesRegex(ReceiptError, "must include the visible handle"):
            make_record(brand="X", platform="Instagram", state="verified", handle_state="verified",
                        evidence="Own profile readback")

    def test_secret_and_multiline_notes_are_rejected(self):
        for note in ("OTP: 829104", "password=hunter2", "first line\nsecond line"):
            with self.subTest(note=note), self.assertRaises(ReceiptError):
                make_record(brand="X", platform="Instagram", state="blocked", note=note)

    def test_duplicate_verified_creation_is_rejected_without_reconcile(self):
        previous = {"brand": "Example Studio", "platform": "Facebook", "state": "verified",
                    "recorded_at": "2026-11-15T00:00:00+00:00"}
        with self.assertRaisesRegex(ReceiptError, "already verified"):
            make_record(brand="example studio", platform="facebook", state="verified", existing=[previous])
        followup = make_record(brand="Example Studio", platform="Facebook", state="verified",
                               evidence="Rechecked profile identity", existing=[previous], reconcile=True)
        self.assertEqual(followup["state"], "verified")

    def test_uncertain_status_requires_reconciliation_before_final_transitions(self):
        previous = {"brand": "Example Studio", "platform": "Facebook", "state": "submitted-uncertain",
                    "recorded_at": "2026-11-15T00:00:00+00:00"}
        for state in ("planned", "in-progress", "created", "verified"):
            with self.subTest(state=state), self.assertRaisesRegex(ReceiptError, "must be reconciled"):
                make_record(brand="Example Studio", platform="Facebook", state=state,
                            evidence="Profile switcher readback", existing=[previous])
        resolved = make_record(brand="Example Studio", platform="Facebook", state="verified",
                               evidence="Exact Page identity visible in switcher", existing=[previous], reconcile=True)
        self.assertEqual(resolved["state"], "verified")

    def test_verified_requires_identity_readback_evidence(self):
        with self.assertRaisesRegex(ReceiptError, "requires --evidence"):
            make_record(brand="Example Archive", platform="Facebook", state="verified")

    def test_append_history_parser_and_latest_per_pair(self):
        first = make_record(brand="Example Studio", platform="Facebook", state="created")
        second = make_record(brand="Example Studio", platform="Facebook", state="verified",
                             evidence="Own profile readback", existing=[first], reconcile=True)
        serialized = "\n".join(json.dumps(x) for x in [first, second])
        records = parse_ledger(serialized)
        self.assertEqual(len(records), 2)
        self.assertEqual(latest_records(records)[("example studio", "facebook")]["state"], "verified")

    def test_malformed_line_fails_closed(self):
        with self.assertRaisesRegex(ReceiptError, "invalid JSON"):
            parse_ledger('{"brand":')

    def test_ledger_rejects_wrong_types_and_unknown_states(self):
        base = {"brand": "Example Studio", "platform": "Facebook", "state": "planned",
                "recorded_at": "2026-11-15T00:00:00+00:00"}
        cases = [({**base, "brand": ["Example Studio"]}, "invalid brand"),
                 ({**base, "platform": None}, "invalid brand"),
                 ({**base, "state": "done"}, "invalid brand"),
                 ({**base, "recorded_at": 123}, "invalid brand"),
                 ({**base, "handle_state": "confirmed"}, "invalid handle state"),
                 ({**base, "note": {"raw": "data"}}, "invalid note")]
        for item, message in cases:
            with self.subTest(item=item), self.assertRaisesRegex(ReceiptError, message):
                parse_ledger(json.dumps(item))

    def test_record_append_is_locked_across_read_validate_and_write(self):
        path = Path("unused-ledger.jsonl")
        order = []
        def flock(fd, mode):
            order.append(("lock", mode))
        def read(fd, size):
            order.append(("read",))
            return b""
        def write(fd, payload):
            order.append(("write",))
            return len(payload)
        with patch.object(receipts, "open_ledger", return_value=42), \
                patch.object(receipts.fcntl, "flock", side_effect=flock), \
                patch.object(receipts.os, "read", side_effect=read), \
                patch.object(receipts.os, "write", side_effect=write), \
                patch.object(receipts.os, "lseek"), patch.object(receipts.os, "fsync"), \
                patch.object(receipts.os, "close"):
            item = receipts.record_receipt(path, brand="Example Studio", platform="Facebook", state="planned")
        self.assertEqual(item["state"], "planned")
        self.assertLess(next(i for i, event in enumerate(order) if event[0] == "lock"),
                        next(i for i, event in enumerate(order) if event[0] == "read"))
        self.assertLess(next(i for i, event in enumerate(order) if event[0] == "read"),
                        next(i for i, event in enumerate(order) if event[0] == "write"))

    def test_open_ledger_never_uses_truncating_mode(self):
        path = Path("unused-ledger.jsonl")
        with patch.object(Path, "mkdir"), patch.object(receipts.os, "open", return_value=42) as open_file, \
                patch.object(receipts.os, "fchmod"):
            fd = receipts.open_ledger(path)
        self.assertEqual(fd, 42)
        flags = open_file.call_args.args[1]
        self.assertTrue(flags & receipts.os.O_RDWR)
        self.assertTrue(flags & receipts.os.O_CREAT)
        self.assertFalse(flags & receipts.os.O_TRUNC)

    def test_invalid_utf8_ledger_fails_with_safe_error(self):
        with patch.object(receipts.os, "lseek"), patch.object(receipts.os, "read", side_effect=[b"\xff", b""]):
            with self.assertRaisesRegex(ReceiptError, "invalid UTF-8"):
                receipts.read_fd(42)


if __name__ == "__main__":
    unittest.main()
