# demon_accel — benchmark report

Every number below was produced in one session (2026-10-01) by the scripts listed in
[Commands](#commands), on commit **508d7a1** (HEAD). `git diff --stat 3d33572 508d7a1` touches only
`docs/DESIGN.md`, so the `demon_accel/` code measured here is byte-identical to **3d33572**
("certified truncation, exact r_max, 1-D path, idempotent wrap, tests"). Nothing is typed by hand.

Timing method everywhere: `time.perf_counter`, 1 warm-up rep, median of 7 reps (`BENCH_REPS=7`) unless a
column says otherwise; the built-in bench uses its own median of 7; the torch forward uses median of 20.
`OMP_NUM_THREADS=4` for every run. float64 unless stated. The scripts were run strictly one after another so
that no two timings shared the 4 cores. `demon_accel/*.py` was not modified.

## Machine

```
$ lscpu | head
Architecture:            x86_64
CPU(s):                  4   (On-line CPU(s) list: 0-3)
Vendor ID:               GenuineIntel
Model name:              Intel(R) Xeon(R) Processor @ 2.10GHz
CPU family: 6, Model: 207
```

| component | value |
|---|---|
| Python | 3.11.15 |
| numpy | 2.4.6 — BLAS/LAPACK: `scipy-openblas`, OpenBLAS 0.3.31.188.0 (DYNAMIC_ARCH, Haswell kernel listed, MAX_THREADS=64); SIMD found: AVX512_ICL / AVX512_SPR |
| scipy | 1.17.1 |
| torch | 2.14.1+cu130, CPU only (`torch.cuda.is_available() == False`), default `torch.get_num_threads() == 4` |
| GPU | none |

Scratchpad directory holding the scripts and raw outputs (`out_*.txt`; the previous revision's outputs are kept
in `old_run/`):
`/tmp/claude-0/-home-user-DEMON-Deterministic-Embedding-from-Manifold-Observation-Neighbors/08489aa1-e4c0-5211-aebf-268609f2704a/scratchpad/bench/` — referred to as `$BENCH` below.

## 1. Built-in bench (`python3 -m demon_accel.bench`)

Output pasted verbatim from `$BENCH/out_builtin.txt`.

```
$ OMP_NUM_THREADS=4 python3 -m demon_accel.bench --n 1024
kind=lowrank  n=1024  r_true=8  n_cols=1024  status=ADMIT  rank=8  t_dense=0.00976  t_probe=0.00856  flops_speedup=64  rel_err_bound=1.33e-14  probe_over_dense_flops=0.0723  t_served=0.000817  wall_speedup=11.9  rel_err_actual=1.53e-15  breakeven_calls=0.958
kind=lowrank  n=1024  r_true=32  n_cols=1024  status=ADMIT  rank=32  t_dense=0.0101  t_probe=0.0297  flops_speedup=16  rel_err_bound=1.27e-14  probe_over_dense_flops=0.303  t_served=0.00128  wall_speedup=7.86  rel_err_actual=2.31e-15  breakeven_calls=3.38
kind=lowrank  n=1024  r_true=128  n_cols=1024  status=ADMIT  rank=128  t_dense=0.01  t_probe=0.136  flops_speedup=4  rel_err_bound=9.61e-15  probe_over_dense_flops=1.03  t_served=0.00336  wall_speedup=2.98  rel_err_actual=2.72e-15  breakeven_calls=20.3
kind=fullrank  n=1024  r_true=1024  n_cols=1024  status=REFUSE_FULL_RANK  rank=263  t_dense=0.0103  t_probe=0.277  probe_over_dense_wall=26.9  rel_err_bound=5.58  probe_over_dense_flops=1.93
kind=anchors  n=1024  r_true=8  K=500  refused=False  checks=15  max_rel_err=2.15e-13  min_iterate_norm=0.643  flops_speedup=12.3  wall_speedup=4.34

$ OMP_NUM_THREADS=4 python3 -m demon_accel.bench --n 2048
kind=lowrank  n=2048  r_true=8  n_cols=2048  status=ADMIT  rank=8  t_dense=0.0887  t_probe=0.0217  flops_speedup=128  rel_err_bound=1.53e-14  probe_over_dense_flops=0.0361  t_served=0.00593  wall_speedup=15  rel_err_actual=1.32e-15  breakeven_calls=0.262
kind=lowrank  n=2048  r_true=32  n_cols=2048  status=ADMIT  rank=32  t_dense=0.0805  t_probe=0.0761  flops_speedup=32  rel_err_bound=1.6e-14  probe_over_dense_flops=0.151  t_served=0.00688  wall_speedup=11.7  rel_err_actual=2.37e-15  breakeven_calls=1.03
kind=lowrank  n=2048  r_true=128  n_cols=2048  status=ADMIT  rank=128  t_dense=0.0744  t_probe=0.305  flops_speedup=8  rel_err_bound=8.99e-15  probe_over_dense_flops=0.513  t_served=0.0146  wall_speedup=5.1  rel_err_actual=2.73e-15  breakeven_calls=5.1
kind=fullrank  n=2048  r_true=2048  n_cols=2048  status=REFUSE_FULL_RANK  rank=519  t_dense=0.0761  t_probe=1.1  probe_over_dense_wall=14.4  rel_err_bound=5.57  probe_over_dense_flops=1.86
kind=anchors  n=1024  r_true=8  K=500  refused=False  checks=15  max_rel_err=1.29e-13  min_iterate_norm=0.219  flops_speedup=12.3  wall_speedup=3.55
```

## 2. Operator stratum (`$BENCH/bench_operator.py`)

Square `A` (n x n), `B` (n x n) Gaussian, `tol = 1e-6`, `norm = "fro"` (gate defaults), `n_cols = n`.
Low-rank `A` from `demon_accel.bench.make_lowrank` (singular values linspace(1, 0.5, r)). Full-rank row:
`A = randn(n,n)/sqrt(n)`. Default `r_max = max_admissible_rank(n,n,n,2.0)` = 255 / 511 / 1023 at n = 1024 / 2048 / 4096,
so the widest probed basis is `r_max + 8` = 263 / 519 / 1031 (the `served rank` column shows that width for refusals).
`actual rel err` = `||A_hat B - A B||_F / ||A B||_F`. `break-even calls = t_probe / (t_dense - t_served)`.
Refusal cost: `probe wall / dense GEMM = t_probe / t_dense`; `probe_flops / flops_full` from `Admission.probe_flops`.

| n | true r | status | served rank | cert. rel_err_bound | actual rel err | t_dense [s] | t_served [s] | wall speedup | FLOP speedup | t_probe [s] | break-even calls | probe wall / dense GEMM | probe_flops / flops_full |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1024 | 8 | ADMIT | 8 | 1.33e-14 | 1.53e-15 | 0.0098 | 0.000869 | 11.3 | 64 | 0.00813 | 0.911 | 0.83 | 0.0723 |
| 1024 | 32 | ADMIT | 32 | 1.27e-14 | 2.31e-15 | 0.0101 | 0.00136 | 7.44 | 16 | 0.0297 | 3.38 | 2.93 | 0.303 |
| 1024 | 128 | ADMIT | 128 | 9.61e-15 | 2.72e-15 | 0.0105 | 0.0035 | 3 | 4 | 0.13 | 18.5 | 12.3 | 1.03 |
| 1024 | 512 | REFUSE_FULL_RANK | 263 | 5.6 | - | 0.0103 | - | - | 1.95 | 0.247 | - | 24 | 1.93 |
| 1024 | full | REFUSE_FULL_RANK | 263 | 5.62 | - | 0.00991 | - | - | 1.95 | 0.26 | - | 26.3 | 1.93 |
| 2048 | 8 | ADMIT | 8 | 1.64e-14 | 1.74e-15 | 0.078 | 0.0149 | 5.24 | 128 | 0.0191 | 0.303 | 0.245 | 0.0361 |
| 2048 | 32 | ADMIT | 32 | 1.3e-14 | 2.26e-15 | 0.0747 | 0.00457 | 16.3 | 32 | 0.0691 | 0.985 | 0.925 | 0.151 |
| 2048 | 128 | ADMIT | 128 | 9.28e-15 | 2.69e-15 | 0.0791 | 0.0149 | 5.31 | 8 | 0.287 | 4.47 | 3.63 | 0.513 |
| 2048 | 512 | REFUSE_NO_SPEEDUP | 512 | 1.03e-14 | - | 0.0887 | - | - | 2 | 1.09 | - | 12.3 | 1.86 |
| 2048 | full | REFUSE_FULL_RANK | 519 | 5.54 | - | 0.0845 | - | - | 1.97 | 1.15 | - | 13.6 | 1.86 |
| 4096 | 8 | ADMIT | 8 | 3.21e-14 | 1.49e-15 | 0.589 | 0.0207 | 28.5 | 256 | 0.0639 | 0.112 | 0.108 | 0.0181 |
| 4096 | 32 | ADMIT | 32 | 7.5e-15 | 2.55e-15 | 0.58 | 0.0264 | 22 | 64 | 0.22 | 0.397 | 0.379 | 0.0757 |
| 4096 | 128 | ADMIT | 128 | 8.81e-15 | 2.69e-15 | 0.582 | 0.0572 | 10.2 | 16 | 0.751 | 1.43 | 1.29 | 0.256 |
| 4096 | 512 | ADMIT | 512 | 9.5e-15 | 3.59e-15 | 0.568 | 0.163 | 3.49 | 4 | 2.73 | 6.72 | 4.8 | 0.929 |
| 4096 | full | REFUSE_FULL_RANK | 1031 | 5.42 | - | 0.569 | - | - | 1.99 | 6.39 | - | 11.2 | 1.81 |

Measured refusal cost, full-rank Gaussian (rows `full` above and the built-in bench):

| n | probe wall / one dense GEMM (this script) | probe wall / dense (built-in bench, `probe_over_dense_wall`) | probe_flops / flops_full |
|---|---|---|---|
| 1024 | 26.3 | 26.9 | 1.93 |
| 2048 | 13.6 | 14.4 | 1.86 |
| 4096 | 11.2 | - | 1.81 |

Observations (from the rows only):

* In every admitted row the actual error (1.5e-15 … 3.6e-15) is below the certified bound (7.5e-15 … 3.2e-14),
  and the served rank equals the true rank. The regression check in [Anomalies](#anomalies) (true rank 12/16/24/40,
  which are not ladder rungs) is also served at the true rank with ~1e-14 certificates and ~2e-15 actual error.
* Wall speedup is below FLOP speedup in every row: 11.3x vs 64x (n=1024, r=8), 28.5x vs 256x (n=4096, r=8),
  3.0x vs 4x (n=1024, r=128), 3.49x vs 4x (n=4096, r=512). The served product is two skinny GEMMs whose output
  (n x n) is written once either way, so at small r it is memory-bound while the dense GEMM is compute-bound on 4 cores.
  The gap is smallest at the largest ranks.
* n=2048, r=8 is an outlier: 14.9 ms served (5.24x) here, versus 5.93 ms (15x) in the built-in bench on the same
  shape, and slower than r=32 (4.57 ms) in the same run. See Anomaly C: the rank-8 served GEMM at n=2048 is not
  stable from run to run on this OpenBLAS.
* r=512 at n=1024 is REFUSE_FULL_RANK: `r_max = 255`, widest basis 263 < 512, residual bound 5.6. r=512 at n=2048
  meets the tolerance (bound 1.03e-14) but the smallest certified truncation is 512 > `r_max = 511`, so it is
  REFUSE_NO_SPEEDUP. r=512 at n=4096 (r_max 1023) is admitted at 3.49x wall / 4x FLOP.
* Refusal cost: the probe FLOPs are 1.8x–1.9x one dense GEMM, but the probe wall time is 11.2x–26.3x one dense
  GEMM (26.3x at 1024, 13.6x at 2048, 11.2x at 4096). The probe is dominated by `np.linalg.qr` on n x l panels and
  the per-rung residual probes, which run far below GEMM throughput; the FLOP count understates the wall cost by
  6x–14x. An operator that is full rank and multiplied fewer than ~11–27 times loses overall.
* Break-even for admitted operators: below one dense call for r=8 at every n and for r=32 at n ≥ 2048; 18.5 calls
  for r=128 at n=1024; 6.7 calls for r=512 at n=4096.

## 3. Decaying spectrum (`$BENCH/bench_decay.py`)

n = 2048, `A = U diag(s) V^T`, `s_i = exp(-i/tau)`, i = 0..2047, U and V Haar-orthogonal, `B` Gaussian 2048 x 2048.
One row per (tau, tol) for `norm="fro"` and one for `norm="spectral"`. The certificate is
`||A - A_hat||_2 / ||A||_norm`; the `actual` column is that same quantity computed exactly (dense SVD of `A - A_hat`),
`sigma_{r+1}/||A||_norm` is the Eckart–Young optimum at the served rank, and `product rel err` is
`||A_hat B - A B||_F / ||A B||_F` (which the gate does not bound; see `gate.py` docstring).

| tau | tol | norm | status | served rank | cert. rel_err_bound | actual ‖A−Â‖₂/‖A‖_norm | σ_{r+1}/‖A‖_norm | product rel err (Fro) | FLOP speedup | wall speedup | t_dense [s] | t_served [s] | t_probe [s] | probe wall / dense |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 16 | 0.01 | fro | ADMIT | 57 | 0.00991 | 0.00972 | 0.00972 | 0.0282 | 18 | 8 | 0.0756 | 0.00945 | 0.303 | 4.01 |
| 16 | 0.01 | spectral | ADMIT | 77 | 0.00983 | 0.00813 | 0.00813 | 0.00807 | 13.3 | 7.01 | 0.0756 | 0.0108 | 0.346 | 4.57 |
| 16 | 0.001 | fro | ADMIT | 94 | 0.000963 | 0.000963 | 0.000963 | 0.0028 | 10.9 | 5.47 | 0.0756 | 0.0138 | 0.582 | 7.69 |
| 16 | 0.001 | spectral | ADMIT | 111 | 0.000971 | 0.000971 | 0.000971 | 0.000971 | 9.22 | 5.65 | 0.0756 | 0.0134 | 0.667 | 8.82 |
| 16 | 1e-06 | fro | ADMIT | 215 | 9.94e-07 | 5e-07 | 5e-07 | 1.45e-06 | 4.76 | 3.62 | 0.0756 | 0.0209 | 0.532 | 7.04 |
| 16 | 1e-06 | spectral | ADMIT | 222 | 9.42e-07 | 9.42e-07 | 9.42e-07 | 9.39e-07 | 4.61 | 3.13 | 0.0756 | 0.0241 | 1.35 | 17.8 |
| 64 | 0.01 | fro | ADMIT | 187 | 0.00997 | 0.00944 | 0.00944 | 0.0537 | 5.48 | 3.26 | 0.0863 | 0.0265 | 1.1 | 12.8 |
| 64 | 0.01 | spectral | REFUSE_FULL_RANK | 519 | 0.0182 | - | 0.000301 | - | 1.97 | - | 0.0863 | - | 1.32 | 15.3 |
| 64 | 0.001 | fro | REFUSE_FULL_RANK | 519 | 0.00319 | - | 5.27e-05 | - | 1.97 | - | 0.0863 | - | 1.09 | 12.6 |
| 64 | 0.001 | spectral | REFUSE_FULL_RANK | 519 | 0.0182 | - | 0.000301 | - | 1.97 | - | 0.0863 | - | 1.33 | 15.5 |
| 64 | 1e-06 | fro | REFUSE_FULL_RANK | 519 | 0.00319 | - | 5.27e-05 | - | 1.97 | - | 0.0863 | - | 1.08 | 12.5 |
| 64 | 1e-06 | spectral | REFUSE_FULL_RANK | 519 | 0.0182 | - | 0.000301 | - | 1.97 | - | 0.0863 | - | 1.4 | 16.2 |
| 256 | 0.01 | fro | REFUSE_FULL_RANK | 519 | 1.28 | - | 0.0116 | - | 1.97 | - | 0.0779 | - | 1.18 | 15.2 |
| 256 | 0.01 | spectral | REFUSE_FULL_RANK | 519 | 14.5 | - | 0.132 | - | 1.97 | - | 0.0779 | - | 1.43 | 18.4 |
| 256 | 0.001 | fro | REFUSE_FULL_RANK | 519 | 1.28 | - | 0.0116 | - | 1.97 | - | 0.0779 | - | 1.18 | 15.1 |
| 256 | 0.001 | spectral | REFUSE_FULL_RANK | 519 | 14.5 | - | 0.132 | - | 1.97 | - | 0.0779 | - | 1.4 | 18 |
| 256 | 1e-06 | fro | REFUSE_FULL_RANK | 519 | 1.28 | - | 0.0116 | - | 1.97 | - | 0.0779 | - | 1.17 | 15.1 |
| 256 | 1e-06 | spectral | REFUSE_FULL_RANK | 519 | 14.5 | - | 0.132 | - | 1.97 | - | 0.0779 | - | 1.37 | 17.5 |

Observations:

* The certificate holds in all 7 admitted rows (`violates_bound: false` in `out_decay.txt`): the actual operator
  error equals the Eckart–Young value at the served rank (the projector residual is ~1e-14, so the certificate is
  `sigma_{r'+1}/||A||`) and is ≤ the bound. The served rank is now the smallest certified truncation (57, 94, 215 for
  fro at tau=16) rather than a ladder rung, so FLOP speedups are 18x / 10.9x / 4.76x (wall 8x / 5.5x / 3.6x) at
  tol 1e-2 / 1e-3 / 1e-6.
* The Frobenius-normalised certificate does not bound the relative product error: at tau=16, tol=1e-2 (fro) the
  operator error is 0.0097 but `||A_hat B - A B||_F/||A B||_F` is 0.028; at tau=64, tol=1e-2 (fro) 0.0094 vs 0.054.
  With `norm="spectral"` the product error matches the certificate (0.0081 vs 0.0098; 9.39e-7 vs 9.42e-7) because
  `||A||_2 ≤ ||A||_F` makes it the tighter normaliser; it costs a higher served rank (77 vs 57, 222 vs 215).
* Where it refuses: tau=64 is admitted only for fro at tol=1e-2 (rank 187, 3.26x wall). For tau=64 at tol ≤ 1e-3 and
  for tau=256 everywhere the widest basis (519) does not certify. The refusal is pessimistic by the factor the
  `gate.py` docstring warns about: at tau=64 the certified residual at width 519 is 0.00319 (fro) while the true
  `sigma_520/||A||_F` is 5.27e-5 (60x), and 0.0182 vs 3.01e-4 for spectral; rank ~300 would have met tol=1e-3 with
  a ~3.3x FLOP gain, but the probe cannot certify it. Spectral normalisation refuses tau=64 at tol=1e-2 (0.0182 > 0.01)
  where fro admits.
* Each refusal costs 1.1–1.4 s of probe = 12.5x–18.4x one dense GEMM (0.076–0.086 s); the ladder is walked to
  `r_max` before refusing, and the spectral path is ~20% more expensive (an extra 2-norm of `Q^T A` per rung).

## 4. Trajectory stratum (`$BENCH/bench_anchors.py`)

n = 1024, K = 500, `AnchorIterator(A, rank=r, tol=1e-9, check_every=50)` (defaults except rank). `A` from
`demon_accel.bench.make_lowrank_rho1(n, r)`: symmetric `U diag(linspace(1, 0.3, r)) U^T`, spectral radius exactly 1
(`rho` from `eigvalsh`), so iterates do not vanish (`min iterate norm` column). Full-rank row: `G = randn(n,n)`
scaled to spectral radius 1, with `rank=8` requested. `wall exact` is a plain Python loop of 500 `A @ y`;
`wall served` includes constructing the iterator and the fit. Error is relative to the exact iterate at each step.

| case | n | rank arg | K | refused | served steps | checks | last residual | max rel err vs exact | iterates with err > 1e-6 | min iterate norm | FLOP speedup (2n²K / it.flops) | wall exact [s] | wall served [s] | wall speedup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| make_lowrank_rho1 rank 4 (rho=1.000000) | 1024 | 4 | 500 | False | 494 | 15 | 4.71e-16 | 1.01e-13 | 0 | 0.661 | 17.3 | 0.0351 | 0.00549 | 6.4 |
| make_lowrank_rho1 rank 8 (rho=1.000000) | 1024 | 8 | 500 | False | 490 | 15 | 5.86e-16 | 1.33e-13 | 0 | 0.254 | 12.3 | 0.0343 | 0.00653 | 5.25 |
| make_lowrank_rho1 rank 32 (rho=1.000000) | 1024 | 32 | 500 | True | 32 | 6 | 8.9e-08 | 1.13e-09 | 0 | 0.085 | 0.954 | 0.038 | 0.0378 | 1 |
| full-rank Gaussian (rho=1), rank=8 requested | 1024 | 8 | 500 | True | 1 | 1 | 0.625 | 0 | 0 | 2.95 | 0.982 | 0.0304 | 0.0321 | 0.947 |

Observations:

* Rank 4 and 8: all 500 iterates within 1.3e-13 of exact, 15 checks, FLOP speedup 17.3x / 12.3x, wall 6.4x / 5.25x.
  The FLOP figure counts `4 n r` per served step plus `2 n²` per check; the wall figure is dominated by per-step
  Python and small-GEMV overhead (~11–13 µs per served step), so it is 2.3x–2.7x below the FLOP figure.
* Rank 32 is **refused** at served step 32 (6th check): the local residual 8.9e-8 exceeds `tol=1e-9` although every
  emitted iterate is within 1.13e-9 of exact. The iterator then falls back and finishes at 0.954x FLOPs and 1.0x wall,
  i.e. slightly slower than plain iteration. See Anomaly A for the cause (ill-conditioned Krylov anchors).
* Full-rank A: refused at the first check (served step 1), 0 wrong iterates (the repaired window is exact,
  `max rel err = 0`). Cost of refusal: 0.982x FLOPs (1.8% extra) and 0.947x wall (0.0321 s vs 0.0304 s, 5.6% extra).

## 5. torch MLP (`$BENCH/bench_torch.py`)

`nn.Sequential(Linear(1024,2048), ReLU, Linear(2048,2048), ReLU, Linear(2048,2048), ReLU, Linear(2048,1024))`,
float32, default init for layers 0 and 6. Layers 2 and 4 have their weight replaced by an exactly rank-16 matrix
`(U * s) @ V^T` with `s = linspace(1, 0.5, 16) * 4/sqrt(2048)`; biases left at default init.
`wrap_model(model, tol=1e-3, batch_hint=512)`. Forward: batch 512, `torch.no_grad()`, 1 warm-up, median of 20.
Default `r_max` for the 2048 x 1024 layers at `batch_hint=512` is `max_admissible_rank(2048,1024,512,2.0) = 341`,
hence the probed width 349 on refusal. `actual` is `||W - U S Vt||_F / ||W||_F` of the served factors.

Per-layer admission (4-thread run; the 1-thread run gives the same statuses and ranks, bounds 4.0e-6 / 4.15e-6):

| layer (weight shape) | status | served rank | cert. rel_err_bound | actual ‖W−Ŵ‖_F/‖W‖_F | FLOP speedup (layer) | probe_flops / layer flops |
|---|---|---|---|---|---|---|
| 0 (2048x1024) | REFUSE_FULL_RANK | 349 | 5.65 | - | 1.96 | 6.1 |
| 2 (2048x2048, exact rank 16) | ADMIT | 16 | 3.96e-06 | 7.27e-07 | 64 | 0.145 |
| 4 (2048x2048, exact rank 16) | ADMIT | 16 | 4.05e-06 | 7.74e-07 | 64 | 0.145 |
| 6 (1024x2048) | REFUSE_FULL_RANK | 349 | 5.86 | - | 1.96 | 6.1 |

Forward timing and output difference (wrapped vs original model, same input):

| threads | forward before [ms] | forward after [ms] | wall speedup | max abs output diff | rel Frobenius output diff | `wrap_model` (probe) time [s] | 2nd `wrap_model` re-wrapped layers |
|---|---|---|---|---|---|---|---|
| 4 | 33.6 | 12.1 | 2.78 | 9.31e-09 | 9.56e-08 | 0.801 | 0 |
| 1 (`torch.set_num_threads(1)`) | 103 | 39.1 | 2.64 | 9.31e-09 | 9.55e-08 | 0.637 | 0 |

Observations:

* Both rank-16 layers are served at rank 16 with certified bound ~4e-6 and actual weight error 7.3e-7 … 8.4e-7
  (previous revision: served at rank 8 with 57% error). Max abs output difference 9.3e-9 on outputs of magnitude up
  to 0.04.
* Whole-model speedup 2.78x (4 threads) / 2.64x (1 thread). The two refused layers stay on `nn.Linear`; from the
  layer shapes they hold 1/3 of the forward FLOPs (2·512·1024·2048 each vs 2·512·2048² for the served ones), so the
  bound is ≈ 2.95x; measured 2.64x–2.78x.
* The probe for the two refused layers costs 6.1x their own forward FLOPs each (0.64–0.80 s total `wrap_model`),
  i.e. 19–24 forward passes of the wrapped model before wrapping pays off, on this machine.
* `wrap_model` is idempotent: a second call wraps 0 layers.

## Anomalies

### A. Trajectory stratum refuses needlessly at rank 32: Krylov anchors are numerically rank-deficient

`$BENCH/diag_anchors32.py` (`out_diag_anchors32.txt`), n = 1024, r = 32, K = 500, own seed (so residuals differ
slightly from the section 4 row):

| eigenvalues in | tol | cond(M), M = [y₁ … y₃₄] | σ₃₂(M)/σ₁(M) | refused | served steps | checks | last residual | max rel err vs exact | FLOP speedup |
|---|---|---|---|---|---|---|---|---|---|
| [0.3, 1] | 1e-9 | 3.06e16 | 3.27e-17 | True | 32 | 6 | 1.50e-08 | 3.21e-10 | 0.954 |
| [0.3, 1] | 1e-6 | 3.06e16 | 3.27e-17 | True | 100 | 8 | 1.35e-06 | 4.74e-07 | 1.008 |
| [0.9, 1] | 1e-9 | 3.06e16 | 3.26e-17 | True | 50 | 7 | 1.12e-08 | 2.04e-09 | 0.980 |
| [0.9, 1] | 1e-6 | 3.06e16 | 3.26e-17 | True | 150 | 9 | 8.89e-06 | 9.84e-06 | 1.110 |

The anchor matrix of 34 consecutive iterates has condition number 3e16 for both spectra: a Krylov sequence is a
Vandermonde system in the eigenvalues and its 32nd singular value is at float64 round-off (3e-17 relative), so
`_fit`'s SVD cannot recover a 32-dimensional invariant subspace and the served step drifts at 1e-8 … 1e-5 per
step. The result is correct (the drift check catches it and the window is repaired; max error 3e-10 at tol 1e-9)
but the run ends at 0.95x–1.1x of plain iteration. Ranks 4 and 8 are fine (section 4). The trajectory stratum is
therefore usable here for r ≲ 10, not for r = 32, and the limit is numerical, not a tolerance setting.

### B. Refusal cost in wall time is 6x–14x its FLOP count

Section 2: the probe of a full-rank operator costs 1.8x–1.9x one dense GEMM in FLOPs (`probe_flops/flops_full`)
but 11.2x–26.3x in wall time. `Admission.probe_flops` is therefore not a usable predictor of probe wall cost on this
machine; the QR factorisations and 10-vector residual probes per rung run at a small fraction of GEMM throughput.
Likewise the torch refusals cost 6.1x the layer's forward FLOPs.

### C. Served time of the rank-8 product at n=2048 is not reproducible

Four measurements of the same served product (rank 8, n = 2048, float64, 4 threads) in this session:
15.0x (built-in bench, 5.93 ms), 5.24x (section 2, 14.9 ms), and in `$BENCH/probe_r8.py` (`out_probe_r8.txt`,
15 reps) 6.03 ms median / 5.81 ms min for the whole product while the second GEMM alone (`U @ T`, 2048x8 times
8x2048) measured 11.2 ms, against 4.45 ms for the same GEMM at rank 16:

| rank | `Vt @ B` [s] | `U @ T` [s] | served total [s] (median) | served total [s] (min) |
|---|---|---|---|---|
| 8 | 0.00119 | 0.0112 | 0.00603 | 0.00581 |
| 16 | 0.00143 | 0.00445 | 0.00607 | 0.00575 |
| 32 | 0.00208 | 0.0056 | 0.00816 | 0.00697 |
| 64 | 0.00318 | 0.00591 | 0.00963 | 0.00929 |

The inner-dimension-8 GEMM that writes the 32 MB result is memory-bound and its time varies by ~2.5x between
otherwise identical runs on this OpenBLAS build; the rank-8 wall speedup at n=2048 should be read as
"between 5x and 15x", not as a single number. The n=1024 and n=4096 rank-8 rows did not show this.

### D. Minor

* `sketch.adaptive_rank` contains a dead branch `s1 = _norm(xp, Q.T @ A) if False else ...`; harmless, but the
  spectral path computes a 2-norm (SVD) of `Q^T A` at every rung, which is the ~20% extra probe cost seen in section 3.
* The two regression checks for the previous revision's defects now pass (`$BENCH/verify_anomaly.py`,
  `out_verify.txt`): true rank 12/16/24/40 at n=1024 served at rank 12/16/24/40 with bounds 1.1e-14 … 1.4e-14 and
  actual operator error 1.4e-15 … 2.6e-15; torch rank-16 served at 16 (bound 4.44e-6, actual 8.0e-7); the full-rank
  trajectory refuses at served step 1 with 0 wrong iterates and 1.8% FLOP overhead.

