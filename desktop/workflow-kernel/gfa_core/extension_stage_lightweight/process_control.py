"""Launch and explicitly stop extension commands under a stable supervisor."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from typing import Any, Sequence, TypeAlias


ProcessIdentity: TypeAlias = dict[str, int | str]

CANCELLATION_GRACE_SECONDS = 0.2
_PROC_READ_ATTEMPTS = 3
_PROC_READ_RETRY_SECONDS = 0.005
_SUPERVISOR_MARKER = "--bbav5-managed-supervisor-v1"
_PR_SET_CHILD_SUBREAPER = 36
_ALLOWED_POPEN_OPTIONS = {"cwd", "env", "stdin", "stdout", "stderr"}
_PROC_UNCERTAIN = object()


def _read_proc_stat(pid: int) -> tuple[str, int, int, int] | object | None:
    """Return ``(state, pgrp, session, start_ticks)`` with race retries."""

    path = Path(f"/proc/{pid}/stat")
    for attempt in range(_PROC_READ_ATTEMPTS):
        try:
            text = path.read_text(encoding="utf-8")
            fields = text.rsplit(")", 1)[1].split()
            return fields[0], int(fields[2]), int(fields[3]), int(fields[19])
        except (FileNotFoundError, ProcessLookupError):
            if attempt + 1 < _PROC_READ_ATTEMPTS:
                time.sleep(_PROC_READ_RETRY_SECONDS)
        except (OSError, IndexError, ValueError):
            return _PROC_UNCERTAIN
    return None


def _pid_start_time(pid: int) -> int | None:
    stat = _read_proc_stat(pid)
    return stat[3] if isinstance(stat, tuple) else None


def _read_proc_cmdline(pid: int) -> list[str] | object | None:
    path = Path(f"/proc/{pid}/cmdline")
    for attempt in range(_PROC_READ_ATTEMPTS):
        try:
            return [
                item.decode("utf-8", errors="surrogateescape")
                for item in path.read_bytes().split(b"\0")
                if item
            ]
        except (FileNotFoundError, ProcessLookupError):
            if attempt + 1 < _PROC_READ_ATTEMPTS:
                time.sleep(_PROC_READ_RETRY_SECONDS)
        except OSError:
            return _PROC_UNCERTAIN
    return None


def _is_matching_supervisor(
    *, pid: int, pgid: int, start_time: int, token: str
) -> bool:
    stat = _read_proc_stat(pid)
    if (
        not isinstance(stat, tuple)
        or stat[0] == "Z"
        or stat[1:] != (pgid, pgid, start_time)
    ):
        return False
    command = _read_proc_cmdline(pid)
    if not isinstance(command, list) or len(command) < 7:
        return False
    try:
        return bool(
            Path(command[1]).resolve() == Path(__file__).resolve()
            and command[2] == _SUPERVISOR_MARKER
            and command[5] == token
            and command[6] == "--"
        )
    except OSError:
        return False


def _set_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _mirror_returncode(returncode: int) -> None:
    if returncode < 0:
        signum = -returncode
        if signum != signal.SIGKILL:
            signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        os._exit(128 + signum)
    os._exit(returncode)


def _supervise(command: Sequence[str], ready_fd: int, release_fd: int) -> None:
    """Run the exact argv, then wait for every descendant as a subreaper."""

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        _set_child_subreaper()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        try:
            child = subprocess.Popen(command)
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
    except BaseException:
        os.write(ready_fd, b"E")
        os.close(ready_fd)
        os.close(release_fd)
        raise

    os.write(ready_fd, b"1")
    os.close(ready_fd)
    os.read(release_fd, 1)
    os.close(release_fd)

    command_returncode: int | None = None
    while True:
        try:
            reaped_pid, status = os.waitpid(-1, 0)
        except ChildProcessError:
            break
        if reaped_pid == child.pid:
            command_returncode = os.waitstatus_to_exitcode(status)
            child.returncode = command_returncode

    if command_returncode is None:
        command_returncode = child.poll()
    _mirror_returncode(command_returncode if command_returncode is not None else 1)


def _supervisor_entry(argv: Sequence[str]) -> None:
    if len(argv) < 6 or argv[0] != _SUPERVISOR_MARKER or argv[4] != "--":
        raise SystemExit(2)
    ready_fd = int(argv[1])
    release_fd = int(argv[2])
    _token = argv[3]
    command = argv[5:]
    if not command:
        raise SystemExit(2)
    _supervise(command, ready_fd, release_fd)


def start_managed_process(
    args: Sequence[str], **kwargs: Any
) -> tuple[subprocess.Popen[bytes], ProcessIdentity]:
    """Start ``args`` below a stable session/group supervisor anchor."""

    if not args:
        raise ValueError("Managed process command must not be empty")
    unsupported = sorted(set(kwargs) - _ALLOWED_POPEN_OPTIONS)
    if unsupported:
        raise TypeError(f"Unsupported managed process option: {', '.join(unsupported)}")

    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    token = secrets.token_hex(16)
    supervisor_args = [
        sys.executable,
        str(Path(__file__).resolve()),
        _SUPERVISOR_MARKER,
        str(ready_write),
        str(release_read),
        token,
        "--",
        *(str(item) for item in args),
    ]
    try:
        process = subprocess.Popen(
            supervisor_args,
            start_new_session=True,
            pass_fds=(ready_write, release_read),
            **kwargs,
        )
    except BaseException:
        for file_descriptor in (ready_read, ready_write, release_read, release_write):
            os.close(file_descriptor)
        raise
    os.close(ready_write)
    os.close(release_read)

    try:
        ready = os.read(ready_read, 1)
        if ready != b"1":
            os.close(release_write)
            process.wait()
            raise RuntimeError("Managed supervisor could not launch the command")
        pid = process.pid
        pgid = os.getpgid(pid)
        start_time = _pid_start_time(pid)
        identity: ProcessIdentity = {
            "pid": pid,
            "pgid": pgid,
            "pid_start_time": start_time or 0,
            "supervisor_token": token,
        }
        if not _is_matching_supervisor(
            pid=pid,
            pgid=pgid,
            start_time=start_time or 0,
            token=token,
        ):
            os.kill(pid, signal.SIGKILL)
            os.close(release_write)
            process.wait()
            raise RuntimeError("Managed supervisor identity could not be verified")
        os.write(release_write, b"1")
        return process, identity
    finally:
        os.close(ready_read)
        try:
            os.close(release_write)
        except OSError:
            pass


def _group_membership(pgid: int, supervisor_pid: int) -> str:
    uncertain = False
    try:
        for proc_path in Path("/proc").iterdir():
            if not proc_path.name.isdigit() or int(proc_path.name) == supervisor_pid:
                continue
            stat = _read_proc_stat(int(proc_path.name))
            if stat is _PROC_UNCERTAIN:
                uncertain = True
                continue
            if stat is None:
                try:
                    uncertain = uncertain or proc_path.exists()
                except OSError:
                    uncertain = True
                continue
            if stat[0] != "Z" and stat[1] == pgid and stat[2] == pgid:
                return "member"
    except OSError:
        return "uncertain"
    return "uncertain" if uncertain else "empty"


def _supervisor_state(
    *,
    pid: int,
    pgid: int,
    start_time: int,
    token: str,
    verify_absent_exit: bool = False,
) -> str:
    stat = _read_proc_stat(pid)
    if stat is _PROC_UNCERTAIN:
        return "lost"
    if stat is None:
        if verify_absent_exit:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return (
                    "matching"
                    if _group_membership(pgid, pid) != "empty"
                    else "exited"
                )
            except OSError:
                pass
        return "lost"
    if stat[1:] != (pgid, pgid, start_time):
        return "lost"
    if stat[0] == "Z":
        if verify_absent_exit:
            return "matching"
        return "lost" if _group_membership(pgid, pid) != "empty" else "exited"
    if _is_matching_supervisor(
        pid=pid, pgid=pgid, start_time=start_time, token=token
    ):
        return "matching"
    stat = _read_proc_stat(pid)
    if (
        isinstance(stat, tuple)
        and stat[1:] == (pgid, pgid, start_time)
        and stat[0] == "Z"
    ):
        if verify_absent_exit:
            return "matching"
        return "lost" if _group_membership(pgid, pid) != "empty" else "exited"
    return "lost"


def _wait_for_supervisor_state(
    *,
    pid: int,
    pgid: int,
    start_time: int,
    token: str,
    grace_seconds: float,
    verify_absent_exit: bool = False,
) -> str:
    deadline = time.monotonic() + grace_seconds
    while True:
        state = _supervisor_state(
            pid=pid,
            pgid=pgid,
            start_time=start_time,
            token=token,
            verify_absent_exit=verify_absent_exit,
        )
        if (
            verify_absent_exit
            and state == "lost"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            continue
        if state != "matching":
            return state
        if time.monotonic() >= deadline:
            return "running"
        time.sleep(0.01)


def stop_process_group(
    identity: ProcessIdentity, *, grace_seconds: float = CANCELLATION_GRACE_SECONDS
) -> bool:
    """Stop only the group owned by the captured, persistent supervisor."""

    try:
        pid = int(identity["pid"])
        pgid = int(identity["pgid"])
        start_time = int(identity["pid_start_time"])
        token = str(identity["supervisor_token"])
    except (KeyError, TypeError, ValueError):
        return False
    if (
        pid <= 0
        or pgid <= 0
        or pid != pgid
        or start_time <= 0
        or not token
        or grace_seconds < 0
    ):
        return False
    if not _is_matching_supervisor(
        pid=pid, pgid=pgid, start_time=start_time, token=token
    ):
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    state = _wait_for_supervisor_state(
        pid=pid,
        pgid=pgid,
        start_time=start_time,
        token=token,
        grace_seconds=grace_seconds,
    )
    if state == "exited":
        return True
    if state == "lost":
        return False
    if not _is_matching_supervisor(
        pid=pid, pgid=pgid, start_time=start_time, token=token
    ):
        return False
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return _wait_for_supervisor_state(
        pid=pid,
        pgid=pgid,
        start_time=start_time,
        token=token,
        grace_seconds=grace_seconds,
        verify_absent_exit=True,
    ) == "exited"


if __name__ == "__main__":
    _supervisor_entry(sys.argv[1:])
