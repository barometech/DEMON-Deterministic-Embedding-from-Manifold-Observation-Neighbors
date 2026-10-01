"""Stratified admission: decide whether an operator may be served from a low-rank stratum.

Certificate (Halko-Martinsson-Tropp 2011, Lemma 4.1; failure probability <= n_rungs * 1e-10):
    ||A - Q Q^T A||_2 <= bound.
The served operator is the rank-r' truncation (Q Q^T A)_{r'}. Because (I - Q Q^T) A and
Q Q^T A - (Q Q^T A)_{r'} have orthogonal column spaces,
    ||A - (Q Q^T A)_{r'}||_2 <= sqrt(bound^2 + s_{r'+1}^2),     s = singular values of Q^T A,
which is what `rel_err_bound` certifies, divided by the chosen norm of A (see `norm`).

What `rel_err_bound <= tol` guarantees for the served product C_hat = A_hat @ B:
    norm="fro"  (default):  ||C_hat - A B||_2 <= tol ||A||_F ||B||_2,  ||C_hat - A B||_F <= tol ||A||_F ||B||_F
    norm="spectral":        ||C_hat - A B||_2 <= tol ||A||_2 ||B||_2   (||A||_2 estimated from below by
                            s_1(Q^T A) after power iteration, so the certificate is conservative)
It does NOT bound ||C_hat - A B|| / ||A B||: for B concentrated on the dropped directions that
ratio can be O(1). The probe reacts to the Frobenius tail, so operators with slowly decaying
spectra are refused pessimistically (up to ~8 sqrt(stable rank) in the worst case).
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import math

from .sketch import adaptive_rank, _xp
from .lowrank import LowRankOperator


class Status(str, Enum):
    ADMIT = "ADMIT"
    REFUSE_FULL_RANK = "REFUSE_FULL_RANK"      # no probed width meets the tolerance
    REFUSE_NO_SPEEDUP = "REFUSE_NO_SPEEDUP"    # tolerance met only at a rank with < speedup_min FLOP gain


@dataclass
class Admission:
    status: Status
    rank: int                   # served rank r' (ADMIT) or last probed width (REFUSE)
    rel_err_bound: float        # certified ||A - A_hat||_2 / ||A||_{norm} for the SERVED operator
    rel_err_est: float          # non-certified max-probe estimate of the projector residual, same normaliser
    flops_full: int
    flops_served: int
    operator: LowRankOperator | None = None
    norm: str = "fro"
    probe_flops: int = 0        # approximate cost of the probe itself (not amortised by the gate)

    @property
    def admitted(self) -> bool:
        return self.status is Status.ADMIT

    @property
    def speedup_flops(self) -> float:
        return self.flops_full / max(1, self.flops_served)


def admit(A, tol: float = 1e-6, n_cols: int | None = None, r_max: int | None = None,
          speedup_min: float = 2.0, power_iters: int = 1, seed: int = 0, oversample: int = 8,
          norm: str = "fro") -> Admission:
    """Probe A (m x k) and decide the stratum for products A @ B with B: (k x n_cols).

    tol         certified relative residual (see module docstring for the exact inequality)
    n_cols      number of columns of B (cost model); default k
    r_max       largest served rank considered; default = largest rank with >= speedup_min FLOP gain
    speedup_min minimum FLOP reduction required to admit
    norm        "fro" or "spectral": normaliser of the certificate
    The probe cost is NOT amortised by the gate; see Admission.probe_flops and docs/DESIGN.md.
    """
    xp = _xp(A)
    m, k = A.shape
    n = k if n_cols is None else int(n_cols)
    r_adm = LowRankOperator.max_admissible_rank(m, k, n, speedup_min)
    if r_max is None:
        r_max = r_adm
    r_max = max(1, min(int(r_max), min(m, k)))
    flops_full = LowRankOperator.flops_dense(m, k, n)
    normA_fro = float(xp.linalg.norm(A))
    if normA_fro == 0.0:                       # zero operator: rank-1 zero factors, error exactly 0
        U = A[:, :1] * 0; Vt = A[:1, :] * 0; S = Vt[:, 0] * 0
        return Admission(Status.ADMIT, 1, 0.0, 0.0, flops_full, LowRankOperator.flops_served(m, k, n, 1),
                         LowRankOperator(U, S, Vt), norm, 0)
    # ladder runs on basis width l = rank + oversample, capped so that l <= r_max + oversample
    rank_l, Q, rel_bound_fro, rel_max_fro = adaptive_rank(A, tol if norm == "fro" else 0.0, r_max,
                                                          oversample=oversample, power_iters=power_iters,
                                                          seed=seed, stop_tol_abs=tol if norm == "spectral" else None)
    l = int(Q.shape[1])
    bound_abs = rel_bound_fro * normA_fro
    est_abs = rel_max_fro * normA_fro
    probe_flops = _probe_flops(m, k, l, power_iters, oversample)
    # small SVD of Q^T A (needed for serving anyway)
    B = Q.T @ A
    Ub, S, Vt = xp.linalg.svd(B, full_matrices=False)
    S_list = [float(s) for s in S]
    normA = normA_fro if norm == "fro" else max(S_list[0], 1e-300)
    rel_bound_proj = bound_abs / normA
    rel_est = est_abs / normA
    if rel_bound_proj > tol:
        return Admission(Status.REFUSE_FULL_RANK, l, rel_bound_proj, rel_est, flops_full,
                         LowRankOperator.flops_served(m, k, n, l), None, norm, probe_flops)
    # smallest truncation r' <= l whose orthogonal-range certificate meets tol
    r_serve, cert = l, rel_bound_proj
    for rp in range(1, l + 1):
        tail = S_list[rp] if rp < l else 0.0
        c = math.sqrt(bound_abs ** 2 + tail ** 2) / normA
        if c <= tol:
            r_serve, cert = rp, c
            break
    flops_served = LowRankOperator.flops_served(m, k, n, r_serve)
    if flops_served * speedup_min > flops_full or r_serve > r_max:
        return Admission(Status.REFUSE_NO_SPEEDUP, r_serve, cert, rel_est, flops_full, flops_served,
                         None, norm, probe_flops)
    U = Q @ Ub[:, :r_serve]
    op = LowRankOperator(U, S[:r_serve], Vt[:r_serve, :])
    return Admission(Status.ADMIT, r_serve, cert, rel_est, flops_full, flops_served, op, norm, probe_flops)


def _probe_flops(m, k, l_last, q, oversample):
    """Approximate probe cost: range finders over the ladder (sum of widths ~ 2 l_last),
    each 2 m k l (2q+1), plus 10 probe vectors (20 m k) per rung, plus Q^T A (2 m k l)."""
    widths = []
    l = min(8 + oversample, l_last)
    while True:
        widths.append(l)
        if l >= l_last:
            break
        l = min(l_last, int(math.ceil((l - oversample) * 2.0)) + oversample)
    return sum(2 * m * k * w * (2 * q + 1) + 20 * m * k for w in widths) + 2 * m * k * l_last