## Where it wins / where it refuses

Measured wins: exact low-rank operators reused many times — 11.3x–28.5x wall for rank 8 (64x–256x FLOP),
7.4x–22x for rank 32, 3.0x–10.2x for rank 128, 3.49x for rank 512 at n=4096, every one with actual error below the
certificate and probe break-even under one dense call for rank 8; decaying spectra with tau=16 at n=2048 served at
3.1x–8x wall (4.6x–18x FLOP) and tau=64 at tol=1e-2 (fro) at 3.26x; rank-4 and rank-8 trajectories at 6.4x / 5.25x
wall with ≤ 1.3e-13 error; and a torch MLP with two low-rank hidden layers at 2.78x (4 threads) / 2.64x (1 thread)
with 9.3e-9 max output difference. Wall speedups are uniformly below FLOP speedups (memory-bound skinny GEMMs for the
operator stratum, per-step Python overhead for the trajectory stratum), and the rank-8 n=2048 product is not
reproducible between 5x and 15x.

Measured refusals: full-rank Gaussian at every n (FULL_RANK; probe = 11.2x–26.3x one dense GEMM, 1.8x–1.9x in
FLOPs); rank n/4 at n=2048 (NO_SPEEDUP, 512 > r_max 511); tau=64 at tol ≤ 1e-3 and tau=256 at every tol (FULL_RANK,
pessimistic by ~60x against the true Eckart–Young residual); the full-rank trajectory (refused at served step 1,
5.6% wall overhead, result exact); and the rank-32 trajectory, which is admitted and then refused because the
Krylov anchors are ill-conditioned, finishing at 0.95x FLOPs / 1.0x wall. No admitted operator-stratum or torch case
was slower than dense, and no served error exceeded its certificate in any row of this report.

