"""Finds the glowing ball in a camera frame (PLAN.md §3).

Two profiles:
  dark  — low-exposure camera, the ball is the only lit thing (sample B). A color
          threshold, nearby pieces merged (fingers split the ball), centroid.
  lit   — auto-exposure room (sample A). A red halo around a blown-out white core,
          scored blobs, white-only blobs accepted only near the prediction.
All coordinates are normalized: x, y in 0..1 of the camera frame (y down),
radius as a fraction of the frame width.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

W, H = 320, 180


@dataclass
class Observation:
    x: float
    y: float
    r: float
    conf: float
    t: float
    area: int = 0
    elongation: float = 1.0  # >1: motion-blur streak
    angle: float = 0.0       # streak direction (radians)


@dataclass
class TrackerConfig:
    profile: str = "auto"         # auto | dark | lit
    hue: float = 0.0              # degrees
    hue_tolerance: float = 15.0
    min_saturation: float = 0.55
    min_value: float = 0.24       # dark profile brightness floor (0..1)
    lit_min_value: float = 0.6    # halo brightness floor in a lit room
    core_value: float = 0.9       # every channel above this = blown-out core
    exclusion: list = field(default_factory=list)  # [[x0, y0, x1, y1], ...] normalized

    @classmethod
    def from_settings(cls, s: dict) -> "TrackerConfig":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in s.items() if k in keys})


class Tracker:
    def __init__(self, config: TrackerConfig | None = None):
        self.config = config or TrackerConfig()
        self.active_profile = "dark" if self.config.profile != "lit" else "lit"
        self._lit_fraction = 0.0
        self.last_ms = 0.0
        self.mask = np.zeros((H, W), np.uint8)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._exclusion_mask = None
        self._exclusion_key = None

    # -- helpers -----------------------------------------------------------------
    def _color_mask(self, hsv: np.ndarray, min_s: float, min_v: float) -> np.ndarray:
        """Pixels within the hue band (wrapping around red) and above the saturation/value floors."""
        c = self.config
        center = (c.hue % 360.0) / 2.0  # OpenCV hue is 0..179
        tol = c.hue_tolerance / 2.0
        lo, hi = center - tol, center + tol
        s, v = int(min_s * 255), int(min_v * 255)
        ranges = []
        if lo < 0:
            ranges += [(0, hi), (180 + lo, 179)]
        elif hi > 179:
            ranges += [(lo, 179), (0, hi - 180)]
        else:
            ranges.append((lo, hi))
        mask = None
        for a, b in ranges:
            m = cv2.inRange(hsv, (int(np.ceil(a)), s, v), (int(np.floor(b)), 255, 255))
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        return mask

    @staticmethod
    def downscale(frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        if w > 2 * W * 2:  # e.g. 1080p: a cheap linear step to 640x360 first, then area-average
            frame_bgr = cv2.resize(frame_bgr, (2 * W, 2 * H), interpolation=cv2.INTER_LINEAR)
        return cv2.resize(frame_bgr, (W, H), interpolation=cv2.INTER_AREA)

    def _exclusion(self) -> np.ndarray | None:
        key = tuple(tuple(r) for r in self.config.exclusion)
        if key != self._exclusion_key:
            self._exclusion_key = key
            if not key:
                self._exclusion_mask = None
            else:
                m = np.ones((H, W), bool)
                for x0, y0, x1, y1 in key:
                    xa, xb = sorted((x0, x1))
                    ya, yb = sorted((y0, y1))
                    m[int(ya * H):int(np.ceil(yb * H)), int(xa * W):int(np.ceil(xb * W))] = False
                self._exclusion_mask = m
        return self._exclusion_mask

    def _choose_profile(self, hsv: np.ndarray) -> str:
        if self.config.profile in ("dark", "lit"):
            return self.config.profile
        lit = float(np.count_nonzero(hsv[..., 2] > 40)) / hsv[..., 2].size
        self._lit_fraction += 0.1 * (lit - self._lit_fraction)
        # hysteresis around the "98% near-black" rule
        if self.active_profile == "dark" and self._lit_fraction > 0.06:
            self.active_profile = "lit"
        elif self.active_profile == "lit" and self._lit_fraction < 0.02:
            self.active_profile = "dark"
        return self.active_profile

    # -- main ----------------------------------------------------------------------
    def process(self, frame_bgr: np.ndarray, t: float | None = None,
                predicted: tuple[float, float] | None = None, gate: float = 1.0) -> Observation | None:
        t0 = time.perf_counter()
        t = time.monotonic() if t is None else t
        small = self.downscale(frame_bgr)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        self.small = small
        profile = self._choose_profile(hsv)
        if profile == "dark":
            obs = self._dark(small, hsv, t)
        else:
            obs = self._lit(small, hsv, t, predicted, gate)
        self.last_ms = (time.perf_counter() - t0) * 1000
        return obs

    def _dark(self, small, hsv, t) -> Observation | None:
        c = self.config
        m8 = self._color_mask(hsv, c.min_saturation, c.min_value)
        ex = self._exclusion()
        if ex is not None:
            m8[~ex] = 0
        mask = m8 > 0
        self.mask = m8
        if cv2.countNonZero(m8) < 3:
            return None
        # Fingers split the ball into pieces: close the gaps, then take the biggest
        # group plus any other group within about one ball radius of it.
        grown = cv2.dilate(m8, self._kernel, iterations=2)
        n, labels, stats, cents = cv2.connectedComponentsWithStats(grown, connectivity=8)
        if n <= 1:
            return None
        areas = stats[1:, cv2.CC_STAT_AREA]
        best = 1 + int(np.argmax(areas))
        r_best = np.sqrt(areas[best - 1] / np.pi)
        keep = [best]
        for i in range(1, n):
            if i != best and np.hypot(*(cents[i] - cents[best])) < 2.0 * r_best + np.sqrt(stats[i, cv2.CC_STAT_AREA] / np.pi):
                keep.append(i)
        group = np.isin(labels, keep) & mask
        ys, xs = np.nonzero(group)
        if len(xs) < 3:
            return None
        return self._observation(xs, ys, t, conf=min(1.0, len(xs) / 30.0))

    def _lit(self, small, hsv, t, predicted, gate) -> Observation | None:
        c = self.config
        halo = self._color_mask(hsv, c.min_saturation, c.lit_min_value) > 0
        cv_ = int(c.core_value * 255)
        core = cv2.inRange(small, (cv_, cv_, cv_), (255, 255, 255)) > 0
        ex = self._exclusion()
        if ex is not None:
            halo &= ex
            core &= ex
        both = (halo | core).astype(np.uint8) * 255
        self.mask = both
        if cv2.countNonZero(both) < 3:
            return None
        grown = cv2.dilate(both, self._kernel)
        n, labels, stats, cents = cv2.connectedComponentsWithStats(grown, connectivity=8)
        best, best_score = None, -1e9
        halo_dil = cv2.dilate(halo.astype(np.uint8), self._kernel).astype(bool)
        # Per-blob counts in one pass each, instead of slicing every blob.
        n_halo_all = np.bincount(labels[halo], minlength=n)
        touch_all = np.bincount(labels[core & halo_dil], minlength=n)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < 4:
                continue
            n_halo = int(n_halo_all[i])
            core_touches_halo = touch_all[i] > 0
            fill = area / float(w * h)
            if n_halo == 0:
                # White only (the ball held high, blown out): accept only near the prediction.
                if predicted is None:
                    continue
                d = np.hypot(cents[i][0] / W - predicted[0], cents[i][1] / W - predicted[1] * H / W)
                if d > 0.08:
                    continue
            score = 1.0 * np.log(area) + 2.0 * core_touches_halo + 1.0 * fill + 0.5 * (n_halo > 0)
            if predicted is not None:
                d = np.hypot(cents[i][0] / W - predicted[0], (cents[i][1] - predicted[1] * H) / W)
                score -= 3.0 * d / max(gate, 1e-3)
            if score > best_score:
                best, best_score = i, score
        if best is None:
            return None
        ys, xs = np.nonzero((labels == best) & (both > 0))
        if len(xs) < 3:
            return None
        return self._observation(xs, ys, t, conf=float(np.clip(best_score / 6.0, 0.2, 1.0)))

    @staticmethod
    def _observation(xs, ys, t, conf) -> Observation:
        cx, cy = xs.mean(), ys.mean()
        area = len(xs)
        el, ang = 1.0, 0.0
        if area >= 6:
            cov = np.cov(np.vstack([xs, ys]))
            ev, evec = np.linalg.eigh(cov)
            if ev[0] > 1e-6:
                el = float(np.sqrt(ev[1] / ev[0]))
                ang = float(np.arctan2(evec[1, 1], evec[0, 1]))
        return Observation(x=(cx + 0.5) / W, y=(cy + 0.5) / H, r=float(np.sqrt(area / np.pi)) / W,
                           conf=conf, t=t, area=area, elongation=el, angle=ang)

    def sample_color(self, frame_bgr: np.ndarray, x: float, y: float, radius: int = 3) -> dict:
        """Click-to-calibrate: read the ball's hue/saturation/value around (x, y)."""
        small = self.downscale(frame_bgr)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        px, py = int(x * W), int(y * H)
        patch = hsv[max(0, py - radius):py + radius + 1, max(0, px - radius):px + radius + 1].reshape(-1, 3)
        patch = patch[patch[:, 2] > 30]
        if len(patch) == 0:
            return {}
        ang = patch[:, 0].astype(float) * 2 * np.pi / 180
        hue = float(np.degrees(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360)
        sat = float(np.percentile(patch[:, 1], 20)) / 255
        val = float(np.percentile(patch[:, 2], 20)) / 255
        self.config.hue = hue
        self.config.min_saturation = round(max(0.3, sat * 0.8), 3)
        self.config.min_value = round(max(0.1, val * 0.5), 3)
        return {"hue": hue, "min_saturation": self.config.min_saturation, "min_value": self.config.min_value}
