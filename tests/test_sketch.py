"""sketch.py: randomized range finder, residual certificate, adaptive rank ladder."""
from __future__ import annotations

import math

import numpy as np
import pytest
import torch

from demon_accel.sketch import adaptive_rank, range_finder, residual_estimate, sketch_svd

N = 256
R_MAX = N // 4           # the gate's default r_max for a square N x N matrix
OVERSAMPLE = 8


# ----------------------------------------------------------------------------- sketch_svd
@pytest.mark.parametrize("r", [1, 4, 8, 16])
def test_sketch_svd_recovers_exact_low_rank_numpy(helpers, r):
    A = helpers.make_lowrank(N, N, r, seed=10 + r)
    U, S, Vt = sketch_svd(A, r, seed=0)
    assert U.shape == (N, r) and S.shape == (r,) and Vt.shape == (r, N)
    rel = np.linalg.norm((U * S) @ Vt - A) / np.linalg.norm(A)
    assert rel <= 1e-12
    # factors are orthonormal and singular values are the true ones (descending)
    assert np.allclose(U.T @ U, np.eye(r), atol=1e-12)
    assert np.allclose(Vt @ Vt.T, np.eye(r), atol=1e-12)
    s_true = np.linalg.svd(A, compute_uv=False)[:r]
    assert np.allclose(S, s_true, rtol=1e-10, atol=1e-13)
    assert np.all(np.diff(S) <= 1e-13)


def test_sketch_svd_rectangular_shapes(helpers):
    A = helpers.make_lowrank(300, 180, 6, seed=3)
    U, S, Vt = sketch_svd(A, 6)
    assert U.shape == (300, 6) and Vt.shape == (6, 180)
    assert np.linalg.norm((U * S) @ Vt - A) / np.linalg.norm(A) <= 1e-12


def test_sketch_svd_exact_rank_torch_matches_numpy(helpers):
    r = 8
    A = helpers.make_lowrank(N, N, r, seed=5)
    At = torch.from_numpy(A.copy())
    U, S, Vt = sketch_svd(At, r, seed=0)
    assert all(isinstance(t, torch.Tensor) for t in (U, S, Vt))
    assert U.dtype == torch.float64
    rel = torch.linalg.norm((U * S) @ Vt - At) / torch.linalg.norm(At)
    assert float(rel) <= 1e-12
    # singular values are RNG-independent for an exactly low-rank input
    _, S_np, _ = sketch_svd(A, r, seed=0)
    assert np.allclose(S.numpy(), S_np, rtol=1e-10, atol=1e-13)


def test_sketch_svd_float32_keeps_dtype(helpers):
    A = helpers.make_lowrank(128, 128, 4, seed=1, dtype=np.float32)
    U, S, Vt = sketch_svd(A, 4)
    assert U.dtype == np.float32 and S.dtype == np.float32 and Vt.dtype == np.float32
    assert np.linalg.norm((U * S) @ Vt - A) / np.linalg.norm(A) <= 1e-5
    Ut, St, Vtt = sketch_svd(torch.from_numpy(A.copy()), 4)
    assert Ut.dtype == torch.float32


# ----------------------------------------------------------------------------- range_finder
@pytest.mark.parametrize("backend", ["numpy", "torch"])
def test_range_finder_is_orthonormal_and_sized(helpers, backend):
    A = helpers.make_gaussian(200, 150, seed=2)
    if backend == "torch":
        A = torch.from_numpy(A.copy())
    Q = range_finder(A, rank=10, oversample=8, seed=4)
    assert tuple(Q.shape) == (200, 18)
    G = (Q.T @ Q)
    G = G.numpy() if backend == "torch" else G
    assert np.allclose(G, np.eye(18), atol=1e-12)
    # l is capped at min(m, k)
    Q2 = range_finder(A, rank=140, oversample=20, seed=4)
    assert tuple(Q2.shape) == (200, 150)


def test_range_finder_is_deterministic_in_seed(helpers):
    A = helpers.make_gaussian(128, 128, seed=7)
    Q1 = range_finder(A, 8, seed=11)
    Q2 = range_finder(A, 8, seed=11)
    Q3 = range_finder(A, 8, seed=12)
    assert np.array_equal(Q1, Q2)
    assert not np.allclose(Q1, Q3)


