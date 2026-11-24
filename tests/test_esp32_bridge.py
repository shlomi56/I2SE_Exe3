"""Phase 6 — ESP32 bridge: protocol codec, Wi-Fi TCP transport, ESP32DispatcherProxy, the MicroPython
firmware (EdgeNode / EdgeAllocator / FleetStateMirror / WirelessServer) run on the host as a local TCP
mock ESP32, decision parity with MarginalCostDDDispatcher, and link-failure handling
(testing_strategy_markdown.md §2.4 and Layer 4).

Real-hardware tests are marked ``hil`` and run only when ``ESP32_HOST`` (and optionally ``ESP32_PORT``)
is set in the environment.
"""
from __future__ import annotations

import ast
import json
import os
import random
import socket
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from src.comms import (
    BLEGattTransport,
    ProtocolError,
    TransportDisconnected,
    TransportError,
    TransportTimeout,
    WiFiTCPTransport,
    decode_allocate_res,
    decode_message,
    encode_allocate_req,
    encode_allocate_res,
    encode_init_config,
)
from src.dispatchers import MarginalCostDDDispatcher, create_dispatcher_for_config
from src.dispatchers.esp32_proxy import ESP32DispatcherProxy
from src.models.dto import AllocationDecision, ElevatorAssignment, Request, SensorSnapshot
from src.models.enums import RequestStatus
from src.request_generator import RequestGenerator
from src.simulation import SimulationEngine, SimulationLogger
from tests.conftest import ROOT

ESP32_DIR = ROOT / "esp32"
if str(ESP32_DIR) not in sys.path:
    sys.path.insert(0, str(ESP32_DIR))

from edge_node import EdgeNode  # noqa: E402
from fleet_state_mirror import MAX_STOPS  # noqa: E402
from wireless_server import WirelessServer  # noqa: E402

# ============================================================================ servers


class EdgeServer:
    """The real firmware (EdgeNode + WirelessServer) served from a background thread on 127.0.0.1."""

    def __init__(self, node=None, wrap=None):
        self.node = node or EdgeNode()
        handler = self.node.handle_line if wrap is None else wrap(self.node.handle_line)
        self.server = WirelessServer(port=0, host="127.0.0.1", handler=handler)
        self.server.begin()
        self.port = self.server.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.stop()
        self.thread.join(timeout=2)


