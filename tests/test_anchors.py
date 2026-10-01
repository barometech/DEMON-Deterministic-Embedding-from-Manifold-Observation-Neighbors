"""anchors.py: trajectory-stratum accelerator y_{k+1} = A y_k with drift certificate.

Check schedule (served-step counter s): s = 1, 2, 4, 8, ... while s < check_every, then every
check_every. At a check the served iterate is replaced by the exact one; on failure the iterator
refuses, recomputes the unverified window exactly and continues with exact products.
"""
from __future__ import annotations

import numpy as np
import pytest
import torch

from demon_accel import AnchorIterator

N, R, K = 512, 8, 500
NA = R + 2
DENSE_FLOPS = 2 * N * N * K


# ----------------------------------------------------------------------------- reference models
def _check_steps(served: int, check_every: int):
    """Served-step indices at which the iterator checks (independent re-derivation of the schedule)."""
    s = set()
    p = 1
    while p < check_every and p <= served:
        s.add(p)
        p *= 2
    s.update(range(check_every, served + 1, check_every))
    return sorted(s)


def _served_flops(n, r, K, check_every, n_anchors=None):
    """Never-refused run: na exact anchors, fit, (K-na) served steps, one exact product per check."""
    na = r + 2 if n_anchors is None else n_anchors
    served = K - na
    return 2 * n * n * na + 2 * n * n * r + served * 4 * n * r + len(_check_steps(served, check_every)) * 2 * n * n


def _refused_at_first_check_flops(n, r, K):
    """na anchors + fit + 1 served step + its check + (re)computation of that iterate + exact rest."""
    na = r + 2
    return 2 * n * n * na + 2 * n * n * r + 4 * n * r + 2 * n * n + 2 * n * n + 2 * n * n * (K - na - 1)


def _spectral_radius_one_lowrank(helpers, seed=0):
    # Chebyshev-extrema eigenvalues cos(pi j/(R-1)), j=0..R-1: contain +1 and -1 (spectral radius
    # exactly 1, iterates never vanish), well separated (Krylov anchors well conditioned).
    lam = np.cos(np.pi * np.arange(R) / (R - 1))
    return helpers.make_symmetric(N, lam, seed=seed)


def _haar_orthogonal(n, seed):
    """Full-rank, every eigenvalue on the unit circle: iterates keep their norm, Krylov anchors are
    well conditioned, so a rank-8 fit is caught at the very first check."""
    return np.linalg.qr(np.random.default_rng(seed).standard_normal((n, n)))[0]


def _leaking_rotation(n=64, theta=2 * np.pi / 800, eps=4e-8, seed=0):
    """Slow rotation on span{e1, e2} that leaks eps * <e2, y> into a decaying direction e3.
    From y0 = e1 the leak grows like |sin(k theta)|, so the first drift checks pass and a later one
    fails: exercises the repair of the unverified window. Conjugated by a Haar rotation V."""
    A = np.zeros((n, n))
    A[0, 0] = A[1, 1] = np.cos(theta)
    A[1, 0], A[0, 1] = np.sin(theta), -np.sin(theta)
    A[2, 1], A[2, 2] = eps, 0.5
    V = np.linalg.qr(np.random.default_rng(seed).standard_normal((n, n)))[0]
    return V @ A @ V.T, V[:, 0].copy()


def _max_rel_err(out, exact):
    return max(np.linalg.norm(a - b) / np.linalg.norm(b) for a, b in zip(out, exact))


# ----------------------------------------------------------------------------- low-rank stratum
def test_exact_low_rank_never_refuses_and_tracks_exact_iteration(helpers):
    A = _spectral_radius_one_lowrank(helpers)
    assert np.linalg.matrix_rank(A) == R
    assert abs(np.linalg.norm(A, 2) - 1.0) <= 1e-12
    y0 = np.random.default_rng(2).standard_normal(N)
    exact = helpers.exact_iteration(A, y0, K)
    assert np.linalg.norm(exact[-1]) > 0.1          # iterates did not decay

    it = AnchorIterator(A, rank=R, tol=1e-9)
    out = it.run(y0, K)
    assert len(out) == K
    assert not it.refused
    assert it.steps == K - NA
    assert it.checks == len(_check_steps(K - NA, 50)) == 15
    assert _max_rel_err(out, exact) <= 1e-9
    assert 0.0 <= it.last_residual <= it.tol
    assert it.flops < DENSE_FLOPS
    assert it.flops == _served_flops(N, R, K, 50)


