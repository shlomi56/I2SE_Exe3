# EdgeNode - glues ProtocolCodec + FleetStateMirror + EdgeAllocator: one request line in, one reply out.
# Independent of the network stack, so it is unit-testable under CPython.

import time

from edge_allocator import EdgeAllocator
from fleet_state_mirror import CapacityExceeded, FleetStateMirror
from protocol_codec import ProtocolCodec

try:  # MicroPython
    _ticks_us = time.ticks_us
    _ticks_diff = time.ticks_diff
except AttributeError:  # CPython
    def _ticks_us():
        return int(time.perf_counter() * 1000000)

    def _ticks_diff(a, b):
        return a - b


class EdgeNode:
    def __init__(self, mirror=None, verbose=False):
        self.mirror = mirror if mirror is not None else FleetStateMirror()
        self.allocator = EdgeAllocator(self.mirror)
        self.verbose = verbose
        self.handled = 0
        self.last_compute_ms = 0.0

    def handle_line(self, line):
        """Process one request line and return the reply line (without the trailing newline)."""
        req_id = None
        try:
            msg = ProtocolCodec.decode(line)
            kind = msg["type"]
            if kind == "PING":
                return ProtocolCodec.encode_pong()
            if kind == "INIT_CONFIG":
                ProtocolCodec.decode_config(msg, self.mirror)
                if self.verbose:
                    print("INIT_CONFIG: %d elevators, floors %d..%d" % (self.mirror.n, self.mirror.min_floor, self.mirror.max_floor))
                return ProtocolCodec.encode_ack(self.mirror.n)
            if kind == "ALLOCATE_REQ":
                req_id = msg.get("req", {}).get("id")
                if not self.mirror.initialized:
                    return ProtocolCodec.encode_error(req_id, "NOT_INITIALIZED", "send INIT_CONFIG first")
                t_start = _ticks_us()
                rid, ts, src, dst = ProtocolCodec.decode_call(msg, self.mirror)
                eid, route, cost = self.allocator.allocate(rid, ts, src, dst)
                compute_ms = _ticks_diff(_ticks_us(), t_start) / 1000.0
                self.mirror.commit_assignment({"elevator_id": eid, "route": route}, (rid, ts, src, dst))
                self.handled += 1
                self.last_compute_ms = compute_ms
                if self.verbose:  # FR-HW-03: serial log of every decision and its computation time
                    print("ALLOC req=%d %d->%d => E%d cost=%.2f in %.3f ms" % (rid, src, dst, eid, cost, compute_ms))
                return ProtocolCodec.encode_decision(rid, eid, route, cost, compute_ms)
            return ProtocolCodec.encode_error(req_id, "UNSUPPORTED", "unsupported message type %s" % kind)
        except CapacityExceeded as exc:
            return ProtocolCodec.encode_error(req_id, "CAPACITY_EXCEEDED", str(exc))
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return ProtocolCodec.encode_error(req_id, "BAD_REQUEST", "%s: %s" % (type(exc).__name__, exc))