class MockServer:
    """Misbehaving peer: ``behavior(conn, line_bytes) -> bytes | None`` (None = stay silent)."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.lines = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            conn.settimeout(0.1)
            buf = b""
            while self.running:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    self.lines += 1
                    reply = self.behavior(conn, line)
                    if reply == b"CLOSE":
                        conn.close()
                        buf = b""
                        break
                    if reply is not None:
                        conn.sendall(reply + b"\n")
                else:
                    continue
                break
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self.running = False
        self.sock.close()
        self.thread.join(timeout=2)


@pytest.fixture
def edge():
    srv = EdgeServer()
    yield srv
    srv.stop()


def make_proxy(port, timeout_ms=500, **kw):
    return ESP32DispatcherProxy(WiFiTCPTransport(connect_timeout_ms=500), "127.0.0.1", str(port), timeout_ms, **kw)


def run_engine(config, requests, dispatcher):
    engine = SimulationEngine(config, dispatcher, requests)
    logger = SimulationLogger(output_dir=None)
    engine.register_observer(logger)
    summary = engine.run()
    return engine, logger, summary


class ParityProxy(ESP32DispatcherProxy):
    """Proxy that also computes the local MC-DDI decision from the same inputs and records both."""

    def initialize(self, building_config):
        super().initialize(building_config)
        self.reference = MarginalCostDDDispatcher()
        self.reference.initialize(building_config)
        self.pairs = []

    def allocate(self, request, fleet_snapshots, active_assignments):
        local = self.reference.compute_allocation(request, fleet_snapshots, active_assignments)
        remote = super().allocate(request, fleet_snapshots, active_assignments)
        self.pairs.append((local, remote))
        return remote


def same(a: AllocationDecision, b: AllocationDecision) -> bool:
    return (a.req_id, a.elevator_id, a.planned_route, a.estimated_cost) == (
        b.req_id, b.elevator_id, b.planned_route, b.estimated_cost)


# ============================================================================ protocol codec (§2.4)


def snap(eid, t=0.0, pos=0.0, speed=0.0, w=0.0, vib=0.0, obs=False, op=True, cap=False):
    return SensorSnapshot(eid, t, pos, speed, w, vib, obs, op, cap)


def test_init_config_roundtrip_into_mirror(building_config):
    node = EdgeNode()
    ack = json.loads(node.handle_line(encode_init_config(building_config)))
    assert ack == {"type": "INIT_ACK", "elevators": 3}
    m = node.mirror
    assert (m.min_floor, m.max_floor) == (-2, 15)  # negative basement floors survive
    assert m.avg_weight == 75.0 and m.buffer_ratio == 0.10 and m.max_vibration == 5.0
    for c, e in enumerate(building_config.elevators):
        assert m.eid[c] == e.elevator_id and m.speed[c] == e.max_speed and m.cap[c] == e.max_capacity
        assert m.door_cycle[c] == e.door_cycle_time  # identical float summation order


def test_allocate_req_roundtrip_edge_cases(building_config):
    node = EdgeNode()
    node.handle_line(encode_init_config(building_config))
    r_on = Request(7, 1.25, -2, 9, 140.0, status=RequestStatus.BOARDED)
    r_wait = Request(8, 3.5, 4, -1, 60.0, status=RequestStatus.ASSIGNED)
    snaps = [snap(0, 10.1, -1.4, -2.0, 140.0, obs=True), snap(1, 10.1, 7.0, vib=7.5, op=False), snap(2, 10.1, 8.0, cap=True)]
    assignments = {0: ElevatorAssignment([9, 4, -1], [r_on, r_wait]), 1: ElevatorAssignment(), 2: ElevatorAssignment()}
    new = Request(9, 10.1, -2, 15, 80.0)
    line = encode_allocate_req(new, snaps, assignments)
    assert "\n" not in line and "140.0" in line.split('"w":')[1][:6]  # sensor weight is sent ...
    assert "actual_weight" not in line and "80.0" not in line  # ... hidden passenger weights are not
    msg = json.loads(line)
    assert msg["type"] == "ALLOCATE_REQ" and msg["req"] == {"id": 9, "time": 10.1, "src": -2, "target": 15}
    from protocol_codec import ProtocolCodec

    ProtocolCodec.decode_call(msg, node.mirror)
    m = node.mirror
    assert (m.pos[0], m.spd[0], m.obs[0], m.qn[0]) == (-1.4, -2.0, 1, 3)
    assert [m.queue[0][k] for k in range(3)] == [9, 4, -1]
    assert [(m.r_id[0][k], m.r_ts[0][k], m.r_src[0][k], m.r_dst[0][k], m.r_on[0][k]) for k in range(2)] == [
        (7, 1.25, -2, 9, 1), (8, 3.5, 4, -1, 0)]
    assert (m.op[1], m.vib[1], m.qn[1], m.rn[1]) == (0, 7.5, 0, 0)  # empty queue + active fault flag
    assert m.atcap[2] == 1


def test_allocate_res_roundtrip_and_exact_floats():
    d = AllocationDecision(42, 2, [-2, 0, 13], 0.1 + 0.2, 1.4)
    back = decode_allocate_res(decode_message(encode_allocate_res(d)))
    assert back == d and back.estimated_cost == 0.30000000000000004
    from protocol_codec import ProtocolCodec

    edge_line = ProtocolCodec.encode_decision(42, 2, [-2, 0, 13], 0.1 + 0.2, 1.4)
    assert decode_allocate_res(decode_message(edge_line)) == d


@pytest.mark.parametrize("line", ["not json", "[1,2]", '{"no_type":1}', '{"type":"WHAT"}'])
def test_decode_rejects_bad_messages(line):
    with pytest.raises(ProtocolError):
        decode_message(line)


def test_decode_rejects_malformed_allocate_res():
    with pytest.raises(ProtocolError):
        decode_allocate_res({"type": "ALLOCATE_RES", "req_id": 1})


def test_edge_error_replies(building_config):
    node = EdgeNode()
    first = json.loads(node.handle_line(encode_allocate_req(Request(1, 0.0, 0, 5, 75.0), [snap(0)], {})))
    assert first["type"] == "ERROR" and first["code"] == "NOT_INITIALIZED"
    assert json.loads(node.handle_line(b"{garbage"))["code"] == "BAD_REQUEST"
    assert json.loads(node.handle_line('{"type":"PING"}')) == {"type": "PONG"}
    node.handle_line(encode_init_config(building_config))
    big = {0: ElevatorAssignment(list(range(1, 15)) * 3, []), 1: ElevatorAssignment(), 2: ElevatorAssignment()}
    err = json.loads(node.handle_line(encode_allocate_req(Request(2, 0.0, 0, 5, 75.0), [snap(0), snap(1), snap(2)], big)))
    assert err["type"] == "ERROR" and err["code"] == "CAPACITY_EXCEEDED" and err["req_id"] == 2
    assert len(big[0].stop_queue) > MAX_STOPS


# ============================================================================ transport


def test_connect_refused_returns_false():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert WiFiTCPTransport(connect_timeout_ms=300).connect("127.0.0.1", str(port)) is False


def test_send_and_receive_against_firmware(edge):
    tr = WiFiTCPTransport()
    assert tr.connect("127.0.0.1", str(edge.port)) and tr.is_connected
    assert json.loads(tr.send_and_receive('{"type":"PING"}', 500)) == {"type": "PONG"}
    with pytest.raises(TransportError):
        tr.send_and_receive("two\nlines", 500)
    tr.disconnect()
    assert not tr.is_connected
    with pytest.raises(TransportError, match="not connected"):
        tr.send_and_receive('{"type":"PING"}', 500)


def test_timeout_is_bounded_and_drops_the_stream():
    srv = MockServer(lambda conn, line: None)  # never answers
    try:
        tr = WiFiTCPTransport()
        assert tr.connect("127.0.0.1", str(srv.port))
        t0 = time.monotonic()
        with pytest.raises(TransportTimeout):
            tr.send_and_receive('{"type":"PING"}', 150)
        elapsed = time.monotonic() - t0
        assert 0.14 <= elapsed < 0.6
        assert not tr.is_connected  # closed so a late reply cannot be mistaken for the next answer
    finally:
        srv.stop()


def test_peer_disconnect_is_detected():
    srv = MockServer(lambda conn, line: b"CLOSE")
    try:
        tr = WiFiTCPTransport()
        assert tr.connect("127.0.0.1", str(srv.port))
        with pytest.raises(TransportDisconnected):
            tr.send_and_receive('{"type":"PING"}', 1000)
        assert not tr.is_connected
    finally:
        srv.stop()


def test_ble_transport_is_a_stub(make_config):
    ble = BLEGattTransport()
    assert not ble.is_connected
    with pytest.raises(NotImplementedError):
        ble.connect("aa:bb", "4fafc201-1fb5-459e-8fcc-c5c9c331914b")
    with pytest.raises(NotImplementedError):
        ble.send_and_receive("{}", 100)
    ble.disconnect()  # safe no-op

    def mut(raw):
        raw["esp32_bridge"].update(enabled=True, transport="BLE_GATT")
        raw["sensors"]["injected_faults"] = []

    cfg = make_config(mut)
    proxy = create_dispatcher_for_config(cfg)
    assert isinstance(proxy, ESP32DispatcherProxy) and isinstance(proxy.transport, BLEGattTransport)
    reqs = [Request(i, float(i), 0, 3 + i, 75.0) for i in range(5)]
    _, _, summary = run_engine(cfg, reqs, proxy)  # fails safe to local MC-DDI
    assert summary["completed_requests"] == 5 and proxy.stats["fallbacks"] == 5


def test_factory_builds_wifi_proxy_from_config(make_config):
    def mut(raw):
        raw["esp32_bridge"].update(enabled=True, host="10.0.0.7", port=6000, timeout_ms=250)

    proxy = create_dispatcher_for_config(make_config(mut))
    assert isinstance(proxy.transport, WiFiTCPTransport)
    assert (proxy.address, proxy.port_or_uuid, proxy.timeout_ms) == ("10.0.0.7", "6000", 250)
    assert proxy.execution_mode == "ESP32" and proxy.algorithm_name == "MC_DDI"


# ============================================================================ bit-for-bit parity (Layer 4)


def test_parity_50_request_replay_over_tcp(building_config, edge):
    """The HIL equivalence test against the host-side mock ESP32: 50/50 identical decisions."""
    requests = RequestGenerator().generate(building_config)[:50]
    proxy = ParityProxy(WiFiTCPTransport(), "127.0.0.1", str(edge.port), 500)
    engine, logger, summary = run_engine(building_config, requests, proxy)
    proxy.close()

    assert len(proxy.pairs) == 50
    mismatches = [(l, r) for l, r in proxy.pairs if not same(l, r)]
    assert mismatches == []
    assert sum(l.elevator_id == r.elevator_id for l, r in proxy.pairs) == 50  # 100% elevator_id parity
    assert proxy.stats["ok"] == 50 and proxy.stats["fallbacks"] == 0
    assert summary["execution_mode"] == "ESP32" and summary["completed_requests"] == 50

    _, local_logger, local_summary = run_engine(building_config, requests, MarginalCostDDDispatcher())
    assert logger.to_csv_text() == local_logger.to_csv_text()  # identical allocations and stop trace
    for key in ("avg_journey_time_sec", "total_elevator_stops", "max_hall_wait_time_sec"):
        assert summary[key] == local_summary[key]


@pytest.mark.parametrize("scenario,seed", [("full", 42), ("up_peak", 1), ("down_peak", 3), ("midday", 7)])
def test_parity_full_scenarios_via_edge_node(raw_config, scenario, seed):
    """Every decision across whole scenarios (faults, door obstructions, parking) is bit-identical."""
    from src.benchmark import build_scenario

    cfg = build_scenario(raw_config, scenario, seed)
    node = EdgeNode()
    node.handle_line(encode_init_config(cfg))
    mismatches, calls = [], [0]

    class Check(MarginalCostDDDispatcher):
        def allocate(self, r, snaps, assigns):
            d = super().allocate(r, snaps, assigns)
            e = decode_allocate_res(decode_message(node.handle_line(encode_allocate_req(r, snaps, assigns))))
            calls[0] += 1
            if not same(d, e):
                mismatches.append((r.req_id, d, e))
            return d

    SimulationEngine(cfg, Check(), RequestGenerator().generate(cfg)).run()
    assert calls[0] >= cfg.traffic.total_requests and mismatches == []


def test_parity_overload_and_all_cars_full_fallback(make_config):
    """Heavy bursts trigger runtime overloads, capacity locks and MC-DDI's fallback tail allocation."""
    def mut(raw):
        raw["sensors"]["injected_faults"] = []

    cfg = make_config(mut)
    node = EdgeNode()
    node.handle_line(encode_init_config(cfg))
    burst = [Request(i, 0.01 * i, 0, 3 + i % 12, 140.0) for i in range(40)]
    mismatches, realloc = [], [0]

    class Check(MarginalCostDDDispatcher):
        def allocate(self, r, snaps, assigns):
            d = super().allocate(r, snaps, assigns)
            e = decode_allocate_res(decode_message(node.handle_line(encode_allocate_req(r, snaps, assigns))))
            if not same(d, e):
                mismatches.append((r.req_id, d, e))
            return d

    engine = SimulationEngine(cfg, Check(), burst)
    summary = engine.run()
    assert summary["overload_events"] > 0 and summary["completed_requests"] == 40
    assert mismatches == []


