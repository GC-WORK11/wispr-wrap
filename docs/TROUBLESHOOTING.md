# Sign-Flow Troubleshooting Guide

This guide covers common network, reachability, browser, and display issues when deploying Sign-Flow.

---

## 1. Phone Reachability and Network Setup

### 1.1 Verifying Common Local Subnet
The phone and laptop must reside on the same Layer 2 / Layer 3 local network subnet to communicate directly via LAN:
1. On your laptop, verify your local IPv4 address:
   ```bash
   ip -4 addr show | grep -E 'inet.*(wl|en|eth)'
   ```
2. On your Android phone, check `Settings -> Network & Internet -> Wi-Fi -> Network details` and verify that the phone IP shares the same subnet prefix (e.g. `192.168.1.x`).
3. If the phone cannot reach the host IP printed by `wrap start`:
   - Check if your router enforces **AP Isolation** or **Guest Network Isolation**. Guest networks typically prevent wireless clients from contacting other devices on the same Wi-Fi. Connect both devices to the primary network.

---

## 2. Firewall Configuration

Sign-Flow defaults to listening on TCP port `8741`. If your Linux host has an active firewall (`ufw` on Debian/Ubuntu or `firewalld` on Fedora/RHEL), inbound connections will be dropped.

### 2.1 Diagnosing Firewall Blocks
Run `wrap doctor` to inspect local firewall state:
```bash
./bin/wrap doctor
```
If port 8741 is blocked, you will see a note indicating that `ufw` or `firewalld` is active.

### 2.2 Opening Port 8741

#### Ubuntu / Debian (UFW)
```bash
sudo ufw allow 8741/tcp
```
To verify:
```bash
sudo ufw status
```

#### Fedora / RHEL / CentOS (firewalld)
```bash
sudo firewall-cmd --add-port=8741/tcp --permanent
sudo firewall-cmd --reload
```

---

## 3. Secure Contexts (HTTP vs. HTTPS) and Camera Access

Modern mobile browsers (Google Chrome, Samsung Internet, Safari) implement strict W3C security policies regarding hardware access:
- **Text, Image, and File Transfer**: Works reliably over plain `http://<lan-ip>:8741` on your local network.
- **Camera Access (`getUserMedia`) and Service Workers**: Blocked by browsers over plain HTTP unless the origin is considered a **Secure Context** (HTTPS or `localhost`).

If you open `http://192.168.x.x:8741` on an Android phone, Go Live will fail to activate the camera because the browser denies `navigator.mediaDevices.getUserMedia`.

### 3.1 Workaround 1: USB Connection via ADB Reverse (Recommended)
This approach routes phone traffic through a USB cable and exposes the daemon at `http://localhost:8741` on the phone. Because `localhost` is inherently recognized as a secure context by mobile browsers, the camera and offline service worker function without SSL certificates:
1. Connect the Android phone to your laptop via USB.
2. Enable USB Debugging in Android Developer Options.
3. Run the bridge command:
   ```bash
   ./bin/wrap bridge
   # Or manually:
   adb reverse tcp:8741 tcp:8741
   ```
4. On the phone, navigate to:
   ```
   http://localhost:8741/?k=<your-pairing-key>
   ```

### 3.2 Workaround 2: Cloudflare Secure Tunnel
If you need wireless camera tracking without USB cabling:
1. Start the automated tunnel:
   ```bash
   ./bin/wrap tunnel
   ```
2. The CLI will establish an encrypted tunnel and output a public HTTPS URL:
   ```
   tunnel: https://<random-subdomain>.trycloudflare.com
   ```
3. Scan the generated QR code. The browser receives a valid TLS connection, enabling camera access while keypoint data streams back to your local machine.

### 3.3 Workaround 3: Android Chrome Dev Flag (Development Only)
If you wish to test directly over LAN HTTP without tunnels or cables:
1. Open Chrome on the Android phone.
2. Navigate to:
   ```
   chrome://flags/#unsafely-treat-insecure-origin-as-secure
   ```
3. Enable the flag and enter the exact host address, including port:
   ```
   http://192.168.1.50:8741
   ```
4. Tap **Relaunch**. Chrome will now permit `getUserMedia` and Service Workers on that LAN IP.

---

## 4. X11 vs. Wayland Session Compatibility

Sign-Flow injects synthetic keystrokes and pointer events into target applications via `xdotool` and coordinates clipboard buffers via `xclip` and `Xlib`.

### 4.1 Wayland Limitation
Modern Wayland compositors (default GNOME on Ubuntu/Fedora) enforce security isolation that deliberately prevents background processes from synthesizing keystrokes into other client windows. If you start Sign-Flow under Wayland, keystrokes will fail to register.

### 4.2 Resolution
Verify your active desktop session:
```bash
echo $XDG_SESSION_TYPE
```
- If the output is `x11`: Keystroke and pointer injection will function normally.
- If the output is `wayland`: Log out of your session. At the display manager login screen (GDM/SDDM), click the gear icon in the bottom-right corner and select **GNOME on Xorg**, **X11**, or your desktop's X11 session.

---

## 5. Pairing and State Troubleshooting

### 5.1 Invalid or Stale Key (HTTP 403 Forbidden)
The daemon generates a cryptographic key at `~/.config/wf/key` on initial boot.
- If the phone browser shows an authentication error or repeatedly prompts for pairing, re-scan the QR code using:
  ```bash
  ./bin/wrap pair
  ```
- Alternatively, check the 8-character numeric/hex code printed in `wrap pair` and enter it manually into the connection screen.

### 5.2 Checking Daemon Logs
If requests fail or the daemon terminates unexpectedly, inspect the log file:
```bash
tail -f wfd.log
```
To check running daemon processes:
```bash
./bin/wrap status
```
To force restart the daemon:
```bash
./bin/wrap restart
```
