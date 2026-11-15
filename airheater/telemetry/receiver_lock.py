"""Local receiver exclusion, with crash recovery on the Linux deployment."""
from contextlib import contextmanager
import os


@contextmanager
def receiver_lock(state):
    state.mkdir(exist_ok=True)
    legacy = state / "azure_receiver.lock"
    if os.name != "posix":
        # Preserve the existing Windows guard; systemd runs on Linux only.
        fd = os.open(str(legacy), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            yield
        finally:
            os.close(fd)
            legacy.unlink()
        return

    import fcntl
    # Never delete this inode: waiters and holders must lock the same file.
    with (state / "azure_receiver.process.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Old receiver versions used existence alone. Refuse migration until
        # the operator has stopped the old process and resolved its lock.
        if legacy.exists():
            raise RuntimeError("Legacy receiver lock requires operator inspection")
        yield
        # Closing releases the lock, including after SIGTERM/SIGKILL or reboot.
