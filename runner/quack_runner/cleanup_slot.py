"""Empties a slot after a run, under the slot uid. Standalone script, stdlib only.

`kill(-1, SIGKILL)` from an unprivileged process signals every process of the same uid in this
PID namespace except itself, so a process the learner left behind (a fork, a daemon) does not
live into the next run. Then the slot directory is emptied.
"""

import os
import shutil
import signal
import sys


def main():
    for _ in range(20):
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            break
        except PermissionError:
            break
    directory = sys.argv[1]
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
