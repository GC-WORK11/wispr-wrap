#!/usr/bin/env python3
"""laptop_engine — the laptop-webcam power path, managed by the daemon.

`POST /live/source {"source":"laptop"}` starts sign-flow's `sf-engine` (argv only, never a
shell): a headless Rust binary that opens the laptop webcam, runs YOLOX + RTMW-L whole-body pose
in process and POSTs one kp42 frame per pose back to THIS daemon's `/live/kp` — the same
endpointer, the same sign bank, the same actions as the phone camera, so a sign taught on either
camera lives in one bank. `{"source":"phone"}` stops it; the webcam light goes off with the
process. Exactly one camera feeds the endpointer at a time: while the laptop is the source,
frames from anything else are refused (wfd answers 409), and the other way round.

Honesty rules for /engine: "attached" means the engine process is alive AND its frames reached
the daemon within the last ATTACH_S seconds — a binary merely being installed is "available".
The engine writes its own live numbers (fps, pose/post ms p50/p95) to a status file that is
reported as-is.

Binary lookup: $WF_SF_ENGINE, `sf-engine` on PATH, then the install locations below.
Extra engine argv (camera index, replay seams for tests): $WF_SF_ENGINE_ARGS, split shell-style
by shlex but executed without a shell.
"""
import json
import os
import shlex
import shutil
import subprocess
import threading
import time

ATTACH_S = 3.0
STOP_WAIT_S = 3.0
INSTALL_PATHS = ("/usr/lib/sign-flow/sf-engine", "/usr/bin/sf-engine",
                 os.path.expanduser("~/.local/bin/sf-engine"))
SOURCES = ("phone", "laptop")


def _exe(p):
    return bool(p) and os.path.isfile(p) and os.access(p, os.X_OK)


class LaptopEngine:
    def __init__(self, port, key_file, state_dir, log=None):
        self.port = int(port)
        self.key_file = key_file
        self.state_dir = state_dir
        self.status_path = os.path.join(state_dir, "sf-engine.status.json")
        self.log_path = os.path.join(state_dir, "sf-engine.log")
        self.log = log or (lambda m: print(m, flush=True))
        self.lock = threading.RLock()
        self.proc = None
        self.source = "phone"
        self.started_at = None
        self.last_exit = None          # {"code", "at", "reason", "tail"}
        self.last_frame_at = 0.0       # time.time() of the last laptop frame accepted
        self.frames = 0

    # -- discovery -------------------------------------------------------

    def binary(self):
        forced = os.environ.get("WF_SF_ENGINE", "").strip()
        if forced:
            return forced if _exe(forced) else None
        p = shutil.which("sf-engine")
        if p:
            return p
        for c in INSTALL_PATHS:
            if _exe(c):
                return c
        return None

    def argv(self):
        b = self.binary()
        if not b:
            return None
        a = [b, "--url", "http://127.0.0.1:%d" % self.port,
             "--key-file", self.key_file, "--status", self.status_path]
        extra = os.environ.get("WF_SF_ENGINE_ARGS", "").strip()
        if extra:
            a += shlex.split(extra)
        return a

    # -- lifecycle -------------------------------------------------------

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _reap(self):
        """A child that exited on its own: record why, fall back to the phone camera."""
        if self.proc is None or self.proc.poll() is None:
            return
        code = self.proc.returncode
        tail = self._log_tail()
        self.last_exit = {"code": code, "at": time.time(), "tail": tail}
        self.log("[wfd] laptop engine exited (code %s): %s" % (code, tail))
        self.proc = None
        self.started_at = None
        if self.source == "laptop":
            self.source = "phone"

    def _log_tail(self):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 600))
                lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
            return lines[-1][:240] if lines else ""
        except OSError:
            return ""

    def start(self):
        with self.lock:
            self._reap()
            if self._alive():
                return True, "running"
            argv = self.argv()
            if not argv:
                return False, "sf-engine is not installed on this laptop"
            if not os.path.exists(self.key_file):
                return False, "no pairing key"
            os.makedirs(self.state_dir, exist_ok=True)
            try:
                os.unlink(self.status_path)
            except OSError:
                pass
            try:
                logf = open(self.log_path, "ab")
                self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                             stdout=logf, stderr=logf,
                                             close_fds=True)
                logf.close()
            except OSError as e:
                self.proc = None
                return False, "could not start sf-engine: %s" % e
            self.started_at = time.time()
            self.last_exit = None
            self.frames = 0
            self.log("[wfd] laptop engine started (pid %d)" % self.proc.pid)
            return True, "started"

    def stop(self, why="stop"):
        with self.lock:
            p, self.proc = self.proc, None
            self.started_at = None
            if p is None or p.poll() is not None:
                return False
            p.terminate()
            try:
                p.wait(STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(STOP_WAIT_S)
            self.last_exit = {"code": p.returncode, "at": time.time(), "tail": why}
            self.log("[wfd] laptop engine stopped (%s)" % why)
            return True

    def set_source(self, source):
        """-> (ok, message). Switching to the laptop starts the engine first: if it cannot
        start, the phone stays the source and the reason is returned."""
        if source not in SOURCES:
            return False, "source must be phone or laptop"
        with self.lock:
            if source == "laptop":
                ok, msg = self.start()
                if not ok:
                    return False, msg
                self.source = "laptop"
                return True, msg
            self.stop("switched to the phone camera")
            self.source = "phone"
            return True, "phone"

    # -- the /live/kp gate -----------------------------------------------

    def admit(self, frame_source):
        """True when a frame from `frame_source` may feed the endpointer now."""
        with self.lock:
            self._reap()
            src = "laptop" if frame_source == "laptop" else "phone"
            if src != self.source:
                return False
            if src == "laptop":
                self.last_frame_at = time.time()
                self.frames += 1
            return True

    # -- reporting -------------------------------------------------------

    def engine_stats(self):
        try:
            with open(self.status_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def attached(self):
        with self.lock:
            return self._alive() and self.source == "laptop" and \
                time.time() - self.last_frame_at <= ATTACH_S

    def status(self):
        with self.lock:
            self._reap()
            alive = self._alive()
            st = {"available": self.binary() is not None,
                  "source": self.source,
                  "running": alive,
                  "attached": self.attached(),
                  "pid": self.proc.pid if alive else None,
                  "frames": self.frames,
                  "last_frame_age_s": (round(time.time() - self.last_frame_at, 2)
                                       if self.last_frame_at else None),
                  "up_s": round(time.time() - self.started_at, 1) if self.started_at else None,
                  "last_exit": self.last_exit}
            s = self.engine_stats() if alive else None
            if s:
                st["vision"] = s.get("vision")
                st["camera"] = s.get("source")
                st["fps"] = s.get("fps")
                st["ms"] = s.get("ms")
                st["poses"] = s.get("poses")
                st["present"] = s.get("present")
                st["last_error"] = s.get("last_error") or None
            return st
