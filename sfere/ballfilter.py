"""Smooths tracker observations and turns them into motion features and events (PLAN.md §3.3, §3.5).

States: tracking -> coasting (no detection for up to 250 ms) -> lost; re-acquiring
from lost needs 2 consecutive detections. Positions are normalized camera coords.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .tracker import Observation

COAST_S = 0.25
REACQUIRE_N = 2
MAX_EXTRAPOLATE_S = 0.1


@dataclass
class BallState:
    x: float = 0.5
    y: float = 0.5
    vx: float = 0.0
    vy: float = 0.0
    r: float = 0.02
    visible: float = 0.0       # 1 tracking, fades while coasting, 0 lost
    state: str = "lost"
    speed: float = 0.0         # normalized 0..1
    accel: float = 0.0         # normalized 0..1
    angle: float = 0.0         # direction of motion, radians
    curvature: float = 0.0     # 0..1
    t: float = 0.0
    events: list = field(default_factory=list)  # names fired since last read


class BallFilter:
    def __init__(self, q_accel: float = 40.0, r_pos: float = 0.004):
        self.q = q_accel
        self.r_pos = r_pos
        self.x = np.zeros(4)  # x, y, vx, vy
        self.P = np.eye(4)
        self.t = None
        self.radius = 0.02
        self.state = "lost"
        self.last_seen = -1e9
        self.found_streak = 0
        self.lost_since = 0.0
        self._lock = threading.Lock()
        self._events: list[str] = []
        self._speed_hist = deque(maxlen=30)
        self._last_stall = -1e9
        self._turn_hist = deque()  # (t, dtheta)
        self._prev_angle = None
        self.speed = self.accel = self.curvature = 0.0
        self._peak_speed = 0.0
        self.history = deque(maxlen=600)  # (t, x, y) of accepted observations, for tests/debug

    # ---------------------------------------------------------------------------------
    def _predict(self, t: float):
        if self.t is None:
            self.t = t
            return
        dt = max(0.0, min(t - self.t, 0.5))
        if dt == 0:
            return
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = self.q
        G = np.array([[dt * dt / 2, 0], [0, dt * dt / 2], [dt, 0], [0, dt]])
        Q = G @ G.T * q * q
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        self.t = t

    def gate(self) -> float:
        v = math.hypot(self.x[2], self.x[3])
        return 0.12 + v * 0.08

    def prediction(self) -> tuple[float, float] | None:
        return None if self.state == "lost" else (float(self.x[0]), float(self.x[1]))

    def update(self, obs: Observation | None, t: float) -> None:
        with self._lock:
            self._predict(t)
            if obs is not None and self.state != "lost":
                d = math.hypot(obs.x - self.x[0], obs.y - self.x[1])
                if d > self.gate() and t - self.last_seen < 0.5:
                    obs = None  # implausible jump: reject
            if obs is None:
                self.found_streak = 0
                if self.state == "lost":
                    self.x[2:] = 0.0   # don't let a lost ball drift off on its last velocity
                if self.state != "lost" and t - self.last_seen > COAST_S:
                    self.state = "lost"
                    self.lost_since = t
                    self._events.append("lost")
                elif self.state == "tracking" and t - self.last_seen > 0.06:
                    self.state = "coasting"
                return
            if self.state == "lost":
                self.found_streak += 1
                if self.found_streak < REACQUIRE_N:
                    self.x[:] = (obs.x, obs.y, 0, 0)
                    self.P = np.diag([1e-4, 1e-4, 1.0, 1.0])
                    self.t = t
                    return
                self.x[:2] = (obs.x, obs.y)
                self.state = "tracking"
                self._events.append("found")
            # Kalman update
            R = np.eye(2) * (self.r_pos / max(obs.conf, 0.1)) ** 2
            Hm = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], float)
            y = np.array([obs.x, obs.y]) - Hm @ self.x
            S = Hm @ self.P @ Hm.T + R
            K = self.P @ Hm.T @ np.linalg.inv(S)
            self.x = self.x + K @ y
            self.P = (np.eye(4) - K @ Hm) @ self.P
            self.radius += 0.4 * (obs.r - self.radius)
            self.last_seen = t
            self.state = "tracking"
            self.history.append((t, float(self.x[0]), float(self.x[1])))
            self._features(t)

    def _features(self, t: float) -> None:
        vx, vy = self.x[2], self.x[3]
        sp = math.hypot(vx, vy)
        speed = min(1.0, sp / 2.5)  # 2.5 frame widths per second = 1
        prev = self.speed
        self.speed = speed
        self._speed_hist.append((t, speed))
        if len(self._speed_hist) >= 2:
            (t0, s0), (t1, s1) = self._speed_hist[-2], self._speed_hist[-1]
            a = abs(s1 - s0) / max(t1 - t0, 1e-3)
            self.accel = min(1.0, 0.7 * self.accel + 0.3 * a / 8.0)
            if a > 8.0 and s1 > s0:
                self._events.append("flick")
        # stall: moving fast within the last half second, (nearly) still now
        recent = [s for (ts, s) in self._speed_hist if t - ts <= 0.5]
        if recent and max(recent) > 0.3 and speed < 0.08 and t - self._last_stall > 0.5:
            self._events.append("stall")
            self._last_stall = t
        if vy < -1.6:
            self._events.append("rise")
        elif vy > 1.6:
            self._events.append("drop")
        if sp > 0.15:
            ang = math.atan2(vy, vx)
            if self._prev_angle is not None:
                d = (ang - self._prev_angle + math.pi) % (2 * math.pi) - math.pi
                self._turn_hist.append((t, d))
                while self._turn_hist and t - self._turn_hist[0][0] > 1.5:
                    self._turn_hist.popleft()
                total = sum(x for _, x in self._turn_hist)
                self.curvature = min(1.0, abs(d) * 4)
                if abs(total) > 2 * math.pi:
                    self._events.append("loop")
                    self._turn_hist.clear()
            self._prev_angle = ang

    def state_at(self, t: float) -> BallState:
        """Extrapolated state for rendering at time t (render clock = capture clock)."""
        with self._lock:
            events, self._events = self._events, []
            if self.t is None:
                return BallState(events=events)
            dt = min(max(t - self.t, 0.0), MAX_EXTRAPOLATE_S)
            x = self.x[0] + self.x[2] * dt
            y = self.x[1] + self.x[3] * dt
            if self.state == "tracking":
                vis = 1.0
            elif self.state == "coasting":
                vis = max(0.0, 1.0 - (t - self.last_seen) / COAST_S)
            else:
                vis = 0.0
            return BallState(x=float(x), y=float(y), vx=float(self.x[2]), vy=float(self.x[3]), r=float(self.radius),
                             visible=vis, state=self.state, speed=self.speed, accel=self.accel,
                             angle=float(math.atan2(self.x[3], self.x[2])), curvature=self.curvature, t=t,
                             events=events)