def _random_state(rng, cfg):
    floors = cfg.floors
    snaps, assigns, rid = [], {}, 1000
    for e in cfg.elevators:
        moving = rng.random() < 0.5
        pos = rng.uniform(cfg.min_floor, cfg.max_floor) if moving else float(rng.choice(floors))
        speed = rng.choice([-1, 1]) * e.max_speed if moving else 0.0
        reqs = []
        for _ in range(rng.randint(0, 7)):
            s, d = rng.sample(floors, 2)
            st = RequestStatus.BOARDED if rng.random() < 0.5 else RequestStatus.ASSIGNED
            reqs.append(Request(rid, rng.uniform(0, 100), s, d, 75.0, status=st))
            rid += 1
        queue = []
        for r in reqs:
            if r.status != RequestStatus.BOARDED:
                queue.append(r.src_floor)
            queue.append(r.target_floor)
        rng.shuffle(queue)
        n_on = sum(r.status == RequestStatus.BOARDED for r in reqs)
        snaps.append(SensorSnapshot(
            e.elevator_id, 100.0, pos, speed, n_on * rng.uniform(40, 110), rng.choice([0.0, 0.0, 0.0, 7.5]),
            rng.random() < 0.15, rng.random() > 0.1, rng.random() < 0.15))
        assigns[e.elevator_id] = ElevatorAssignment(queue, sorted(reqs, key=lambda r: r.req_id))
    s, d = rng.sample(floors, 2)
    return Request(1, rng.uniform(0, 100), s, d, 75.0), snaps, assigns