def test_low_rank_with_decaying_spectrum_also_served(helpers):
    A = helpers.make_symmetric(N, np.linspace(1.0, 0.5, R), seed=3)
    y0 = np.random.default_rng(4).standard_normal(N)
    exact = helpers.exact_iteration(A, y0, K)
    it = AnchorIterator(A, rank=R, tol=1e-9)
    out = it.run(y0, K)
    assert not it.refused
    assert _max_rel_err(out, exact) <= 1e-9


def test_rank_above_true_rank_still_exact(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=5)
    y0 = np.random.default_rng(6).standard_normal(N)
    exact = helpers.exact_iteration(A, y0, 200)
    it = AnchorIterator(A, rank=12, tol=1e-9)
    out = it.run(y0, 200)
    assert not it.refused
    assert _max_rel_err(out, exact) <= 1e-9


def test_first_anchors_are_exact_products(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=7)
    y0 = np.random.default_rng(8).standard_normal(N)
    it = AnchorIterator(A, rank=R)
    out = it.run(y0, NA)
    assert it.steps == 0 and it.checks == 0 and it.Q is not None and it.Q.shape == (N, R)
    assert np.allclose(it.Q.T @ it.Q, np.eye(R), atol=1e-12)
    for a, b in zip(out, helpers.exact_iteration(A, y0, NA)):
        assert np.array_equal(a, b)
    assert it.flops == 2 * N * N * NA + 2 * N * N * R


def test_second_run_continues_to_track(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=9)
    y0 = np.random.default_rng(10).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-9)
    out1 = it.run(y0, 100)
    out2 = it.run(out1[-1], 100)
    assert not it.refused
    exact = helpers.exact_iteration(A, y0, 200)
    assert _max_rel_err(out1 + out2, exact) <= 1e-9


def test_torch_backend(helpers):
    A = torch.from_numpy(_spectral_radius_one_lowrank(helpers, seed=9))
    y0 = torch.randn(N, dtype=torch.float64, generator=torch.Generator().manual_seed(10))
    exact = helpers.exact_iteration(A, y0, 200)
    it = AnchorIterator(A, rank=R, tol=1e-9)
    out = it.run(y0, 200)
    assert not it.refused and isinstance(out[-1], torch.Tensor)
    assert max(float(torch.linalg.norm(a - b) / torch.linalg.norm(b)) for a, b in zip(out, exact)) <= 1e-9
    assert it.flops == _served_flops(N, R, 200, 50)


# ----------------------------------------------------------------------------- full rank: refusal
@pytest.mark.parametrize("check_every", [1, 5, 50])
def test_full_rank_refuses_at_first_check_and_outputs_are_exact(check_every):
    A = _haar_orthogonal(N, seed=11)
    y0 = np.random.default_rng(12).standard_normal(N)
    exact = [y0]
    for _ in range(K):
        exact.append(A @ exact[-1])
    exact = exact[1:]
    it = AnchorIterator(A, rank=R, tol=1e-9, check_every=check_every)
    out = it.run(y0, K)
    assert it.refused
    assert it.steps == 1 and it.checks == 1              # refused at the very first check
    assert it.last_residual > it.tol
    assert len(out) == K
    errs = [np.linalg.norm(a - b) / np.linalg.norm(b) for a, b in zip(out, exact)]
    assert max(errs) <= 1e-12
    # every output is the exact product of its predecessor (backend GEMM path)
    for j in range(K - 1):
        assert np.array_equal(out[j + 1], A @ out[j])
    assert it.flops >= DENSE_FLOPS
    assert it.flops == _refused_at_first_check_flops(N, R, K)


def test_refusal_persists_across_runs():
    A = _haar_orthogonal(N, seed=16)
    y0 = np.random.default_rng(17).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-9)
    out1 = it.run(y0, 20)
    assert it.refused
    f = it.flops
    out2 = it.run(out1[-1], 30)
    assert it.refused and it.c is None
    assert it.flops - f == 2 * N * N * 30            # exact products only, no refit, no checks
    assert it.checks == 1
    z = out1[-1]
    for a in out2:
        z = A @ z
        assert np.array_equal(a, z)


def test_tight_tolerance_refuses_slightly_perturbed_low_rank(helpers):
    """A = low-rank + 1e-3 full-rank tail: the drift certificate must fire (tol 1e-9)."""
    A = _spectral_radius_one_lowrank(helpers, seed=24) + 1e-3 * _haar_orthogonal(N, seed=25)
    y0 = np.random.default_rng(26).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-9)
    out = it.run(y0, 100)
    assert it.refused and it.last_residual > 1e-9 and it.steps == 1
    exact = helpers.exact_iteration(A, y0, 100)
    assert _max_rel_err(out, exact) <= 1e-12


def test_loose_tolerance_accepts_the_same_perturbation(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=24) + 1e-3 * _haar_orthogonal(N, seed=25)
    y0 = np.random.default_rng(26).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-3)
    it.run(y0, 100)
    assert not it.refused and 0.0 < it.last_residual <= 1e-3


