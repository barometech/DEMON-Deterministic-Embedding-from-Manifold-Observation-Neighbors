"""gate.py: stratified admission (ADMIT / REFUSE_FULL_RANK / REFUSE_NO_SPEEDUP) and its certificate."""
from __future__ import annotations

import numpy as np
import pytest
import torch

from demon_accel.lowrank import LowRankOperator
from demon_accel import Admission, LowRankOperator, Status, admit

OVERSAMPLE = 8      # probed basis width == ladder rank + OVERSAMPLE; the SERVED rank is the smallest
                    # truncation r' whose certificate sqrt(bound^2 + s_{r'+1}^2)/||A||_F meets tol


# ----------------------------------------------------------------------------- ADMIT
def test_exact_rank8_1024_is_admitted(helpers):
    n, r = 1024, 8
    A = helpers.make_lowrank(n, n, r, seed=1)
    adm = admit(A, tol=1e-6)
    assert isinstance(adm, Admission)
    assert adm.status is Status.ADMIT and adm.admitted
    assert adm.rank >= r
    assert adm.rank == 8                                   # exact numerical rank is served
    assert adm.rel_err_bound <= 1e-6
    assert adm.rel_err_est <= adm.rel_err_bound
    assert isinstance(adm.operator, LowRankOperator)
    assert adm.operator.shape == (n, n) and adm.operator.rank == adm.rank
    # cost model: default n_cols == k, served cost == operator.flops_matmul
    assert adm.flops_full == LowRankOperator.flops_dense(n, n, n) == 2 * n * n * n
    assert adm.flops_served == adm.operator.flops_matmul(n)
    assert adm.speedup_flops == adm.flops_full / adm.flops_served >= 2.0
    # the served operator reproduces A
    assert np.linalg.norm(adm.operator.dense() - A) / np.linalg.norm(A) <= 1e-12


@pytest.mark.parametrize("n_cols", [1, 7, 256, 1024])
def test_flops_served_matches_lowrank_operator_formula(helpers, n_cols):
    m, k = 512, 384
    A = helpers.make_lowrank(m, k, 8, seed=2)
    adm = admit(A, tol=1e-6, n_cols=n_cols)
    assert adm.admitted
    op = adm.operator
    assert adm.flops_served == op.flops_matmul(n_cols) == 2 * adm.rank * (k * n_cols + m * n_cols) + adm.rank * n_cols
    assert adm.flops_full == LowRankOperator.flops_dense(m, k, n_cols) == 2 * m * k * n_cols


def test_flops_accounting_also_reported_when_refused(helpers):
    A = helpers.make_gaussian(128, 128, seed=3)
    adm = admit(A, tol=1e-6, n_cols=16)
    assert not adm.admitted and adm.operator is None
    assert adm.flops_full == 2 * 128 * 128 * 16
    assert adm.flops_served == 2 * adm.rank * (128 * 16 + 128 * 16) + adm.rank * 16
    assert adm.speedup_flops == adm.flops_full / adm.flops_served


# ----------------------------------------------------------------------------- REFUSE
def test_dense_gaussian_refused_full_rank(helpers):
    n = 512
    A = helpers.make_gaussian(n, n, seed=4)
    adm = admit(A, tol=1e-6)
    assert adm.status is Status.REFUSE_FULL_RANK
    assert not adm.admitted
    assert adm.operator is None
    r_adm = LowRankOperator.max_admissible_rank(n, n, n, 2.0)
    assert adm.rank == r_adm + OVERSAMPLE     # climbed the whole ladder; rank = last probed basis width
    assert adm.rel_err_bound > 1e-6


