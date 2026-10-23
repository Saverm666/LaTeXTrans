import unittest

from src.gui.job_worker import EventLogWriter


class EventLogWriterTest(unittest.TestCase):
    def test_partial_lines_and_carriage_returns_are_forwarded(self):
        events = []
        writer = EventLogWriter(events.append)
        writer.write("Request ")
        writer.write("completed\n25%\r50%\r")
        self.assertEqual([e["message"] for e in events], ["Request completed", "25%", "50%"])

    def test_flush_keeps_last_partial_error(self):
        events = []
        writer = EventLogWriter(events.append)
        writer.write("Connection failed")
        writer.flush()
        self.assertEqual(events, [{"type": "log", "message": "Connection failed"}])
        writer.flush()
        self.assertEqual(len(events), 1)