def test_parity_randomized_fleet_states(building_config):
    """400 random fleet states: moving cars past floors, capacity locks, faults, obstructions, unsorted
    queues, re-dispatched (old) calls — local and edge decisions must be bit-identical."""
    rng = random.Random(2026)
    local = MarginalCostDDDispatcher()
    local.initialize(building_config)
    node = EdgeNode()
    node.handle_line(encode_init_config(building_config))
    for _ in range(400):
        req, snaps, assigns = _random_state(rng, building_config)
        d = local.compute_allocation(req, snaps, assigns)
        e = decode_allocate_res(decode_message(node.handle_line(encode_allocate_req(req, snaps, assigns))))
        assert same(d, e), (req, snaps, assigns, d, e)


# ============================================================================ latency, stability, memory


def test_latency_and_stability_200_requests(building_config, edge):
    requests = RequestGenerator().generate(building_config)
    proxy = make_proxy(edge.port, 500)
    engine, _, summary = run_engine(building_config, requests, proxy)
    proxy.close()
    lat = sorted(engine.dispatch_latencies_ms)
    p99 = lat[int(0.99 * (len(lat) - 1))]
    assert p99 < building_config.esp32_bridge.timeout_ms
    assert proxy.stats["fallbacks"] == 0 and proxy.stats["reconnects"] == 1  # one persistent link
    assert edge.node.handled == len(lat) and summary["completed_requests"] == 200