def test_range_finder_captures_exact_range(helpers):
    r = 8
    A = helpers.make_lowrank(N, N, r, seed=8)
    Q = range_finder(A, r, seed=0)
    assert np.linalg.norm(A - Q @ (Q.T @ A)) / np.linalg.norm(A) <= 1e-12


# ----------------------------------------------------------------------------- residual_estimate
def _spectral_residual(A, Q):
    R = A - Q @ (Q.T @ A)
    R = R.numpy() if isinstance(R, torch.Tensor) else R
    return float(np.linalg.norm(R, 2))


@pytest.mark.parametrize("kind", ["gaussian", "decay", "lowrank_plus_noise"])
def test_residual_bound_dominates_true_spectral_residual_20_seeds(helpers, kind):
    """Halko-Martinsson-Tropp Lemma 4.1: 10*sqrt(2/pi)*max_i ||(I-QQ^T) A w_i|| >= ||(I-QQ^T)A||_2
    with failure probability <= 10^-n_probes (= 1e-10). Must hold on every seed."""
    if kind == "gaussian":
        A = helpers.make_gaussian(N, N, seed=100)
    elif kind == "decay":
        A = helpers.make_lowrank(N, N, 64, seed=101, s=0.85 ** np.arange(64))
    else:
        A = helpers.make_lowrank(N, N, 8, seed=102) + 1e-3 * helpers.make_gaussian(N, N, seed=103)
    const = 10.0 * math.sqrt(2.0 / math.pi)
    for seed in range(20):
        Q = range_finder(A, 8, seed=seed)
        bound, mx = residual_estimate(A, Q, n_probes=10, seed=1000 + seed)
        true = _spectral_residual(A, Q)
        assert bound >= true, (kind, seed, bound, true)
        assert bound == pytest.approx(const * mx)
        assert 0.0 <= mx <= bound
        # the probes also cannot exceed the Frobenius residual (||R w|| <= ||R||_F ||w||)
        assert mx <= np.linalg.norm(A - Q @ (Q.T @ A)) * math.sqrt(A.shape[1]) * 2.0


def test_residual_estimate_is_zero_for_captured_range(helpers):
    A = helpers.make_lowrank(N, N, 8, seed=9)
    Q = range_finder(A, 8, seed=0)
    bound, mx = residual_estimate(A, Q)
    assert bound <= 1e-12 * np.linalg.norm(A)


def test_residual_estimate_torch_agrees_with_numpy(helpers):
    A = helpers.make_lowrank(N, N, 64, seed=104, s=0.85 ** np.arange(64))
    Q = range_finder(A, 8, seed=3)
    b_np, m_np = residual_estimate(A, Q, seed=5)
    b_t, m_t = residual_estimate(torch.from_numpy(A.copy()), torch.from_numpy(Q.copy()), seed=5)
    assert isinstance(b_t, float) and isinstance(m_t, float)
    true = _spectral_residual(A, Q)
    assert b_np >= true and b_t >= true
    # different RNG streams -> different probes, but the same quantity is estimated
    assert 0.2 <= b_t / b_np <= 5.0
    assert b_t == pytest.approx(10.0 * math.sqrt(2.0 / math.pi) * m_t)


# ----------------------------------------------------------------------------- adaptive_rank
@pytest.mark.parametrize("r", [1, 4, 8, 12, 16, 20, 24, 32, 40, 64])
def test_adaptive_rank_picks_rank_between_true_rank_and_next_ladder_step(helpers, r):
    """The certified object is the basis Q (ladder rank + oversample columns). Its dimension must
    cover the true rank and must not exceed the first ladder rung >= r plus the oversample; the
    returned ladder rank is on the ladder and never above that rung."""
    A = helpers.make_lowrank(N, N, r, seed=200 + r)
    rank, Q, rel_bound, rel_max = adaptive_rank(A, tol=1e-6, r_max=R_MAX, seed=0)
    assert rel_bound <= 1e-6
    assert rel_max <= rel_bound
    rung = helpers.ladder_step_at_or_above(r, r_max=R_MAX)
    assert rank <= rung and rank in {8, 16, 32, 64}
    assert Q.shape[1] == rank + OVERSAMPLE
    assert r <= Q.shape[1] <= rung + OVERSAMPLE, f"true rank {r}, certified basis has {Q.shape[1]} columns"
    # the smallest rung whose basis covers r is the one that is picked (no overshoot)
    assert rank == min(x for x in (8, 16, 32, 64) if x + OVERSAMPLE >= r)
    # and the basis really captures the range
    assert np.linalg.norm(A - Q @ (Q.T @ A)) / np.linalg.norm(A) <= 1e-12


