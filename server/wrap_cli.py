#!/usr/bin/env python3
"""wrap_cli — the `wrap` CLI as pure stdlib Python.

Same commands as bin/wrap (start/pair/stop/status/logs/send/install/
tunnel/open/doctor), runnable where bash is not — Windows first, but it
behaves identically on POSIX too. The npm shim (bin/wrap.js) execs bash
bin/wrap on POSIX and this file everywhere else.

DAEMON LIFETIME CONTRACT: `wrap start` must leave wfd.py running after
this process exits — POSIX uses start_new_session (setsid), Windows
DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP (no console, survives the
terminal closing). All HTTP is urllib; there is no curl dependency.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

SELF = os.path.realpath(__file__)
ROOT = os.path.dirname(os.path.dirname(SELF))
PORT = int(os.environ.get("WF_PORT", "8741"))
PIDFILE = os.path.join(ROOT, "wfd.pid")
LOG = os.path.join(ROOT, "wfd.log")
TUNLOG = os.path.join(ROOT, "tunnel.log")
TUNPID = os.path.join(ROOT, "tunnel.pid")
KEYFILE = os.path.expanduser(os.path.join("~", ".config", "wf", "key"))
CF = os.environ.get("CF") or shutil.which("cloudflared") \
    or os.path.expanduser(os.path.join("~", ".local", "bin", "cloudflared"))

IS_NT = os.name == "nt"


def bound_port():
    """Port the daemon actually bound (scraped from its log line — it
    falls forward when the requested port is busy)."""
    try:
        txt = open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = re.findall(r"http://0\.0\.0\.0:(\d+)", txt)
    return m[-1] if m else ""


def live_port():
    return int(bound_port() or PORT)


def _get(path, host="localhost", timeout=3):
    try:
        with urllib.request.urlopen(
                "http://%s:%d%s" % (host, live_port(), path),
                timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def daemon_running():
    return _get("/health", timeout=2) is not None


def key():
    try:
        return open(KEYFILE, encoding="utf-8").read().strip()
    except OSError:
        return ""


def qr(link):
    subprocess.call([sys.executable,
                     os.path.join(ROOT, "server", "qrprint.py"), link])


def pair_card():
    info = _get("/pair-info")
    if info is None:
        print("daemon not answering")
        return 1
    i = json.loads(info)
    link = "http://%s:%d/?k=%s" % (i["ip"], i["port"], i["key"])
    qr(link)
    print()
    print("  scan with your phone camera — composer opens paired")
    print("  or open on phone:  %s" % link)
    for a in i.get("ips", []):
        if a != i["ip"]:
            print("  other interface:   http://%s:%d/?k=%s"
                  % (a, i["port"], i["key"]))
    print("  app/manual:        address %s:%d   code %s"
          % (i["ip"], i["port"], i["code"]))
    return 0


def _spawn_daemon():
    """wfd.py detached, stdio -> wfd.log, surviving this CLI's death."""
    log = open(LOG, "ab", buffering=0)
    kw = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
          "close_fds": True}
    if IS_NT:
        kw["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen([sys.executable,
                             os.path.join(ROOT, "server", "wfd.py")], **kw)
    log.close()
    return proc