def test_edge_memory_is_constant(building_config):
    node = EdgeNode()
    node.handle_line(encode_init_config(building_config))
    req, snaps, assigns = _random_state(random.Random(5), building_config)
    line = encode_allocate_req(req, snaps, assigns)
    buffers = [id(b) for b in (node.allocator.w_q, node.allocator.pr_route, node.allocator.c_times, node.mirror.queue[0])]
    sizes = [len(node.allocator.w_q), len(node.allocator.pr_route), len(node.mirror.queue[0])]
    for _ in range(50):
        node.handle_line(line)
    tracemalloc.start()
    base = tracemalloc.take_snapshot()
    for _ in range(300):
        node.handle_line(line)
    growth = sum(s.size_diff for s in tracemalloc.take_snapshot().compare_to(base, "filename"))
    tracemalloc.stop()
    assert growth < 16_384  # no per-call accumulation (heap-leak check)
    assert buffers == [id(b) for b in (node.allocator.w_q, node.allocator.pr_route, node.allocator.c_times, node.mirror.queue[0])]
    assert sizes == [len(node.allocator.w_q), len(node.allocator.pr_route), len(node.mirror.queue[0])]


# ============================================================================ link-drop resilience


def test_unresponsive_edge_falls_back_without_hanging(building_config):
    srv = MockServer(lambda conn, line: None)
    try:
        proxy = make_proxy(srv.port, timeout_ms=40)
        reqs = RequestGenerator().generate(building_config)[:10]
        t0 = time.monotonic()
        _, logger, summary = run_engine(building_config, reqs, proxy)
        assert time.monotonic() - t0 < 10.0
        assert summary["completed_requests"] == 10 and proxy.stats["fallbacks"] == 10
        _, local_logger, _ = run_engine(building_config, reqs, MarginalCostDDDispatcher())
        assert logger.to_csv_text() == local_logger.to_csv_text()  # fallback = same algorithm
    finally:
        srv.stop()