# ----------------------------------------------------------------------------- late refusal: window repair
def test_late_refusal_repairs_unverified_window():
    A, y0 = _leaking_rotation()
    n, rank, na, Kl = A.shape[0], 2, 4, 300
    it = AnchorIterator(A, rank=rank, tol=1e-9, check_every=50)
    out = it.run(y0, Kl)
    assert it.refused
    assert it.steps == 32 and it.checks == 6             # checks at 1, 2, 4, 8, 16 passed; 32 failed
    assert it.last_residual > it.tol
    last_verified = (na - 1) + 16                        # out index of the served step 16 (corrected there)
    refusal = (na - 1) + 32
    # the unverified window (17..31) and everything after are exact products of the predecessor
    for j in range(last_verified, Kl - 1):
        assert np.array_equal(out[j + 1], A @ out[j])
    # i.e. from the last verified iterate on, the output IS the exact trajectory
    z = out[last_verified]
    for j in range(last_verified + 1, Kl):
        z = A @ z
        assert np.array_equal(out[j], z)
    # every earlier returned iterate was certified (or exact) at a check: local residual <= tol there
    for s in (1, 2, 4, 8, 16):
        j = (na - 1) + s
        assert np.linalg.norm(out[j] - A @ out[j - 1]) / np.linalg.norm(A @ out[j - 1]) <= 1e-15
    # the step the refusal fired on is exact as well (repaired)
    assert np.array_equal(out[refusal], A @ out[refusal - 1])
    # flops: anchors + fit + 32 served + 6 checks + 15 repaired + 1 recomputed + exact remainder
    expected = (2 * n * n * na + 2 * n * n * rank + 32 * 4 * n * rank + 6 * 2 * n * n
                + 15 * 2 * n * n + 2 * n * n + 2 * n * n * (Kl - refusal - 1))
    assert it.flops == expected


def test_leaking_rotation_is_served_when_tolerance_allows():
    A, y0 = _leaking_rotation()
    it = AnchorIterator(A, rank=2, tol=1e-6, check_every=50)
    out = it.run(y0, 300)
    assert not it.refused
    exact = [y0]
    for _ in range(300):
        exact.append(A @ exact[-1])
    assert _max_rel_err(out, exact[1:]) <= 1e-6


# ----------------------------------------------------------------------------- check_every / anchors
@pytest.mark.parametrize("check_every", [1, 3, 10, 50, 100, 489, 490, 1000])
def test_check_every_is_respected(helpers, check_every):
    A = _spectral_radius_one_lowrank(helpers, seed=18)
    y0 = np.random.default_rng(19).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-9, check_every=check_every)
    it.run(y0, K)
    assert not it.refused
    served = K - NA
    assert it.steps == served
    assert it.checks == len(_check_steps(served, check_every))
    assert it.flops == _served_flops(N, R, K, check_every)
    assert 0.0 <= it.last_residual <= it.tol


def test_check_every_one_checks_every_served_step(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=20)
    y0 = np.random.default_rng(21).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-9, check_every=1)
    out = it.run(y0, 60)
    assert it.checks == it.steps == 60 - NA
    # every iterate was replaced by the exact product at its check
    for j in range(59):
        assert np.array_equal(out[j + 1], A @ out[j])


def test_check_schedule_counts_served_steps_not_total(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=20)
    y0 = np.random.default_rng(21).standard_normal(N)
    it = AnchorIterator(A, rank=R, tol=1e-9, check_every=7)
    it.run(y0, NA + 6)                 # served steps 1..6 -> checks at 1, 2, 4
    assert it.steps == 6 and it.checks == 3
    assert it.flops == 2 * N * N * NA + 2 * N * N * R + 6 * 4 * N * R + 3 * 2 * N * N
    it2 = AnchorIterator(A, rank=R, tol=1e-9, check_every=7)
    it2.run(y0, NA + 7)                # served step 7 is a check_every check
    assert it2.steps == 7 and it2.checks == 4
    assert it2.flops == it.flops + 4 * N * R + 2 * N * N


def test_n_anchors_override(helpers):
    A = _spectral_radius_one_lowrank(helpers, seed=22)
    y0 = np.random.default_rng(23).standard_normal(N)
    exact = helpers.exact_iteration(A, y0, 100)
    it = AnchorIterator(A, rank=R, tol=1e-9, n_anchors=R + 6)
    out = it.run(y0, 100)
    assert not it.refused and it.steps == 100 - (R + 6)
    assert it.flops == _served_flops(N, R, 100, 50, n_anchors=R + 6)
    assert _max_rel_err(out, exact) <= 1e-9
