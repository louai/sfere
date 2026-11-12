"""Raw frames into an encoder process (ffmpeg, gst-launch) without ever blocking the caller.

A writer thread feeds the process's stdin from a short queue. If the process falls behind,
the oldest waiting frame is dropped and counted: a recording may skip a frame, the show
never stalls.
"""
from __future__ import annotations

import collections
import shutil
import subprocess
import threading


class FramePipe:
    def __init__(self, cmd: list[str], name: str, queue: int = 3):
        self.name = name
        self.cmd = cmd
        self.dropped = 0
        self.written = 0
        self.error = ""
        self._log = collections.deque(maxlen=30)
        self._q: collections.deque = collections.deque()
        self._max = queue
        self._cv = threading.Condition()
        self._closing = False
        if not shutil.which(cmd[0]):
            raise RuntimeError(f"{cmd[0]} isn't installed")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        threading.Thread(target=self._read_log, name=f"{name}:log", daemon=True).start()
        self._thread = threading.Thread(target=self._write, name=f"{name}:write", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None and not self.error

    def push(self, data: bytes) -> None:
        with self._cv:
            if self._closing:
                return
            if len(self._q) >= self._max:
                self._q.popleft()
                self.dropped += 1
            self._q.append(data)
            self._cv.notify()

    def _write(self):
        while True:
            with self._cv:
                while not self._q and not self._closing:
                    self._cv.wait()
                if not self._q:
                    break
                data = self._q.popleft()
            try:
                self.proc.stdin.write(data)
                self.written += 1
            except (BrokenPipeError, ValueError, OSError):
                self.error = self.last_log() or f"{self.name} stopped"
                break
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def _read_log(self):
        for line in self.proc.stderr:
            self._log.append(line.decode(errors="replace").rstrip())

    def last_log(self) -> str:
        return self._log[-1] if self._log else ""

    def close(self, timeout: float = 20.0) -> int:
        """Finish writing what is queued, then wait for the process to end. Returns its exit code."""
        with self._cv:
            self._closing = True
            self._cv.notify()
        self._thread.join(timeout)
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()