def test_mid_run_disconnects_reconnect_and_resync(building_config):
    """The edge drops the TCP link after every 4th message; the proxy reconnects, re-sends INIT_CONFIG
    and keeps getting edge decisions (no fallback)."""
    counter = {"n": 0}

    def flaky(handler):
        def wrapped(line):
            counter["n"] += 1
            if counter["n"] % 4 == 0:
                raise ConnectionResetError("simulated Wi-Fi drop")
            return handler(line)
        return wrapped

    srv = EdgeServer(wrap=flaky)
    try:
        reqs = RequestGenerator().generate(building_config)[:30]
        proxy = make_proxy(srv.port, 300)
        _, logger, summary = run_engine(building_config, reqs, proxy)
        proxy.close()
        assert summary["completed_requests"] == 30
        assert proxy.stats["reconnects"] > 3 and proxy.stats["link_errors"] > 0
        assert proxy.stats["ok"] == 30 and proxy.stats["fallbacks"] == 0
        _, local_logger, _ = run_engine(building_config, reqs, MarginalCostDDDispatcher())
        assert logger.to_csv_text() == local_logger.to_csv_text()
    finally:
        srv.stop()


def test_edge_dies_mid_run(building_config):
    srv = EdgeServer()
    reqs = RequestGenerator().generate(building_config)[:40]
    proxy = make_proxy(srv.port, 100)

    calls = {"n": 0}
    orig = proxy.allocate

    def allocate(r, s, a):
        calls["n"] += 1
        if calls["n"] == 21:
            srv.stop()
        return orig(r, s, a)

    proxy.allocate = allocate
    _, logger, summary = run_engine(building_config, reqs, proxy)
    assert summary["completed_requests"] == 40
    assert proxy.stats["ok"] >= 20 and proxy.stats["fallbacks"] >= 1
    _, local_logger, _ = run_engine(building_config, reqs, MarginalCostDDDispatcher())
    assert logger.to_csv_text() == local_logger.to_csv_text()


def test_edge_error_reply_falls_back_once(building_config):
    def behavior(conn, line):
        msg = json.loads(line)
        if msg["type"] == "INIT_CONFIG":
            return b'{"type":"INIT_ACK","elevators":3}'
        return json.dumps({"type": "ERROR", "req_id": msg["req"]["id"], "code": "CAPACITY_EXCEEDED", "message": "x"}).encode()

    srv = MockServer(behavior)
    try:
        proxy = make_proxy(srv.port, 300)
        proxy.initialize(building_config)
        d = proxy.allocate(Request(1, 0.0, 0, 5, 75.0), [snap(0), snap(1), snap(2, pos=8.0)],
                           {0: ElevatorAssignment(), 1: ElevatorAssignment(), 2: ElevatorAssignment()})
        assert d.elevator_id == 0
        assert proxy.stats["edge_errors"] == 1 and proxy.stats["fallbacks"] == 1 and proxy.stats["sent"] == 1
        assert "CAPACITY_EXCEEDED" in proxy.last_error
    finally:
        srv.stop()


def test_mismatched_reply_and_late_reply_are_not_trusted(building_config):
    state = {"n": 0}

    def behavior(conn, line):
        msg = json.loads(line)
        if msg["type"] == "INIT_CONFIG":
            return b'{"type":"INIT_ACK","elevators":3}'
        state["n"] += 1
        if state["n"] == 1:  # wrong req_id
            return encode_allocate_res(AllocationDecision(999, 1, [0, 5], 1.0)).encode()
        if state["n"] == 2:  # too late: arrives after the proxy's timeout
            time.sleep(0.15)
            return encode_allocate_res(AllocationDecision(msg["req"]["id"], 1, [0, 5], 1.0)).encode()
        return encode_allocate_res(AllocationDecision(msg["req"]["id"], 2, [0, 5], 1.0)).encode()

    srv = MockServer(behavior)
    try:
        proxy = make_proxy(srv.port, 100, max_retries=2)
        proxy.initialize(building_config)
        d = proxy.allocate(Request(1, 0.0, 0, 5, 75.0), [snap(0), snap(1), snap(2)],
                           {0: ElevatorAssignment(), 1: ElevatorAssignment(), 2: ElevatorAssignment()})
        assert d.elevator_id == 2 and proxy.stats["ok"] == 1
        assert proxy.stats["link_errors"] >= 1 and proxy.stats["timeouts"] == 1
    finally:
        srv.stop()


