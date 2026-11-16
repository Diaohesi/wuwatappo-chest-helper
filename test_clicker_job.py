"""Exercise real Windows Job inheritance using only harmless Python sleepers.

No clicker executable, desktop window, or input API is used. A pipe barrier
prevents the parent from creating its child until it has joined the Job.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import unittest

from clicker_job import ProcessGroup


@unittest.skipUnless(os.name == "nt", "Windows Job Objects require Windows")
class ProcessGroupIntegrationTests(unittest.TestCase):
    def test_restart_child_stays_owned_and_unrelated_process_survives(self):
        deadline = time.monotonic()+6.
        group = None
        parent = None
        unrelated = None
        reader = None
        output = queue.Queue()
        flags = subprocess.CREATE_NO_WINDOW
        parent_script = """
import os
import subprocess
import sys

print('READY', flush=True)
if sys.stdin.readline().strip() != 'SPAWN':
    raise SystemExit(2)
child = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(30)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL, close_fds=True,
    creationflags=subprocess.CREATE_NO_WINDOW)
print('CHILD ' + str(child.pid), flush=True)
# The test checks both PIDs before allowing the original process to exit.
sys.stdin.readline()
"""

        def remaining():
            value = deadline-time.monotonic()
            self.assertGreater(value, 0., "Job inheritance test exceeded its six-second deadline")
            return value

        def await_line():
            try:
                line = output.get(timeout=remaining())
            except queue.Empty:
                self.fail("Timed out waiting for the harmless test parent")
            self.assertTrue(line, "The harmless test parent exited before its synchronization output")
            return line.strip()

        def await_membership(predicate, message):
            while True:
                members = group.pids()
                if predicate(members):
                    return members
                timeout = deadline-time.monotonic()
                self.assertGreater(timeout, 0., message)
                time.sleep(min(.02, timeout))

        try:
            unrelated = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=flags)
            parent = subprocess.Popen(
                [sys.executable, "-u", "-c", parent_script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                close_fds=True, creationflags=flags)

            def read_output():
                for line in parent.stdout:
                    output.put(line)
                output.put("")

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            self.assertEqual(await_line(), "READY")
            group = ProcessGroup()
            group.assign(parent)
            self.assertIn(parent.pid, group.pids())
            self.assertNotIn(unrelated.pid, group.pids())

            parent.stdin.write("SPAWN\n")
            parent.stdin.flush()
            child_line = await_line()
            self.assertTrue(child_line.startswith("CHILD "), child_line)
            child_pid = int(child_line.split()[1])
            members = await_membership(
                lambda values: parent.pid in values and child_pid in values,
                "The spawned child was not inherited into the Job")
            self.assertNotIn(unrelated.pid, members)

            parent.stdin.write("EXIT\n")
            parent.stdin.flush()
            self.assertEqual(parent.wait(timeout=remaining()), 0)
            members = await_membership(
                lambda values: parent.pid not in values and child_pid in values,
                "The Job lost the live child when its original parent exited")
            self.assertNotIn(unrelated.pid, members)
            self.assertIsNone(unrelated.poll())

            group.terminate()
            self.assertEqual(await_membership(lambda values: not values,
                                             "Job termination left a child alive"), [])
            self.assertIsNone(unrelated.poll(), "An unrelated process was terminated")
        finally:
            # The Job kills every inherited test child even if an assertion
            # fails before its PID can be received from the pipe.
            if group is not None:
                try:
                    group.terminate()
                except OSError:
                    # Closing the final handle still applies KILL_ON_JOB_CLOSE.
                    pass
                finally:
                    group.close()
            for process in (parent, unrelated):
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
            for process in (parent, unrelated):
                if process is not None:
                    process.wait(timeout=1.)
            if reader is not None:
                reader.join(timeout=.5)
            if parent is not None:
                for stream in (parent.stdin, parent.stdout):
                    if stream is not None:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
