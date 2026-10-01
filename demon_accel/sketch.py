"""Randomized range finder with adaptive rank and a posteriori residual estimate.

References: Halko, Martinsson, Tropp, SIAM Review 53(2), 2011 (Algs. 4.1-4.4, Lemma 4.1).
Backend-agnostic over numpy and torch (duck typing on the array's module).
"""
from __future__ import annotations
import math
import numpy as np


def _xp(a):
    """Return the array module of `a` (numpy or torch)."""
    mod = type(a).__module__.split(".")[0]
    if mod == "torch":
        import torch
        return torch
    return np


def _randn(xp, shape, like, seed):
    if xp is np:
        return np.random.default_rng(seed).standard_normal(shape).astype(like.dtype, copy=False)
    g = xp.Generator(device=like.device).manual_seed(int(seed))
    return xp.randn(*shape, generator=g, dtype=like.dtype, device=like.device)


def _qr(xp, Y):
    if xp is np:
        return np.linalg.qr(Y, mode="reduced")[0]
    return xp.linalg.qr(Y, mode="reduced")[0]


def _norm(xp, a):
    return float(xp.linalg.norm(a))


def range_finder(A, rank: int, oversample: int = 8, power_iters: int = 1, seed: int = 0):
    """Orthonormal basis Q (m x (rank+oversample)) approximating the range of A.

    Cost: (power_iters+1) products of A with a (k x l) block plus the transposes,
    i.e. about 2*m*k*l*(2*power_iters+1) flops.
    """
    xp = _xp(A)
    m, k = A.shape
    l = min(rank + oversample, min(m, k))
    Omega = _randn(xp, (k, l), A, seed)
    Y = A @ Omega
    Q = _qr(xp, Y)
    for _ in range(power_iters):          # subspace iteration with re-orthonormalisation
        Z = _qr(xp, A.T @ Q)
        Q = _qr(xp, A @ Z)
    return Q


def residual_estimate(A, Q, n_probes: int = 10, seed: int = 1):
    """A posteriori estimate of ||(I - Q Q^T) A||_2 (Halko et al., Lemma 4.1).

    With n_probes Gaussian vectors w_i, the quantity
        10*sqrt(2/pi) * max_i ||(I - Q Q^T) A w_i||
    upper-bounds the spectral residual with probability >= 1 - 10^-n_probes.
    We return both the bound and the plain max (a tighter, non-certified value).
    """
    xp = _xp(A)
    k = A.shape[1]
    W = _randn(xp, (k, n_probes), A, seed)
    AW = A @ W
    R = AW - Q @ (Q.T @ AW)
    if xp is np:
        col = np.linalg.norm(R, axis=0)
    else:
        col = xp.linalg.norm(R, dim=0)
    mx = float(col.max())
    bound = 10.0 * math.sqrt(2.0 / math.pi) * mx
    return bound, mx


def sketch_svd(A, rank: int, oversample: int = 8, power_iters: int = 1, seed: int = 0, Q=None):
    """Rank-`rank` truncated SVD via randomized range finder. Returns (U, S, Vt).

    If `Q` (an orthonormal basis) is given it is used instead of a fresh range finder.
    NOTE: truncating to `rank` < Q.shape[1] is NOT covered by a certificate computed for Q;
    callers that need the certificate must keep all Q.shape[1] components (see gate.admit).
    """
    xp = _xp(A)
    if Q is None:
        Q = range_finder(A, rank, oversample, power_iters, seed)
    B = Q.T @ A                                   # (l x k) small
    if xp is np:
        Ub, S, Vt = np.linalg.svd(B, full_matrices=False)
    else:
        Ub, S, Vh = xp.linalg.svd(B, full_matrices=False)
        Vt = Vh
    U = Q @ Ub
    return U[:, :rank], S[:rank], Vt[:rank, :]


def adaptive_rank(A, tol: float, r_max: int, r0: int = 8, growth: float = 2.0,
                  oversample: int = 8, power_iters: int = 1, seed: int = 0):
    """Smallest rank in the geometric ladder r0, r0*growth, ... <= r_max whose range
    finder meets relative residual `tol` (certified bound / ||A||_F-based normaliser).

    Returns (rank, Q, rel_bound, rel_max). If no rank <= r_max meets tol, returns the
    last tried rank with its (failing) residuals; the caller decides.
    """
    xp = _xp(A)
    normA = _norm(xp, A)                 # Frobenius; relative to it the bound is conservative
    if normA == 0.0:
        Q = range_finder(A, 1, 0, 0, seed)
        return 1, Q, 0.0, 0.0
    r = max(1, min(r0, r_max))
    while True:
        Q = range_finder(A, r, oversample, power_iters, seed)
        bound, mx = residual_estimate(A, Q, seed=seed + 1)
        rel_bound, rel_max = bound / normA, mx / normA
        if rel_bound <= tol or r >= r_max:
            return r, Q, rel_bound, rel_max
        r = min(r_max, int(math.ceil(r * growth)))
