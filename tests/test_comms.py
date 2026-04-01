"""Unit tests for fleet/comms.py."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))

MESSAGE_SCHEMA_VERSION = "1.0"


class CommsTestBase(unittest.TestCase):
    def setUp(self):
        import db as dbmod
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ["FLEET_TEST_DB"] = self.tmp.name
        dbmod.init_db(self.tmp.name)
        self._dbmod = dbmod
        # Register test agents
        dbmod.register_agent("supervisor", "supervisor", 1)
        dbmod.register_agent("coder_1", "worker", 2)
        dbmod.register_agent("researcher", "worker", 3)

    def tearDown(self):
        try:
            self._dbmod.close_all()
        except Exception:
            pass
        os.environ.pop("FLEET_TEST_DB", None)
        os.unlink(self.tmp.name)


class TestWrapMessage(CommsTestBase):
    def test_adds_schema_version(self):
        from comms import _wrap_message
        result = json.loads(_wrap_message(json.dumps({"key": "val"})))
        self.assertEqual(result["_schema_version"], MESSAGE_SCHEMA_VERSION)

    def test_does_not_double_wrap(self):
        from comms import _wrap_message
        wrapped = _wrap_message(json.dumps({"_schema_version": "1.0", "x": 1}))
        result = json.loads(wrapped)
        self.assertEqual(result["_schema_version"], "1.0")
        self.assertEqual(result["x"], 1)

    def test_handles_invalid_json(self):
        from comms import _wrap_message
        result = _wrap_message("not-json")
        self.assertEqual(result, "not-json")


class TestCheckMessageVersion(CommsTestBase):
    def test_current_version_is_current(self):
        from comms import check_message_version
        body = json.dumps({"_schema_version": MESSAGE_SCHEMA_VERSION})
        version, is_current = check_message_version(body)
        self.assertEqual(version, MESSAGE_SCHEMA_VERSION)
        self.assertTrue(is_current)

    def test_old_version_not_current(self):
        from comms import check_message_version
        body = json.dumps({"_schema_version": "0.9"})
        version, is_current = check_message_version(body)
        self.assertFalse(is_current)

    def test_missing_version_defaults_0_0(self):
        from comms import check_message_version
        version, is_current = check_message_version(json.dumps({"x": 1}))
        self.assertEqual(version, "0.0")
        self.assertFalse(is_current)

    def test_invalid_json_returns_unknown(self):
        from comms import check_message_version
        version, is_current = check_message_version("bad")
        self.assertEqual(version, "unknown")
        self.assertFalse(is_current)


class TestPostAndGetMessage(CommsTestBase):
    def test_message_delivered_to_recipient(self):
        from comms import post_message, get_messages
        post_message("coder_1", "researcher", json.dumps({"text": "hello"}))
        msgs = get_messages("researcher")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from_agent"], "coder_1")

    def test_unread_only_filters_read(self):
        from comms import post_message, get_messages
        post_message("coder_1", "researcher", json.dumps({"text": "hi"}))
        # First fetch marks as read
        get_messages("researcher", unread_only=True)
        # Second fetch should return nothing with unread_only=True
        msgs = get_messages("researcher", unread_only=True)
        self.assertEqual(msgs, [])

    def test_message_has_schema_version(self):
        from comms import post_message, get_messages
        post_message("coder_1", "researcher", json.dumps({"text": "test"}))
        msgs = get_messages("researcher")
        body = json.loads(msgs[0]["body_json"])
        self.assertIn("_schema_version", body)

    def test_channel_filter(self):
        from comms import post_message, get_messages
        post_message("coder_1", "researcher", json.dumps({"x": 1}), channel="sup")
        post_message("coder_1", "researcher", json.dumps({"x": 2}), channel="fleet")
        sup_msgs = get_messages("researcher", channels=["sup"])
        self.assertEqual(len(sup_msgs), 1)
        body = json.loads(sup_msgs[0]["body_json"])
        self.assertEqual(body["x"], 1)

    def test_limit_respected(self):
        from comms import post_message, get_messages
        for i in range(10):
            post_message("coder_1", "researcher", json.dumps({"i": i}))
        msgs = get_messages("researcher", unread_only=False, limit=3)
        self.assertLessEqual(len(msgs), 3)


class TestBroadcastMessage(CommsTestBase):
    def test_fleet_broadcast_reaches_all_agents(self):
        from comms import broadcast_message, get_messages
        count = broadcast_message("supervisor", json.dumps({"announce": True}), channel="fleet")
        self.assertGreater(count, 0)

    def test_sup_channel_only_reaches_supervisors(self):
        from comms import broadcast_message, get_messages
        broadcast_message("supervisor", json.dumps({"sup": True}), channel="sup")
        # supervisor should get it
        sup_msgs = get_messages("supervisor")
        sup_bodies = [json.loads(m["body_json"]) for m in sup_msgs]
        self.assertTrue(any(b.get("sup") for b in sup_bodies))
        # workers should NOT get it via "sup" channel
        coder_msgs = get_messages("coder_1")
        self.assertEqual(coder_msgs, [])

    def test_pool_channel_only_reaches_workers(self):
        from comms import broadcast_message, get_messages
        broadcast_message("supervisor", json.dumps({"pool": True}), channel="pool")
        sup_msgs = get_messages("supervisor")
        self.assertEqual(sup_msgs, [])
        coder_msgs = get_messages("coder_1")
        self.assertTrue(len(coder_msgs) >= 1)


class TestNotesSystem(CommsTestBase):
    def test_post_note_returns_id(self):
        from comms import post_note
        note_id = post_note("fleet", "supervisor", json.dumps({"event": "boot"}))
        self.assertIsNotNone(note_id)
        self.assertIsInstance(note_id, int)

    def test_get_notes_retrieves_posted_note(self):
        from comms import post_note, get_notes
        post_note("fleet", "supervisor", json.dumps({"event": "startup"}))
        notes = get_notes("fleet")
        self.assertEqual(len(notes), 1)
        body = json.loads(notes[0]["body_json"])
        self.assertEqual(body["event"], "startup")

    def test_get_notes_channel_isolation(self):
        from comms import post_note, get_notes
        post_note("fleet", "supervisor", json.dumps({"ch": "fleet"}))
        post_note("sup", "supervisor", json.dumps({"ch": "sup"}))
        fleet_notes = get_notes("fleet")
        self.assertEqual(len(fleet_notes), 1)

    def test_get_note_count(self):
        from comms import post_note, get_note_count
        post_note("fleet", "coder_1", json.dumps({"n": 1}))
        post_note("fleet", "coder_1", json.dumps({"n": 2}))
        count = get_note_count("fleet")
        self.assertEqual(count, 2)

    def test_get_notes_limit(self):
        from comms import post_note, get_notes
        for i in range(5):
            post_note("fleet", "coder_1", json.dumps({"i": i}))
        notes = get_notes("fleet", limit=2)
        self.assertLessEqual(len(notes), 2)


if __name__ == "__main__":
    unittest.main()
