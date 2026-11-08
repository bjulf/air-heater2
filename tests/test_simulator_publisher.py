"""Tests for retry identity and sequence persistence; no Azure account needed."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airheater.telemetry.simulator import SequenceCounter, send_pending


class PublisherTests(unittest.TestCase):
    def test_sequence_survives_restart_and_excludes_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "counter"
            with SequenceCounter(path) as counter:
                self.assertEqual(counter.next(), 1)
                with self.assertRaises(FileExistsError):
                    SequenceCounter(path)
            with SequenceCounter(path) as counter:
                self.assertEqual(counter.next(), 2)
            path.write_text("broken")
            with self.assertRaises(ValueError):
                SequenceCounter(path)
            self.assertFalse(Path(str(path) + ".lock").exists())

    def test_retry_preserves_message_and_order(self):
        async def scenario():
            queue = asyncio.Queue(maxsize=2)
            queue.put_nowait("first")
            queue.put_nowait("second")
            with self.assertRaises(asyncio.QueueFull):
                queue.put_nowait("overflow")
            attempts = []

            async def send(body):
                attempts.append(body)
                if len(attempts) == 1:
                    raise OSError("offline")

            worker = asyncio.create_task(send_pending(queue, send, retry_seconds=0))
            await asyncio.wait_for(queue.join(), 1)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker
            self.assertEqual(attempts, ["first", "first", "second"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