def test_fallback_can_be_disabled(building_config):
    srv = MockServer(lambda conn, line: None)
    try:
        proxy = make_proxy(srv.port, 30, fallback_to_local=False)
        proxy.initialize(building_config)
        with pytest.raises(TransportError):
            proxy.allocate(Request(1, 0.0, 0, 5, 75.0), [snap(0)], {0: ElevatorAssignment()})
    finally:
        srv.stop()


def test_main_cli_with_esp32_flag(edge, tmp_path, capsys):
    from src.main import main

    req_path = tmp_path / "r.csv"
    gen = RequestGenerator()
    from src.config_loader import load_config

    gen.save_to_file(gen.generate(load_config(ROOT / "simulation_config.json"))[:25], req_path)
    rc = main(["--config", str(ROOT / "simulation_config.json"), "--requests", str(req_path),
               "--esp32", f"127.0.0.1:{edge.port}", "--output-dir", str(tmp_path / "out")])
    assert rc == 0
    summary = json.loads((tmp_path / "out" / "simulation_summary.json").read_text())
    assert summary["execution_mode"] == "ESP32" and summary["completed_requests"] == 25
    assert "'fallbacks': 0" in capsys.readouterr().out


# ============================================================================ firmware stays MicroPython-compatible

MICROPYTHON_IMPORTS = {"json", "ujson", "socket", "time", "math", "gc", "network", "machine", "config",
                       "edge_allocator", "fleet_state_mirror", "protocol_codec", "edge_node", "wireless_server"}


@pytest.mark.parametrize("path", sorted(ESP32_DIR.glob("*.py")), ids=lambda p: p.name)
def test_firmware_uses_only_micropython_features(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {a.name.split(".")[0] for a in node.names} <= MICROPYTHON_IMPORTS, ast.dump(node)
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in MICROPYTHON_IMPORTS, node.module
        assert not isinstance(node, (ast.JoinedStr, ast.AnnAssign, ast.NamedExpr, ast.Match)), (
            f"{path.name}:{getattr(node, 'lineno', '?')} uses {type(node).__name__}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.returns is None and not node.decorator_list or all(
                isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list), node.name


# ============================================================================ real hardware (opt-in)

ESP32_HOST = os.environ.get("ESP32_HOST")
ESP32_PORT = os.environ.get("ESP32_PORT", "5000")


@pytest.mark.hil
@pytest.mark.skipif(not ESP32_HOST, reason="set ESP32_HOST (and ESP32_PORT) to run against a flashed board")
def test_hil_parity_and_latency_on_real_esp32(building_config):
    requests = RequestGenerator().generate(building_config)
    proxy = ParityProxy(WiFiTCPTransport(), ESP32_HOST, ESP32_PORT, building_config.esp32_bridge.timeout_ms)
    engine, _, summary = run_engine(building_config, requests, proxy)
    proxy.close()
    first50 = proxy.pairs[:50]
    assert all(l.elevator_id == r.elevator_id for l, r in first50)  # spec: 100% elevator_id parity
    lat = sorted(engine.dispatch_latencies_ms)
    assert lat[int(0.99 * (len(lat) - 1))] < building_config.esp32_bridge.timeout_ms
    assert proxy.stats["fallbacks"] == 0 and summary["completed_requests"] == 200


@pytest.fixture(scope="module")
def raw_config():
    return json.loads((ROOT / "simulation_config.json").read_text(encoding="utf-8"))