def _kill_pid_file(path, name):
    try:
        pid = int(open(path).read().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print("%s stopped" % name)
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _adb(*argv):
    adb = shutil.which("adb")
    if not adb:
        return None
    try:
        r = subprocess.run([adb] + list(argv), capture_output=True,
                           text=True, timeout=15)
        return r
    except (OSError, subprocess.TimeoutExpired):
        return None


def cmd_start():
    if daemon_running():
        print("daemon already running on :%d" % live_port())
    else:
        open(LOG, "w").close()      # truncate so bound_port() is fresh
        proc = _spawn_daemon()
        try:
            open(PIDFILE, "w").write(str(proc.pid))
        except OSError:
            pass
        for _ in range(40):
            if daemon_running():
                break
            time.sleep(0.25)
        if not daemon_running():
            print("daemon failed — see %s (wrap logs)" % LOG)
            return 1
        bp = live_port()
        if bp != PORT:
            print("port %d busy — using :%d" % (PORT, bp))
    r = _adb("get-state")
    if r is not None and r.returncode == 0:
        r2 = _adb("reverse", "tcp:8741", "tcp:%d" % live_port())
        if r2 is not None and r2.returncode == 0:
            print("usb bridge: phone localhost:8741 -> laptop :%d"
                  % live_port())
    rc = pair_card()
    # firewall hint only when the LAN URL can't be fetched from here
    info = _get("/pair-info")
    lip = json.loads(info).get("ip", "") if info else ""
    if lip and lip != "127.0.0.1" and _get("/health", host=lip,
                                           timeout=2) is None:
        print("firewall: http://%s:%d unreachable from here — likely a "
              "firewall." % (lip, live_port()))
        if IS_NT:
            print('          allow it: netsh advfirewall firewall add '
                  'rule name="sign-flow" dir=in action=allow '
                  'protocol=TCP localport=%d' % live_port())
        else:
            print("          allow it: sudo ufw allow %d/tcp   (or: sudo "
                  "firewall-cmd --add-port=%d/tcp)"
                  % (live_port(), live_port()))
    return rc


def cmd_stop():
    _kill_pid_file(PIDFILE, "daemon")
    _kill_pid_file(TUNPID, "tunnel")
    adb = shutil.which("adb")
    if adb:
        subprocess.run([adb, "reverse", "--remove", "tcp:8741"],
                       capture_output=True, timeout=10)
    return 0


def cmd_status():
    if daemon_running():
        bp = live_port()
        try:
            pid = open(PIDFILE).read().strip()
        except OSError:
            pid = "?"
        rss = ""
        try:
            for line in open("/proc/%s/status" % pid):
                if line.startswith("VmRSS"):
                    rss = ", rss %.1f MB" % (int(line.split()[1]) / 1024)
        except OSError:
            pass
        print("daemon: UP on :%d (pid %s%s)" % (bp, pid, rss or ", rss ?"))
        info = _get("/pair-info")
        lip = json.loads(info).get("ip", "") if info else ""
        if lip:
            print("lan:    http://%s:%d/" % (lip, bp))
    else:
        print("daemon: DOWN")
    adb = shutil.which("adb")
    devs = _adb("devices") if adb else None
    if devs is not None:
        lines = [l for l in devs.stdout.splitlines()[1:] if l.strip()]
        if lines:
            print(devs.stdout.strip())
        else:
            print("adb: no device")
        rev = _adb("reverse", "--list")
        if rev is not None and rev.stdout.strip():
            print(rev.stdout.strip())
        else:
            print("bridge: none")
    else:
        print("adb: not installed")
    try:
        m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com",
                       open(TUNLOG, encoding="utf-8",
                            errors="replace").read())
        if m:
            print("tunnel: %s" % m[-1])
    except OSError:
        pass
    return 0


def cmd_logs(follow):
    if not os.path.isfile(LOG):
        print("no log yet — run: wrap start")
        return 1
    if not follow:
        lines = open(LOG, encoding="utf-8", errors="replace") \
            .read().splitlines()
        for l in lines[-50:]:
            print(l)
        return 0
    pos = os.path.getsize(LOG)
    try:
        while True:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                data = f.read()
                pos = f.tell()
            if data:
                sys.stdout.write(data)
                sys.stdout.flush()
            else:
                time.sleep(0.3)
    except KeyboardInterrupt:
        return 0


