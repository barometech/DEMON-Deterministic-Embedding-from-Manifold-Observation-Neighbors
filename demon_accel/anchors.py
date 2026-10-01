"""Trajectory-stratum accelerator for iterated products y_{k+1} = A y_k.

The first r+2 iterates (anchors) span the subspace the trajectory lives in; after
projecting A onto it each step costs O(n r). Correctness is certified by exact checks:
at served steps 1, 2, 4, 8, ... (geometric) and then every `check_every` steps, the true
product A @ y_prev is computed and compared with the served step. On failure the iterator
refuses, recomputes every iterate since the last verified one exactly, and continues with
exact products. Every returned iterate is therefore either exact or within `tol` of a
verified step.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .sketch import _xp, _norm


@dataclass
class AnchorIterator:
    A: object
    rank: int
    tol: float = 1e-9
    check_every: int = 50
    n_anchors: int | None = None
    Q: object = field(default=None, init=False)
    AQ: object = field(default=None, init=False)
    c: object = field(default=None, init=False)
    steps: int = field(default=0, init=False)          # served steps
    checks: int = field(default=0, init=False)
    refused: bool = field(default=False, init=False)
    last_residual: float = field(default=0.0, init=False)
    flops: int = field(default=0, init=False)

    def _n_anchors(self):
        return self.rank + 2 if self.n_anchors is None else self.n_anchors

    def _is_check_step(self, s: int) -> bool:
        if s <= 0:
            return False
        if s < self.check_every:
            return (s & (s - 1)) == 0                 # 1, 2, 4, 8, ...
        return s % self.check_every == 0

    def run(self, y0, n_steps: int):
        """Iterate n_steps times from y0. Returns list of iterates. Falls back to exact
        products once refused (and repairs the unverified window)."""
        xp = _xp(self.A)
        n = self.A.shape[0]
        out = []
        y = y0
        na = self._n_anchors()
        anchors = []
        last_verified = -1                            # index into out of last exact/verified iterate
        for k in range(n_steps):
            if self.refused or len(anchors) < na:
                y = self.A @ y
                self.flops += 2 * n * n
                out.append(y)
                last_verified = k
                if not self.refused:
                    anchors.append(y)
                    if len(anchors) == na:
                        self._fit(anchors)
                        self.c = self.Q.T @ y
                continue
            # served step: y_{k+1} = A Q (Q^T y_k) = AQ c
            y = self.AQ @ self.c
            self.flops += 4 * n * self.rank
            self.steps += 1
            if self._is_check_step(self.steps):
                y_true = self.A @ out[-1]
                self.flops += 2 * n * n
                self.checks += 1
                res = _norm(xp, y - y_true) / max(_norm(xp, y_true), 1e-300)
                self.last_residual = res
                y = y_true                            # free correction at check steps
                if res > self.tol:
                    self.refused = True
                    # repair the unverified window (last_verified+1 .. k-1) exactly
                    z = out[last_verified]
                    for j in range(last_verified + 1, k):
                        z = self.A @ z
                        self.flops += 2 * n * n
                        out[j] = z
                    y = self.A @ z
                    self.flops += 2 * n * n
                    self.c = None
                    out.append(y)
                    last_verified = k
                    continue
                last_verified = k
            self.c = self.Q.T @ y
            out.append(y)
        return out

    def _fit(self, anchors):
        xp = _xp(self.A)
        M = np.stack(anchors, 1) if xp is np else xp.stack(anchors, 1)
        U = np.linalg.svd(M, full_matrices=False)[0] if xp is np else xp.linalg.svd(M, full_matrices=False)[0]
        self.Q = U[:, : self.rank]
        self.AQ = self.A @ self.Q
        n = self.A.shape[0]
        self.flops += 2 * n * n * self.rank
