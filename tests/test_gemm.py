"""gemm.py: matmul(A, B, tol) -> (C, Admission); served when admitted, backend GEMM otherwise."""
from __future__ import annotations

import numpy as np
import pytest
import torch

import demon_accel.gemm as gemm_mod
from demon_accel import Status, admit, matmul


def _rng(seed):
    return np.random.default_rng(seed)


# ----------------------------------------------------------------------------- refused path
def test_refused_returns_exact_backend_product(helpers):
    A = helpers.make_gaussian(256, 256, seed=1)
    B = _rng(2).standard_normal((256, 32))
    C, adm = matmul(A, B, tol=1e-6)
    assert adm.status is Status.REFUSE_FULL_RANK and not adm.admitted
    assert C.shape == (256, 32)
    assert np.array_equal(C, A @ B)              # bitwise: same backend GEMM


def test_refused_no_speedup_also_exact(helpers):
    A = helpers.make_lowrank(256, 256, 8, seed=3)
    B = _rng(4).standard_normal((256, 32))
    C, adm = matmul(A, B, tol=1e-6, speedup_min=1e9)   # gate_kw are forwarded
    assert adm.status is Status.REFUSE_NO_SPEEDUP
    assert np.array_equal(C, A @ B)


# ----------------------------------------------------------------------------- served path
def test_admitted_returns_served_product_close_to_exact(helpers):
    A = helpers.make_lowrank(512, 384, 8, seed=5)
    B = _rng(6).standard_normal((384, 64))
    C, adm = matmul(A, B, tol=1e-6)
    assert adm.admitted and adm.rank >= 8
    assert C.shape == (512, 64)
    assert np.linalg.norm(C - A @ B) / np.linalg.norm(A @ B) <= 1e-12


def test_matmul_torch(helpers):
    A = torch.from_numpy(helpers.make_lowrank(256, 256, 8, seed=7))
    B = torch.randn(256, 16, dtype=torch.float64, generator=torch.Generator().manual_seed(8))
    C, adm = matmul(A, B, tol=1e-6)
    assert adm.admitted and isinstance(C, torch.Tensor)
    assert float(torch.linalg.norm(C - A @ B) / torch.linalg.norm(A @ B)) <= 1e-12
    G = torch.from_numpy(helpers.make_gaussian(256, 256, seed=9))
    C2, adm2 = matmul(G, B, tol=1e-6)
    assert not adm2.admitted and torch.equal(C2, G @ B)


# ----------------------------------------------------------------------------- admission reuse
def test_passed_admission_is_reused_without_reprobe(helpers, monkeypatch):
    A = helpers.make_lowrank(256, 256, 8, seed=10)
    B = _rng(11).standard_normal((256, 32))
    adm = admit(A, tol=1e-6, n_cols=32)
    assert adm.admitted

    def boom(*a, **k):
        raise AssertionError("admit() must not be called when an admission is passed")

    monkeypatch.setattr(gemm_mod, "admit", boom)
    C, adm_out = matmul(A, B, admission=adm)
    assert adm_out is adm
    assert np.linalg.norm(C - A @ B) / np.linalg.norm(A @ B) <= 1e-12
    # sanity: without the admission the patched admit *is* reached
    with pytest.raises(AssertionError, match="must not be called"):
        matmul(A, B)


def test_passed_refused_admission_is_honoured(helpers, monkeypatch):
    A = helpers.make_gaussian(128, 128, seed=12)
    B = _rng(13).standard_normal((128, 8))
    adm = admit(A, tol=1e-6)
    assert not adm.admitted
    monkeypatch.setattr(gemm_mod, "admit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reprobe")))
    C, adm_out = matmul(A, B, admission=adm)
    assert adm_out is adm and np.array_equal(C, A @ B)


def test_matmul_passes_n_cols_and_gate_kwargs_to_admit(helpers, monkeypatch):
    A = helpers.make_lowrank(128, 128, 8, seed=14)
    B = _rng(15).standard_normal((128, 23))
    calls = []
    real_admit = gemm_mod.admit

    def spy(A_, **kw):
        calls.append(kw)
        return real_admit(A_, **kw)

    monkeypatch.setattr(gemm_mod, "admit", spy)
    C, adm = matmul(A, B, tol=1e-4, r_max=16, seed=3)
    assert len(calls) == 1
    assert calls[0]["n_cols"] == 23 and calls[0]["tol"] == 1e-4
    assert calls[0]["r_max"] == 16 and calls[0]["seed"] == 3
    assert adm.flops_full == 2 * 128 * 128 * 23


# ----------------------------------------------------------------------------- shapes of B
def test_b_with_one_column(helpers):
    A = helpers.make_lowrank(256, 192, 8, seed=16)
    B = _rng(17).standard_normal((192, 1))
    C, adm = matmul(A, B, tol=1e-6)
    assert adm.admitted
    assert adm.flops_full == 2 * 256 * 192 * 1
    assert C.shape == (256, 1)
    assert np.linalg.norm(C - A @ B) / np.linalg.norm(A @ B) <= 1e-12


def test_one_dimensional_b_on_refused_operator_is_exact(helpers):
    A = helpers.make_gaussian(128, 128, seed=18)
    b = _rng(19).standard_normal(128)
    C, adm = matmul(A, b, tol=1e-6)
    assert not adm.admitted
    assert adm.flops_full == 2 * 128 * 128 * 1        # 1-D B is costed as n_cols == 1
    assert C.shape == (128,) and np.array_equal(C, A @ b)


def test_one_dimensional_b_on_admitted_operator(helpers):
    A = helpers.make_lowrank(256, 256, 8, seed=20)
    b = _rng(21).standard_normal(256)
    C, adm = matmul(A, b, tol=1e-6)
    assert adm.admitted
    assert C.shape == (256,), f"got shape {C.shape}"
    assert np.linalg.norm(C - A @ b) / np.linalg.norm(A @ b) <= 1e-12