def cmd_send(text):
    body = json.dumps({"text": text, "mode": "type"}).encode()
    req = urllib.request.Request(
        "http://localhost:%d/send" % live_port(), data=body,
        headers={"Content-Type": "application/json",
                 "X-WF-Key": key()}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        print(" -> typed at cursor")
        return 0
    except Exception as e:
        print("send failed: %s" % e)
        return 1


def cmd_install():
    build = os.path.join(ROOT, "app", "build.sh")
    if not os.path.isfile(build):
        print("app/build.sh not found")
        return 1
    shell = shutil.which("bash") or shutil.which("sh")
    if not shell:
        print("need bash to run app/build.sh")
        return 1
    if subprocess.call([shell, build], cwd=os.path.dirname(build)) != 0:
        return 1
    apk = os.path.join(ROOT, "app", "wf-composer.apk")
    r = _adb("install", "-r", apk)
    if r is None or r.returncode != 0:
        print("adb install failed — is a phone plugged in / adb present?")
        return 1
    _adb("shell", "am", "start", "-n", "com.wf.composer/.MainActivity")
    return 0


def cmd_tunnel():
    if not shutil.which(CF) and not os.path.isfile(CF):
        print("cloudflared not found (set $CF or install it)")
        return 1
    _kill_pid_file(TUNPID, "tunnel")
    time.sleep(0.5)
    log = open(TUNLOG, "wb")
    kw = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
          "close_fds": True}
    if IS_NT:
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [CF, "tunnel", "--url", "http://localhost:%d" % PORT,
             "--no-autoupdate"], **kw)
    except OSError as e:
        log.close()
        print("cloudflared spawn failed: %s" % e)
        return 1
    log.close()
    open(TUNPID, "w").write(str(proc.pid))
    turl = ""
    sys.stdout.write("waiting for tunnel")
    sys.stdout.flush()
    for _ in range(30):
        try:
            m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com",
                           open(TUNLOG, encoding="utf-8",
                                errors="replace").read())
            turl = m[-1] if m else ""
        except OSError:
            turl = ""
        if turl:
            break
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(1)
    print()
    if not turl:
        print("tunnel failed — see %s" % TUNLOG)
        return 1
    print("tunnel: %s" % turl)
    qr("%s/?k=%s" % (turl, key()))
    print("  scan with your phone camera — works from anywhere")
    return 0


def cmd_open():
    r = _adb("shell", "am", "start", "-n", "com.wf.composer/.MainActivity")
    if r is None or r.returncode != 0:
        print("adb not available or no device")
        return 1
    return 0


