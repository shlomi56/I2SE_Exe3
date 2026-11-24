# WirelessServer - Wi-Fi station + single-client TCP line server (LLD section 5).
# Runs unchanged on MicroPython (ESP32) and CPython (host-side mock for tests).

import socket

MAX_LINE = 16384  # bytes; a full 3-car fleet_state is ~1-3 KB


class WirelessServer:
    def __init__(self, port=5000, host="0.0.0.0", handler=None, verbose=False):
        self.port = port
        self.host = host
        self.handler = handler  # callable(line_bytes) -> reply str
        self.verbose = verbose
        self.running = False
        self._listener = None
        self._client = None
        self.ip = host

    # ------------------------------------------------------------------ LLD methods

    def begin(self, ssid=None, password=None, timeout_s=20):
        """Join Wi-Fi (ESP32 only) and open the listening socket. Returns the IP address."""
        if ssid:
            import network  # MicroPython-only module
            import time

            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)
            if not wlan.isconnected():
                wlan.connect(ssid, password)
                waited = 0
                while not wlan.isconnected() and waited < timeout_s * 10:
                    time.sleep_ms(100)
                    waited += 1
            if not wlan.isconnected():
                raise OSError("Wi-Fi connection to %s failed" % ssid)
            self.ip = wlan.ifconfig()[0]
        addr = socket.getaddrinfo(self.host, self.port)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(addr)
        s.listen(1)
        s.settimeout(0.2)
        self._listener = s
        if self.port == 0:  # ephemeral port (host-side tests)
            self.port = s.getsockname()[1]
        self.running = True
        if self.verbose:
            print("EdgeAllocator listening on %s:%d" % (self.ip, self.port))
        return self.ip

    def poll_client(self):
        """Accept one client (if any) and serve it until it disconnects. Returns False once stopped."""
        if not self.running:
            return False
        try:
            conn, peer = self._listener.accept()
        except OSError:
            return self.running  # accept timeout: nothing to do
        self._client = conn
        if self.verbose:
            print("client connected:", peer)
        conn.settimeout(0.2)
        buf = b""
        try:
            while self.running:
                try:
                    chunk = conn.recv(1024)
                except OSError:
                    continue  # read timeout - keep waiting for the next request
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_LINE and b"\n" not in buf:
                    self.send_response('{"type":"ERROR","req_id":null,"code":"BAD_REQUEST","message":"line too long"}')
                    break
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    if line.strip():
                        self.send_response(self.handler(line))
        except Exception as exc:  # never let one bad client kill the server loop on the MCU
            if self.verbose:
                print("client error:", exc)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._client = None
            if self.verbose:
                print("client disconnected")
        return self.running

    def send_response(self, json_str):
        data = (json_str + "\n").encode("utf-8")
        conn = self._client
        sent = 0
        while sent < len(data):
            n = conn.send(data[sent:])
            if n is None:  # some MicroPython ports return None for a full write
                n = len(data) - sent
            sent += n

    # ------------------------------------------------------------------ helpers

    def serve_forever(self):
        while self.poll_client():
            pass

    def stop(self):
        self.running = False
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
