"""Trajectory-stratum accelerator for iterated products y_{k+1} = A y_k.

The first r+2 iterates (anchors) span the subspace the trajectory lives in; after
projecting A onto it each step costs O(n r). A drift certificate re-checks the residual of
the true iterate outside the subspace every `check_every` steps and refuses on failure.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .sketch import _xp, _qr, _norm


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
    steps: int = field(default=0, init=False)
    refused: bool = field(default=False, init=False)
    last_residual: float = field(default=0.0, init=False)
    flops: int = field(default=0, init=False)

    def _n_anchors(self):
        return self.rank + 2 if self.n_anchors is None else self.n_anchors

    def run(self, y0, n_steps: int):
        """Iterate n_steps times from y0. Returns list of iterates (dense vectors).
        Falls back to exact products once refused."""
        xp = _xp(self.A)
        n = self.A.shape[0]
        out = []
        y = y0
        na = self._n_anchors()
        anchors = []
        for k in range(n_steps):
            if self.refused or len(anchors) < na:
                y = self.A @ y
                self.flops += 2 * n * n
                out.append(y)
                if not self.refused:
                    anchors.append(y)
                    if len(anchors) == na:
                        self._fit(anchors)
                        self.c = self.Q.T @ y
                continue
            # served step: y_{k+1} = A Q c = AQ c ;  c <- Q^T y_{k+1}
            y = self.AQ @ self.c
            self.c = self.Q.T @ y
            self.flops += 4 * n * self.rank
            self.steps += 1
            if self.steps % self.check_every == 0:
                y_true = self.A @ out[-1]
                self.flops += 2 * n * n
                res = _norm(xp, y_true - self.Q @ (self.Q.T @ y_true)) / max(_norm(xp, y_true), 1e-300)
                self.last_residual = res
                if res > self.tol:
                    self.refused = True
                    y = y_true
                    self.c = None
            out.append(y)
        return out

    def _fit(self, anchors):
        xp = _xp(self.A)
        M = xp.stack(anchors, 1) if xp is not np else np.stack(anchors, 1)
        if xp is np:
            U = np.linalg.svd(M, full_matrices=False)[0]
        else:
            U = xp.linalg.svd(M, full_matrices=False)[0]
        self.Q = U[:, : self.rank]
        self.AQ = self.A @ self.Q
        n = self.A.shape[0]
        self.flops += 2 * n * n * self.rank
