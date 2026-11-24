# EdgeAllocator - Marginal-Cost Destination Dispatch Insertion on the ESP32 (LLD section 5).
#
# A statement-by-statement port of src/dispatchers/mc_ddi.py + route_evaluator.py +
# src/models/route_semantics.py. Floating-point operations are performed in the same order as on the
# PC, so under CPython (64-bit floats) the decision, route and cost are bit-identical. All scratch
# buffers are preallocated in __init__: allocate() creates no lists, so memory use is constant.
#
# MicroPython-compatible: no dataclasses, typing, enum or f-strings.

import math

BOARDING_NOW = -1
TIE_EPS = 1e-3
_POS_EPS = 1e-6
_INF = float("inf")


def braking_floor(position, signed_speed):
    if signed_speed > 0:
        return int(math.floor(position + _POS_EPS)) + 1
    if signed_speed < 0:
        return int(math.ceil(position - _POS_EPS)) - 1
    return int(round(position))


class EdgeAllocator:
    def __init__(self, mirror):
        self.m = mirror
        ms, mr = mirror.max_stops, mirror.max_reqs
        self.NEW = mr  # slot index used for the incoming request
        work = ms + 2 * (mr + 1) + 4
        self.work_size = work
        # plan_route scratch
        self.w_q = [0] * work
        self.w_used = [False] * work
        self.w_remap = [0] * work
        self.order = [0] * (mr + 1)
        # base plan (car's current queue)
        self.base_route = [0] * work
        self.base_board = [0] * (mr + 1)
        self.base_alight = [0] * (mr + 1)
        self.base_times = [0.0] * work
        # candidate construction + plan
        self.q_src = [0] * work
        self.cand = [0] * (work + 2)
        self.pr_route = [0] * work
        self.c_board = [0] * (mr + 1)
        self.c_alight = [0] * (mr + 1)
        self.c_times = [0.0] * work
        self.boards = [0] * work
        self.alights = [0] * work
        # best decision
        self.best_route = [0] * work
        self.best_len = 0
        # per-call state
        self.n_order = 0
        self.n_onboard = 0
        self.new_src = 0
        self.new_dst = 0
        self.new_ts = 0.0

    # ================================================================== request accessors

    def _src(self, c, k):
        return self.new_src if k == self.NEW else self.m.r_src[c][k]

    def _dst(self, c, k):
        return self.new_dst if k == self.NEW else self.m.r_dst[c][k]

    def _ts(self, c, k):
        return self.new_ts if k == self.NEW else self.m.r_ts[c][k]

    def _build_order(self, c, include_new):
        """onboard requests (host order), then waiting requests (host order), then the new one."""
        m = self.m
        n = 0
        for k in range(m.rn[c]):
            if m.r_on[c][k]:
                self.order[n] = k
                n += 1
        self.n_onboard = n
        for k in range(m.rn[c]):
            if not m.r_on[c][k]:
                self.order[n] = k
                n += 1
        if include_new:
            self.order[n] = self.NEW
            n += 1
        self.n_order = n

    # ================================================================== shared route semantics

    def _plan(self, c, route, route_len, out_route, out_board, out_alight):
        """plan_route(prune=True, append_missing_src=False) into preallocated buffers; returns length."""
        q = self.w_q
        used = self.w_used
        wl = route_len
        for i in range(wl):
            q[i] = route[i]
            used[i] = False
        for oi in range(self.n_order):
            k = self.order[oi]
            if oi < self.n_onboard:
                a = -1
                dst = self._dst(c, k)
                for i in range(wl):
                    if q[i] == dst:
                        a = i
                        break
                if a < 0:
                    q[wl] = dst
                    used[wl] = False
                    a = wl
                    wl += 1
                out_alight[k] = a
                used[a] = True
            else:
                src = self._src(c, k)
                b = BOARDING_NOW
                for i in range(wl):
                    if q[i] == src:
                        b = i
                        break
                if b != BOARDING_NOW:
                    used[b] = True
                dst = self._dst(c, k)
                a = -1
                for i in range(b + 1, wl):
                    if q[i] == dst:
                        a = i
                        break
                if a < 0:
                    q[wl] = dst
                    used[wl] = False
                    a = wl
                    wl += 1
                out_board[k] = b
                out_alight[k] = a
                used[a] = True
        remap = self.w_remap
        n = 0
        for i in range(wl):
            if used[i]:
                remap[i] = n
                out_route[n] = q[i]
                n += 1
        for oi in range(self.n_order):
            k = self.order[oi]
            out_alight[k] = remap[out_alight[k]]
            if oi >= self.n_onboard and out_board[k] != BOARDING_NOW:
                out_board[k] = remap[out_board[k]]
        return n

    # ================================================================== kinematics

    def _leg(self, c, start_floor, end_floor):
        distance = abs(end_floor - start_floor)
        if distance < 1e-9:
            return 0.0
        m = self.m
        return m.start[c] + distance / m.speed[c] + m.stop[c]

    def _simulate(self, c, route, n, out_times, t0):
        m = self.m
        t = t0
        if m.obs[c]:
            t += m.obstruction_delay
        pos = m.pos[c]
        moving = m.spd[c]
        bf = braking_floor(m.pos[c], m.spd[c])
        v = m.speed[c]
        for i in range(n):
            stop = route[i]
            if moving != 0.0:
                ahead = (moving > 0 and stop >= bf) or (moving < 0 and stop <= bf)
                if ahead:
                    t += abs(stop - pos) / v + m.stop[c]
                else:
                    t += abs(bf - pos) / v + m.stop[c]
                    t += self._leg(c, bf, stop)
                moving = 0.0
            else:
                t += self._leg(c, pos, stop)
            out_times[i] = t
            t += m.door_cycle[c]
            pos = float(stop)

    def _completion_time(self, c, base_len, t0):
        m = self.m
        if base_len == 0:
            if m.spd[c] != 0.0:
                bf = braking_floor(m.pos[c], m.spd[c])
                return t0 + abs(bf - m.pos[c]) / m.speed[c] + m.stop[c]
            return t0
        return self.base_times[base_len - 1] + m.door_cycle[c]

    # ================================================================== capacity check

    def _capacity_ok(self, c, n):
        m = self.m
        avg = m.avg_weight
        w_limit = m.max_w[c] * (1.0 - m.buffer_ratio)
        boards, alights = self.boards, self.alights
        for i in range(n):
            boards[i] = 0
            alights[i] = 0
        now_boarders = 0
        for oi in range(self.n_onboard, self.n_order):
            b = self.c_board[self.order[oi]]
            if b == BOARDING_NOW:
                now_boarders += 1
            else:
                boards[b] += 1
        for oi in range(self.n_order):
            alights[self.c_alight[self.order[oi]]] += 1
        new_b = self.c_board[self.NEW]
        if new_b == BOARDING_NOW:
            return False
        if m.atcap[c]:
            first_drop = n
            for i in range(n):
                if alights[i] > 0:
                    first_drop = i
                    break
            if new_b < first_drop:
                return False
        new_a = self.c_alight[self.NEW]
        load = self.n_onboard + now_boarders
        weight = m.w[c] + avg * now_boarders
        for k in range(new_a):
            load -= alights[k]
            weight = max(0.0, weight - avg * alights[k])
            load += boards[k]
            weight += avg * boards[k]
            if k >= new_b and (load > m.cap[c] or weight > w_limit + 1e-9):
                return False
        return True

    # ================================================================== marginal cost

    def _penalty(self, wait):
        m = self.m
        excess = wait - m.soft_max_wait
        return m.fairness * excess * excess if excess > 0 else 0.0

    def _cost(self, c, t0):
        m = self.m
        new_arrival = self.c_times[self.c_alight[self.NEW]]
        b = self.c_board[self.NEW]
        new_board = t0 if b == BOARDING_NOW else self.c_times[b]
        cost = new_arrival - self.new_ts
        for oi in range(self.n_onboard):
            k = self.order[oi]
            cost += self.c_times[self.c_alight[k]] - self.base_times[self.base_alight[k]]
        for oi in range(self.n_onboard, self.n_order):
            k = self.order[oi]
            if k == self.NEW:
                continue
            cost += self.c_times[self.c_alight[k]] - self.base_times[self.base_alight[k]]
            if m.fairness > 0.0:
                bn = self.c_board[k]
                bo = self.base_board[k]
                new_wait = (t0 if bn == BOARDING_NOW else self.c_times[bn]) - m.r_ts[c][k]
                old_wait = (t0 if bo == BOARDING_NOW else self.base_times[bo]) - m.r_ts[c][k]
                cost += self._penalty(new_wait) - self._penalty(old_wait)
        if m.fairness > 0.0:
            cost += self._penalty(new_board - self.new_ts)
        return cost

    def _evaluate(self, c, route, route_len, t0, check_capacity):
        """Plan + (optional) capacity check + cost of a candidate; returns (cost, pruned_len) or None."""
        self._build_order(c, True)
        n = self._plan(c, route, route_len, self.pr_route, self.c_board, self.c_alight)
        if check_capacity and not self._capacity_ok(c, n):
            return None
        self._simulate(c, self.pr_route, n, self.c_times, t0)
        return self._cost(c, t0), n

    # ================================================================== insertion

    def _insert_or_merge(self, src, src_len, floor, index, min_merge, out):
        """Copy ``src`` into ``out`` with ``floor`` inserted/merged at ``index``; returns (len, position)."""
        for i in range(src_len):
            out[i] = src[i]
        if index - 1 >= min_merge and index - 1 < src_len and src[index - 1] == floor:
            return src_len, index - 1
        if index < src_len and src[index] == floor:
            return src_len, index
        for i in range(src_len, index, -1):
            out[i] = out[i - 1]
        out[index] = floor
        return src_len + 1, index

    # ================================================================== main entry

    def allocate(self, req_id, ts, src, dst):
        """Returns (elevator_id, route_list, cost). ``route_list`` is the only object allocated per call."""
        m = self.m
        self.new_src, self.new_dst, self.new_ts = src, dst, ts
        best_cost = _INF
        best_added = 0
        best_weight = 0.0
        best_eid = -1
        found = False

        for c in range(m.n):  # slots are sorted by elevator id
            if not m.op[c] or m.vib[c] > m.max_vibration:
                continue
            t0 = m.t[c]
            self._build_order(c, False)
            base_len = self._plan(c, m.queue[c], m.qn[c], self.base_route, self.base_board, self.base_alight)
            self._simulate(c, self.base_route, base_len, self.base_times, t0)
            for i in range(base_len + 1):
                qs_len, src_idx = self._insert_or_merge(self.base_route, base_len, src, i, 0, self.q_src)
                for j in range(src_idx + 1, qs_len + 1):
                    cand_len, _ = self._insert_or_merge(self.q_src, qs_len, dst, j, src_idx + 1, self.cand)
                    res = self._evaluate(c, self.cand, cand_len, t0, True)
                    if res is None:
                        continue
                    cost, n = res
                    added = n - base_len
                    if self._is_better(cost, added, m.w[c], m.eid[c], found, best_cost, best_added, best_weight, best_eid):
                        found = True
                        best_cost, best_added, best_weight, best_eid = cost, added, m.w[c], m.eid[c]
                        for k in range(n):
                            self.best_route[k] = self.pr_route[k]
                        self.best_len = n

        if not found:
            return self._fallback(src, dst)
        return best_eid, [self.best_route[k] for k in range(self.best_len)], best_cost

    @staticmethod
    def _is_better(cost, added, weight, eid, found, b_cost, b_added, b_weight, b_eid):
        if not found:
            return True
        if cost < b_cost - TIE_EPS:
            return True
        if cost > b_cost + TIE_EPS:
            return False
        return (added, weight, eid) < (b_added, b_weight, b_eid)

    def _fallback(self, src, dst):
        """Append [src, dst] to the operational car whose queue empties earliest (no capacity check)."""
        m = self.m
        ok_slots = [c for c in range(m.n) if m.op[c] and m.vib[c] <= m.max_vibration]
        unlocked = [c for c in ok_slots if not m.atcap[c]]
        pool = unlocked or ok_slots or list(range(m.n))
        best_key = None
        best_c = -1
        for c in pool:
            t0 = m.t[c]
            self._build_order(c, False)
            base_len = self._plan(c, m.queue[c], m.qn[c], self.base_route, self.base_board, self.base_alight)
            self._simulate(c, self.base_route, base_len, self.base_times, t0)
            key = (self._completion_time(c, base_len, t0), m.eid[c])
            if best_key is None or key < best_key:
                best_key, best_c = key, c
        c = best_c
        t0 = m.t[c]
        self._build_order(c, False)
        base_len = self._plan(c, m.queue[c], m.qn[c], self.base_route, self.base_board, self.base_alight)
        self._simulate(c, self.base_route, base_len, self.base_times, t0)
        n = base_len
        for k in range(n):
            self.cand[k] = self.base_route[k]
        if n == 0 or self.cand[n - 1] != src:
            self.cand[n] = src
            n += 1
        self.cand[n] = dst
        n += 1
        route = [self.cand[k] for k in range(n)]
        cost, _ = self._evaluate(c, self.cand, n, t0, False)
        return m.eid[c], route, cost