## Commands

All run from the repository root with `OMP_NUM_THREADS=4`. `$BENCH` is the scratchpad directory named under
[Machine](#machine).

```
git rev-parse HEAD                       # 508d7a1ba002a159a6dc51313da3c9eba96a8fe3
git diff --stat 3d33572 508d7a1          # docs/DESIGN.md only
lscpu | head
OMP_NUM_THREADS=4 python3 -c "import numpy as np, scipy, torch; print(np.__version__, scipy.__version__, torch.__version__); np.show_config()"

OMP_NUM_THREADS=4 python3 -m demon_accel.bench --n 1024      # section 1 -> $BENCH/out_builtin.txt
OMP_NUM_THREADS=4 python3 -m demon_accel.bench --n 2048

cd $BENCH && export OMP_NUM_THREADS=4
python3 bench_operator.py   | tee out_operator.txt          # section 2
python3 bench_decay.py      | tee out_decay.txt             # section 3
python3 bench_anchors.py    | tee out_anchors.txt           # section 4
python3 bench_torch.py 4    | tee out_torch_4t.txt          # section 5, 4 threads
python3 bench_torch.py 1    | tee out_torch_1t.txt          # section 5, torch.set_num_threads(1)
python3 verify_anomaly.py   | tee out_verify.txt            # regression checks (Anomalies D)
python3 probe_r8.py         | tee out_probe_r8.txt          # Anomaly C
python3 diag_anchors32.py   | tee out_diag_anchors32.txt    # Anomaly A
```

Scripts: `$BENCH/common.py` (timing helper: warm-up + median of `BENCH_REPS`, default 7), `bench_operator.py`,
`bench_decay.py`, `bench_anchors.py`, `bench_torch.py`, `verify_anomaly.py`, `probe_r8.py`, `diag_anchors32.py`.