def cmd_doctor():
    def have(c):
        return shutil.which(c) is not None

    def pyxlib():
        try:
            import Xlib  # noqa
            return True
        except Exception:
            return False

    bad = 0
    print("wrap doctor — dependency check")
    if IS_NT:
        req = [("python", True, "the daemon itself"),
               ("powershell", have("powershell") or have("pwsh"),
                "injection + clipboard + window calls")]
        opt = [("adb", have("adb"), "usb bridge + app install"),
               ("cloudflared", have("cloudflared"),
                "wrap tunnel — anywhere access"),
               ("scrcpy", have("scrcpy"), "phone screen mirroring")]
    elif sys.platform == "darwin":
        req = [("python3", have("python3"), "the daemon itself"),
               ("osascript", have("osascript"),
                "types/pastes/focus via System Events")]
        opt = [("adb", have("adb"), "usb bridge + app install"),
               ("cloudflared", have("cloudflared"),
                "wrap tunnel — anywhere access"),
               ("scrcpy", have("scrcpy"), "phone screen mirroring")]
    else:
        req = [("python3", have("python3"), "the daemon itself"),
               ("curl", have("curl"), "CLI talks to the daemon over HTTP"),
               ("xdotool", have("xdotool"), "types/pastes at the cursor"),
               ("xprop", have("xprop"),
                "active-window detection (x11-utils)"),
               ("xclip", have("xclip"), "clipboard save/restore"),
               ("convert", have("convert"),
                "image -> png normalization (imagemagick)"),
               ("python3-xlib", pyxlib(),
                "multi-target clipboard (apt: python3-xlib)")]
        opt = [("adb", have("adb"), "usb bridge + app install"),
               ("cloudflared", have("cloudflared"),
                "wrap tunnel — anywhere access"),
               ("scrcpy", have("scrcpy"), "phone screen mirroring"),
               ("notify-send", have("notify-send"),
                "confirm prompts for riskier signed actions")]
    try:
        ufw = open("/etc/ufw/ufw.conf").read()
        if "ENABLED=yes" in ufw:
            print("  [ note  ] ufw is ON — LAN pairing needs: "
                  "sudo ufw allow 8741/tcp")
    except OSError:
        pass
    for name, ok, why in req:
        print("  [%s] %-13s %s" % ("ok" if ok else "MISSING", name, why))
        bad += 0 if ok else 1
    for name, ok, why in opt:
        print("  [%s] %-13s %s (optional)"
              % ("ok" if ok else " - ", name, why))

    def env(tag, name, why):
        print("  [%s] %-13s %s" % (tag, name, why))

    if IS_NT:
        env("ok", "session", "windows (win32 + SendKeys injection)")
    elif sys.platform == "darwin":
        env("ok", "session", "macos (osascript injection — grant "
            "Accessibility to the terminal)")
    else:
        sess = os.environ.get("XDG_SESSION_TYPE", "").lower()
        disp = os.environ.get("DISPLAY", "")
        if sess == "wayland":
            env("warn", "session",
                "wayland — xdotool/xclip can't inject; log into an X11 "
                "session")
            bad += 1
        elif not sess and not disp:
            env("warn", "session",
                "headless (no XDG_SESSION_TYPE/DISPLAY) — injection needs "
                "an X11 desktop")
            bad += 1
        else:
            env("ok", "session", "%s (DISPLAY=%s)"
                % (sess or "x11", disp or "unset"))
    v = sys.version_info
    env("ok" if v >= (3, 7) else "MISSING",
        "python %d.%d" % (v.major, v.minor), "daemon needs 3.7+")
    bad += 0 if v >= (3, 7) else 1
    try:
        m = os.stat(KEYFILE).st_mode & 0o777
        if not IS_NT and m & 0o077:
            env("warn", "key perms",
                "%s is %s — fix: chmod 600 %s"
                % (KEYFILE, oct(m)[2:], KEYFILE))
            bad += 1
        else:
            env("ok", "key perms", "%s mode %s" % (KEYFILE, oct(m)[2:]))
    except OSError:
        env("note", "key perms",
            "no key yet — created mode 600 on first `wrap start`")
    return 1 if bad else 0


USAGE = """wrap — Flow Composer bridge (phone -> laptop cursor)
  wrap start     start daemon + show pair QR (scan = connected)
  wrap pair      show pair QR / one-shot code again
  wrap bridge    adb reverse (after replug)
  wrap tunnel    cloudflare tunnel + pair QR (works from anywhere)
  wrap doctor    verify dependencies (run this first)
  wrap status    daemon / port / rss / lan link / adb state
  wrap logs      daemon log (wrap logs -f to follow)
  wrap send TEXT type TEXT at laptop cursor
  wrap install   build + install Android app (optional)
  wrap open      launch app on phone
  wrap stop      stop daemon"""


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "start":
        return cmd_start()
    if cmd == "pair":
        return pair_card() or 0
    if cmd == "restart":
        cmd_stop()
        time.sleep(0.3)
        return cmd_start()
    if cmd == "bridge":
        _adb("wait-for-device")
        r = _adb("reverse", "tcp:8741", "tcp:%d" % PORT)
        if r is not None and r.returncode == 0:
            print("usb bridge up: phone localhost:8741 -> laptop :%d" % PORT)
            return 0
        print("adb reverse failed")
        return 1
    if cmd == "stop":
        return cmd_stop()
    if cmd == "status":
        return cmd_status()
    if cmd == "logs":
        return cmd_logs(len(argv) > 2 and argv[2] == "-f")
    if cmd in ("send", "test"):
        text = " ".join(argv[2:]) or ("hello from wrap %s"
                                      % time.strftime("%H:%M:%S"))
        return cmd_send(text)
    if cmd == "install":
        return cmd_install()
    if cmd == "tunnel":
        return cmd_tunnel()
    if cmd == "open":
        return cmd_open()
    if cmd in ("doctor", "check"):
        return cmd_doctor()
    print(USAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
