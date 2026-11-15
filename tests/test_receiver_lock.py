"""Exercise Linux receiver exclusion across real process termination."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from airheater.telemetry.receiver_lock import receiver_lock


@unittest.skipUnless(os.name == "posix", "Linux flock deployment tests")
class ReceiverLockTests(unittest.TestCase):
    def test_legacy_lock_requires_operator(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            legacy = state / "azure_receiver.lock"
            legacy.touch()
            with self.assertRaises(RuntimeError):
                with receiver_lock(state):
                    self.fail("Legacy lock bypassed")
            self.assertTrue(legacy.exists())

    def test_exclusion_and_release_after_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            script = (
                "from pathlib import Path; import sys,time; "
                "from airheater.telemetry.receiver_lock import receiver_lock\n"
                "with receiver_lock(Path(sys.argv[1])):\n"
                " print('locked', flush=True)\n"
                " time.sleep(60)\n"
            )
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", script, directory],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaises(OSError):
                    with receiver_lock(state):
                        self.fail("Concurrent receiver admitted")
                process.kill()
                process.wait(timeout=10)
                with receiver_lock(state):
                    self.assertTrue((state / "azure_receiver.process.lock").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
