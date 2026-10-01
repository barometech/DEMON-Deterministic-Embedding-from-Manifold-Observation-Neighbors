"""Stratified admission: decide whether an operator may be served from a low-rank stratum."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from .sketch import adaptive_rank, sketch_svd, _xp
from .lowrank import LowRankOperator


class Status(str, Enum):
    ADMIT = "ADMIT"
    REFUSE_FULL_RANK = "REFUSE_FULL_RANK"      # no rank <= r_max meets the tolerance
    REFUSE_NO_SPEEDUP = "REFUSE_NO_SPEEDUP"    # a rank meets tol but costs >= speedup_min * dense


@dataclass
class Admission:
    status: Status
    rank: int
    rel_err_bound: float        # certified (prob >= 1-1e-10) relative spectral residual / ||A||_F
    rel_err_est: float          # non-certified max-probe estimate
    flops_full: int
    flops_served: int
    operator: LowRankOperator | None = None

    @property
    def admitted(self) -> bool:
        return self.status is Status.ADMIT

    @property
    def speedup_flops(self) -> float:
        return self.flops_full / max(1, self.flops_served)


def admit(A, tol: float = 1e-6, n_cols: int | None = None, r_max: int | None = None,
          speedup_min: float = 2.0, power_iters: int = 1, seed: int = 0) -> Admission:
    """Probe A (m x k) and decide the stratum for products A @ B with B: (k x n_cols).

    tol         relative residual the served product must satisfy (certified bound)
    n_cols      number of columns of B the operator will be applied to (cost model); default k
    r_max       largest rank considered; default min(m,k)//4
    speedup_min minimum FLOP reduction required to admit
    """
    m, k = A.shape
    n = k if n_cols is None else int(n_cols)
    if r_max is None:
        r_max = max(1, min(m, k) // 4)
    rank, Q, rel_bound, rel_max = adaptive_rank(A, tol, r_max, power_iters=power_iters, seed=seed)
    flops_full = LowRankOperator.flops_dense(m, k, n)
    flops_served = 2 * rank * (k * n + m * n) + rank * n
    if rel_bound > tol:
        return Admission(Status.REFUSE_FULL_RANK, rank, rel_bound, rel_max, flops_full, flops_served)
    if flops_served * speedup_min > flops_full:
        return Admission(Status.REFUSE_NO_SPEEDUP, rank, rel_bound, rel_max, flops_full, flops_served)
    U, S, Vt = sketch_svd(A, rank, power_iters=power_iters, seed=seed)
    return Admission(Status.ADMIT, rank, rel_bound, rel_max, flops_full, flops_served,
                     LowRankOperator(U, S, Vt))
