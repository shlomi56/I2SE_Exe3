"""``ITransport`` communication layer (LLD §3.4).

``WiFiTCPTransport`` speaks newline-delimited UTF-8 JSON over a persistent TCP socket (stdlib ``socket``).
One request line out, one response line back. On a timeout the connection is closed on purpose: a late
reply would otherwise be read as the answer to the *next* request.
"""
from __future__ import annotations

import socket
import time
from abc import ABC, abstractmethod
from typing import Optional

MAX_LINE_BYTES = 1 << 20  # 1 MiB safety cap on a single response line


class TransportError(OSError):
    """Base class for link failures (not connected, refused, malformed framing)."""


class TransportTimeout(TransportError):
    """No complete response line arrived within ``timeout_ms``."""


class TransportDisconnected(TransportError):
    """The peer closed the connection."""


class ITransport(ABC):
    @abstractmethod
    def connect(self, address: str, port_or_uuid: str) -> bool:
        """Open the link; ``True`` on success, ``False`` if the peer is unreachable."""

    @abstractmethod
    def send_and_receive(self, payload: str, timeout_ms: int) -> str:
        """Send one message and block (at most ``timeout_ms``) for exactly one reply."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the link (idempotent)."""

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...


class WiFiTCPTransport(ITransport):
    def __init__(self, connect_timeout_ms: int = 2000, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self.connect_timeout_ms = connect_timeout_ms
        self.max_line_bytes = max_line_bytes
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    def connect(self, address: str, port_or_uuid: str) -> bool:
        self.disconnect()
        try:
            sock = socket.create_connection((address, int(port_or_uuid)), timeout=self.connect_timeout_ms / 1000.0)
        except (OSError, ValueError):
            return False
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._buffer = b""
        return True

    def send_and_receive(self, payload: str, timeout_ms: int) -> str:
        if self._sock is None:
            raise TransportError("not connected")
        if "\n" in payload:
            raise TransportError("payload must be a single line")
        deadline = time.monotonic() + timeout_ms / 1000.0
        try:
            self._sock.settimeout(max(timeout_ms, 1) / 1000.0)
            self._sock.sendall(payload.encode("utf-8") + b"\n")
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout()
                self._sock.settimeout(remaining)
                chunk = self._sock.recv(4096)
                if not chunk:
                    self.disconnect()
                    raise TransportDisconnected("peer closed the connection")
                self._buffer += chunk
                if len(self._buffer) > self.max_line_bytes:
                    self.disconnect()
                    raise TransportError("response line exceeds size limit")
        except socket.timeout as exc:
            self.disconnect()  # drop the stream so a late reply cannot desynchronize the next request
            raise TransportTimeout(f"no reply within {timeout_ms} ms") from exc
        except TransportError:
            raise
        except OSError as exc:
            self.disconnect()
            raise TransportDisconnected(str(exc)) from exc
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8")

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buffer = b""


class BLEGattTransport(ITransport):
    """Placeholder for the BLE GATT link (HLD §6: Wi-Fi is the chosen transport; BLE stays pluggable)."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401 - accept any future options
        pass

    @property
    def is_connected(self) -> bool:
        return False

    def connect(self, address: str, port_or_uuid: str) -> bool:
        raise NotImplementedError("BLE_GATT transport is not implemented; use WIFI_TCP")

    def send_and_receive(self, payload: str, timeout_ms: int) -> str:
        raise NotImplementedError("BLE_GATT transport is not implemented; use WIFI_TCP")

    def disconnect(self) -> None:
        """Nothing to close; kept as a no-op so callers can always clean up safely."""
