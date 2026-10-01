"""Shared helpers for the demon_accel test-suite.

Everything is deterministic: every random object is built from an explicit seed.
"""
from __future__ import annotations

import numpy as np
import pytest
import torch


# --------------------------------------------------------------------------- matrices
def make_lowrank(m: int, k: int, r: int, seed: int = 0, s=None, dtype=np.float64):
    """Exactly rank-r (m x k) matrix U diag(s) V^T with orthonormal U, V.
    Default singular values linspace(1, 0.5, r) (bench.make_lowrank convention)."""
    rng = np.random.default_rng(seed)
    U = np.linalg.qr(rng.standard_normal((m, r)))[0]
    V = np.linalg.qr(rng.standard_normal((k, r)))[0]
    s = np.linspace(1.0, 0.5, r) if s is None else np.asarray(s, dtype=float)
    return ((U * s) @ V.T).astype(dtype)


def make_gaussian(m: int, k: int, seed: int = 0, dtype=np.float64):
    """Dense full-rank Gaussian, scaled so the spectral norm is O(1)."""
    rng = np.random.default_rng(seed)
    return (rng.standard_normal((m, k)) / np.sqrt(k)).astype(dtype)


def make_symmetric(n: int, eigenvalues, seed: int = 0):
    """Symmetric n x n matrix U diag(eigenvalues) U^T with orthonormal U (n x len(eig)).
    rank == len(eigenvalues); spectral radius == max |eigenvalues|."""
    rng = np.random.default_rng(seed)
    lam = np.asarray(eigenvalues, dtype=float)
    U = np.linalg.qr(rng.standard_normal((n, lam.size)))[0]
    return (U * lam) @ U.T


def ladder_step_at_or_above(r: int, r0: int = 8, growth: float = 2.0, r_max: int = 10**9):
    """Smallest rank in the geometric ladder r0, r0*growth, ... (capped at r_max) >= r."""
    x = r0
    while x < r and x < r_max:
        x = min(r_max, int(np.ceil(x * growth)))
    return x


def spectral_norm(M) -> float:
    M = M.numpy() if isinstance(M, torch.Tensor) else np.asarray(M)
    return float(np.linalg.norm(M, 2))


def fro(M) -> float:
    M = M.numpy() if isinstance(M, torch.Tensor) else np.asarray(M)
    return float(np.linalg.norm(M))


def exact_iteration(A, y0, n_steps: int):
    """[A y0, A^2 y0, ..., A^n_steps y0] with the backend GEMM."""
    out = []
    y = y0
    for _ in range(n_steps):
        y = A @ y
        out.append(y)
    return out


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _torch_determinism():
    torch.manual_seed(0)
    torch.set_num_threads(min(4, torch.get_num_threads()))
    yield


@pytest.fixture
def helpers():
    """Namespace object exposing the helper functions above."""
    class H:
        pass
    for fn in (make_lowrank, make_gaussian, make_symmetric, ladder_step_at_or_above,
               spectral_norm, fro, exact_iteration):
        setattr(H, fn.__name__, staticmethod(fn))
    return H
