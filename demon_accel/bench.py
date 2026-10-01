"""Benchmarks: wall-clock and FLOPs of served vs backend GEMM on synthetic strata.

Run: python -m demon_accel.bench [--n 2048] [--reps 5]
Every number in the README tables comes from this script.
"""
from __future__ import annotations
import argparse, time, sys
import numpy as np
from .gate import admit, Status
from .anchors import AnchorIterator


def make_lowrank(n, r, decay=None, rng=None, dtype=np.float64):
    rng = rng or np.random.default_rng(0)
    U = np.linalg.qr(rng.standard_normal((n, r)))[0]
    V = np.linalg.qr(rng.standard_normal((n, r)))[0]
    s = np.linspace(1.0, 0.5, r) if decay is None else decay
    return (U * s) @ V.T


def timeit(fn, reps):
    fn()
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return float(np.median(ts))


def bench_operator(n, r, n_cols, reps, tol, rng):
    A = make_lowrank(n, r, rng=rng)
    B = rng.standard_normal((n, n_cols))
    t_probe = time.perf_counter(); adm = admit(A, tol=tol, n_cols=n_cols); t_probe = time.perf_counter() - t_probe
    t_dense = timeit(lambda: A @ B, reps)
    row = dict(kind="lowrank", n=n, r_true=r, n_cols=n_cols, status=adm.status.value, rank=adm.rank,
               t_dense=t_dense, t_probe=t_probe, flops_speedup=adm.speedup_flops, rel_err_bound=adm.rel_err_bound)
    if adm.admitted:
        C_ref = A @ B
        t_served = timeit(lambda: adm.operator.matmul(B), reps)
        err = np.linalg.norm(adm.operator.matmul(B) - C_ref) / np.linalg.norm(C_ref)
        row.update(t_served=t_served, wall_speedup=t_dense / t_served, rel_err_actual=err,
                   breakeven_calls=t_probe / max(t_dense - t_served, 1e-12))
    return row


def bench_fullrank(n, n_cols, reps, tol, rng):
    A = rng.standard_normal((n, n)) / np.sqrt(n)
    B = rng.standard_normal((n, n_cols))
    t_probe = time.perf_counter(); adm = admit(A, tol=tol, n_cols=n_cols); t_probe = time.perf_counter() - t_probe
    t_dense = timeit(lambda: A @ B, reps)
    return dict(kind="fullrank", n=n, r_true=n, n_cols=n_cols, status=adm.status.value, rank=adm.rank,
                t_dense=t_dense, t_probe=t_probe, flops_speedup=adm.speedup_flops, rel_err_bound=adm.rel_err_bound)


def bench_anchors(n, r, K, rng):
    A = make_lowrank(n, r, rng=rng)
    y0 = rng.standard_normal(n)
    ys = []; y = y0
    t = time.perf_counter()
    for _ in range(K):
        y = A @ y; ys.append(y)
    t_dense = time.perf_counter() - t
    it = AnchorIterator(A, rank=r, tol=1e-9)
    t = time.perf_counter(); out = it.run(y0, K); t_served = time.perf_counter() - t
    err = max(np.linalg.norm(a - b) / max(np.linalg.norm(b), 1e-300) for a, b in zip(out, ys))
    return dict(kind="anchors", n=n, r_true=r, K=K, refused=it.refused, max_rel_err=err,
                flops_speedup=(2 * n * n * K) / it.flops, wall_speedup=t_dense / t_served)


def fmt(row):
    return "  ".join(f"{k}={v:.3g}" if isinstance(v, float) else f"{k}={v}" for k, v in row.items())


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=2048)
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--tol", type=float, default=1e-6)
    a = p.parse_args(argv)
    rng = np.random.default_rng(0)
    rows = []
    for r in (8, 32, 128):
        rows.append(bench_operator(a.n, r, a.n, a.reps, a.tol, rng))
    rows.append(bench_fullrank(a.n, a.n, a.reps, a.tol, rng))
    rows.append(bench_anchors(min(a.n, 1024), 8, 500, rng))
    for row in rows:
        print(fmt(row))
    return rows


if __name__ == "__main__":
    main()