@pytest.mark.parametrize("r", [4, 8, 32])
def test_adaptive_rank_returned_basis_captures_range(helpers, r):
    A = helpers.make_lowrank(N, N, r, seed=300 + r)
    rank, Q, rel_bound, _ = adaptive_rank(A, tol=1e-6, r_max=R_MAX)
    assert Q.shape[0] == N and Q.shape[1] >= rank
    assert np.linalg.norm(A - Q @ (Q.T @ A)) / np.linalg.norm(A) <= 1e-12


def test_adaptive_rank_full_rank_stops_at_r_max_with_failing_bound(helpers):
    A = helpers.make_gaussian(N, N, seed=400)
    rank, Q, rel_bound, rel_max = adaptive_rank(A, tol=1e-6, r_max=R_MAX)
    assert rank == R_MAX
    assert rel_bound > 1e-6 and rel_max > 1e-6
    assert rel_bound >= rel_max


def test_adaptive_rank_never_exceeds_r_max(helpers):
    A = helpers.make_lowrank(N, N, 8, seed=401)
    rank, Q, rel_bound, _ = adaptive_rank(A, tol=1e-6, r_max=3)
    assert rank == 3 and Q.shape[1] == 3 + OVERSAMPLE     # ladder capped; basis still covers rank 8
    assert rel_bound <= 1e-12
    rank2, _, rel_bound2, _ = adaptive_rank(A, tol=1e-6, r_max=100, r0=5, growth=3.0)
    assert rank2 in (5, 15)              # custom ladder 5, 15, 45, 100
    assert rel_bound2 <= 1e-6


def test_adaptive_rank_zero_matrix():
    A = np.zeros((64, 64))
    rank, Q, rel_bound, rel_max = adaptive_rank(A, tol=1e-6, r_max=16)
    assert rank == 1 and rel_bound == 0.0 and rel_max == 0.0
    assert Q.shape[0] == 64 and np.all(np.isfinite(Q))


def test_adaptive_rank_torch_matches_numpy(helpers):
    A = helpers.make_lowrank(N, N, 8, seed=500)
    rank_np, Q_np, b_np, m_np = adaptive_rank(A, tol=1e-6, r_max=R_MAX, seed=0)
    rank_t, Q_t, b_t, m_t = adaptive_rank(torch.from_numpy(A.copy()), tol=1e-6, r_max=R_MAX, seed=0)
    assert rank_t == rank_np == 8
    assert isinstance(Q_t, torch.Tensor) and tuple(Q_t.shape) == Q_np.shape
    assert b_np <= 1e-12 and b_t <= 1e-12
    # same subspace up to the RNG stream: both capture the range of A
    At = torch.from_numpy(A.copy())
    assert float(torch.linalg.norm(At - Q_t @ (Q_t.T @ At)) / torch.linalg.norm(At)) <= 1e-12


def test_adaptive_rank_torch_full_rank_refuses_like_numpy(helpers):
    A = helpers.make_gaussian(128, 128, seed=501)
    rank_np, _, b_np, _ = adaptive_rank(A, tol=1e-6, r_max=32)
    rank_t, _, b_t, _ = adaptive_rank(torch.from_numpy(A.copy()), tol=1e-6, r_max=32)
    assert rank_np == rank_t == 32
    assert b_np > 1e-6 and b_t > 1e-6
    assert 0.2 <= b_t / b_np <= 5.0