def test_rank_above_r_max_is_refused(helpers):
    n = 256
    A = helpers.make_lowrank(n, n, n // 2, seed=5)
    adm = admit(A, tol=1e-6)
    assert adm.status in (Status.REFUSE_FULL_RANK, Status.REFUSE_NO_SPEEDUP)
    assert not adm.admitted and adm.operator is None
    assert adm.rank <= LowRankOperator.max_admissible_rank(n, n, n, 2.0) + OVERSAMPLE


def test_explicit_r_max_below_true_rank_is_refused(helpers):
    A = helpers.make_lowrank(256, 256, 32, seed=6)
    adm = admit(A, tol=1e-6, r_max=16)
    assert adm.status is Status.REFUSE_FULL_RANK
    assert adm.rank == 16 + OVERSAMPLE                       # 24 < 32: cannot capture the range


def test_refuse_no_speedup_reachable(helpers):
    A = helpers.make_lowrank(256, 256, 8, seed=7)
    adm = admit(A, tol=1e-6, speedup_min=1e9)
    assert adm.status is Status.REFUSE_NO_SPEEDUP
    assert not adm.admitted and adm.operator is None
    assert adm.rank == 8
    assert adm.rel_err_bound <= 1e-6          # tolerance was met; cost was the reason
    assert adm.flops_served * 1e9 > adm.flops_full
    # the same matrix is admitted at the default speedup_min
    assert admit(A, tol=1e-6).admitted


def test_speedup_min_boundary_uses_cost_model(helpers):
    A = helpers.make_lowrank(256, 256, 8, seed=8)
    adm = admit(A, tol=1e-6, n_cols=256)
    ratio = adm.flops_full / adm.flops_served
    assert admit(A, tol=1e-6, n_cols=256, speedup_min=ratio * 0.99).admitted
    assert admit(A, tol=1e-6, n_cols=256, speedup_min=ratio * 1.01).status is Status.REFUSE_NO_SPEEDUP


def test_tolerance_is_relative_to_scale(helpers):
    n = 256
    A = helpers.make_lowrank(n, n, 8, seed=9) + 1e-6 * helpers.make_gaussian(n, n, seed=90)
    a1, a2 = admit(A, tol=1e-2), admit((2.0 ** 20) * A, tol=1e-2)      # exact scaling in fp
    assert a1.admitted and a2.admitted and a1.rank == a2.rank == 8
    assert a1.rel_err_bound > 1e-8                                       # a non-roundoff residual
    assert a1.rel_err_bound == pytest.approx(a2.rel_err_bound, rel=1e-9)
    assert a1.rel_err_est == pytest.approx(a2.rel_err_est, rel=1e-9)


# ----------------------------------------------------------------------------- certificate
def test_served_error_within_reported_bound(helpers):
    """A = rank-8 + small full-rank tail, tol = 1e-2 -> ADMIT at rank 8. The bound certifies
    ||A - A_r||_2 / ||A||_F; the served product error normalised the same way must respect it."""
    n = 512
    A = helpers.make_lowrank(n, n, 8, seed=10) + 4e-7 * np.random.default_rng(11).standard_normal((n, n))
    B = np.random.default_rng(12).standard_normal((n, 64))
    adm = admit(A, tol=1e-2, n_cols=64)
    assert adm.status is Status.ADMIT and adm.rank == 8
    assert 0.0 < adm.rel_err_bound <= 1e-2
    normA_F = np.linalg.norm(A)
    op_err = np.linalg.norm(A - adm.operator.dense(), 2) / normA_F
    assert op_err <= adm.rel_err_bound
    C_exact, C_served = A @ B, adm.operator.matmul(B)
    err_fro_rel = np.linalg.norm(C_served - C_exact, 2) / (normA_F * np.linalg.norm(B, 2))
    assert err_fro_rel <= adm.rel_err_bound
    # the bound is also informative, not vacuous: within two orders of magnitude of the truth
    assert adm.rel_err_bound <= 1e3 * op_err
    # and the non-certified estimate is below the bound
    assert adm.rel_err_est <= adm.rel_err_bound


@pytest.mark.parametrize("r_true", [4, 12, 16, 20, 24, 40])
def test_served_error_within_bound_for_rank_between_ladder_steps(helpers, r_true):
    """Regression for the certificate/truncation mismatch: the gate must serve the whole certified
    basis, so an exactly rank-r matrix whose rank falls strictly inside (rung, rung+8] is served exactly."""
    n = 256
    A = helpers.make_lowrank(n, n, r_true, seed=13 + r_true)
    B = np.random.default_rng(14).standard_normal((n, 32))
    adm = admit(A, tol=1e-6, n_cols=32)
    assert adm.admitted
    assert adm.rank >= r_true and adm.operator.rank == adm.rank
    err = np.linalg.norm(adm.operator.matmul(B) - A @ B, 2) / (np.linalg.norm(A) * np.linalg.norm(B, 2))
    assert err <= adm.rel_err_bound, f"served err {err:.3g} vs certified bound {adm.rel_err_bound:.3g}"
    assert err <= 1e-12
    assert adm.flops_served == adm.operator.flops_matmul(32)   # cost model uses the served rank


# ----------------------------------------------------------------------------- edge cases
@pytest.mark.parametrize("backend", ["numpy", "torch"])
def test_zero_matrix_does_not_crash(backend):
    Z = np.zeros((128, 96))
    if backend == "torch":
        Z = torch.from_numpy(Z)
    adm = admit(Z, tol=1e-6)
    assert isinstance(adm, Admission)
    assert np.isfinite(adm.rel_err_bound) and np.isfinite(adm.rel_err_est)
    assert adm.rel_err_bound == 0.0
    if adm.admitted:
        D = adm.operator.dense()
        D = D.numpy() if backend == "torch" else D
        assert np.all(np.isfinite(D)) and np.all(D == 0.0)
        B = np.ones((96, 5))
        B = torch.from_numpy(B) if backend == "torch" else B
        C = adm.operator.matmul(B)
        C = C.numpy() if backend == "torch" else C
        assert C.shape == (128, 5) and np.all(C == 0.0)


def test_admit_torch_rank8(helpers):
    n = 512
    A = torch.from_numpy(helpers.make_lowrank(n, n, 8, seed=15))
    adm = admit(A, tol=1e-6)
    assert adm.admitted and adm.rank >= 8
    assert isinstance(adm.operator.U, torch.Tensor)
    B = torch.randn(n, 32, dtype=torch.float64, generator=torch.Generator().manual_seed(1))
    assert float(torch.linalg.norm(adm.operator.matmul(B) - A @ B) / torch.linalg.norm(A @ B)) <= 1e-10


def test_admit_torch_gaussian_refused(helpers):
    A = torch.from_numpy(helpers.make_gaussian(256, 256, seed=16))
    adm = admit(A, tol=1e-6)
    assert adm.status is Status.REFUSE_FULL_RANK and adm.operator is None


def test_lowrank_operator_helpers(helpers):
    A = helpers.make_lowrank(128, 96, 8, seed=17)
    adm = admit(A, tol=1e-6, n_cols=10)
    op = adm.operator
    X = np.random.default_rng(18).standard_normal((5, 128))
    assert np.allclose(op.rmatmul(X), X @ A, atol=1e-12)
    B = np.random.default_rng(19).standard_normal((96, 10))
    assert np.allclose(op @ B, A @ B, atol=1e-12)
    assert np.allclose(op.dense(), A, atol=1e-12)
    assert op.shape == (128, 96)


def test_status_is_str_enum():
    assert Status.ADMIT == "ADMIT" and Status.REFUSE_FULL_RANK.value == "REFUSE_FULL_RANK"
    assert Status.REFUSE_NO_SPEEDUP.value == "REFUSE_NO_SPEEDUP"


def test_r_max_below_true_rank_is_refused(helpers):
    """r_max is the largest SERVED rank. A rank-8 operator needs r'=8 > r_max=3 to meet tol,
    so it is refused (NO_SPEEDUP: the tolerance is met, but not within the rank budget)."""
    A = helpers.make_lowrank(256, 256, 8, seed=19)
    adm = admit(A, tol=1e-6, r_max=3)
    assert adm.status is Status.REFUSE_NO_SPEEDUP and adm.operator is None
    assert adm.rank == 8 and adm.rel_err_bound <= 1e-6


def test_top_rung_is_refused_for_no_speedup_on_square_shapes(helpers):
    """Exact rank n/4 meets the tolerance, but serving rank n/4 costs 2r(2n)n + rn > n^3,
    i.e. less than 2x FLOP gain (the +rn term): refused as NO_SPEEDUP, not as FULL_RANK."""
    n = 256
    A = helpers.make_lowrank(n, n, n // 4, seed=20)
    adm = admit(A, tol=1e-6)
    assert adm.status is Status.REFUSE_NO_SPEEDUP
    assert adm.rank == n // 4 and adm.rel_err_bound <= 1e-6
    assert adm.rank > LowRankOperator.max_admissible_rank(n, n, n, 2.0)
