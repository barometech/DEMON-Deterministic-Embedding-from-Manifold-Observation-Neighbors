# demon_accel — design

Scope: accelerate matrix products **only where the operator or the trajectory lives on a
low-dimensional stratum**, certify the error, and refuse (fall back to BLAS/cuBLAS) otherwise.
This is the computational instance of Popovich, *Contraction versus Recurrence*
(arXiv:2607.14885, §5 "anchor-subspace acceleration" and "stratified admission").

## What is and is not claimed

* Dense full-rank GEMM is not accelerated. BLAS/cuBLAS are near roofline there; the gate
  returns `REFUSE` and the product is served by the backend's own GEMM.
* Acceleration happens on two strata:
  1. **Operator stratum** (fresh inputs): `A ≈ U S Vᵀ` with rank `r`. Serving `A @ B`
     costs `2r(k n + m n)` instead of `2 m k n`. One-time probe cost `≈ 2 m k (r+p) (q+1)`
     amortizes over reuse (weights of a layer are reused every forward pass).
  2. **Trajectory stratum** (iterated products `y_{k+1} = A y_k`): the iterates span a
     subspace `col(Q)` of dimension `≤ rank(A)` (often lower: Krylov collapse onto the
     dominant invariant subspace). After `r+2` anchors, each step costs `O(n r)`.
* Error certificate: for the operator stratum, the achievable error equals the distance to
  the rank-`r` determinantal variety (Eckart–Young): `‖A − A_r‖₂ = σ_{r+1}`,
  `‖A − A_r‖_F² = Σ_{i>r} σ_i²`. The randomized probe estimates this residual a posteriori
  (Halko–Martinsson–Tropp 2011, Lemma 4.1) with Gaussian test vectors.
* Tolerances are **relative** (`‖A − Â‖ / ‖A‖`) unless stated.

## Modules

| file | role |
|---|---|
| `sketch.py` | randomized range finder (adaptive rank, power iterations), a posteriori residual estimator |
| `lowrank.py` | `LowRankOperator(U, S, Vt)`: matmul from factors, FLOP accounting |
| `gate.py` | `admit(A, tol)` → `Admission(status, rank, est_rel_err, flops_full, flops_served)`; REFUSE when no speedup or no tolerance met |
| `anchors.py` | `AnchorIterator`: trajectory-stratum accelerator with drift certificate |
| `gemm.py` | `matmul(A, B, tol)` : gate → served or BLAS; returns `(C, Admission)` |
| `torch_ext.py` | `DemonLinear`: wraps `nn.Linear`, probes weight once, serves from factors when admitted, else original path (cuBLAS on CUDA) |
| `bench.py` | wall-clock + FLOP benchmarks vs the backend GEMM on synthetic strata and random full-rank |

## Admission rule

```
sketch A with target rank r (adaptive, grow while est_rel_err > tol and r < r_max)
if est_rel_err > tol:                      -> REFUSE (full rank at this tolerance)
if flops_served >= speedup_min * flops_full: -> REFUSE (rank too high to pay off)
else                                       -> ADMIT(rank=r, certificate=est_rel_err)
```

`r_max` defaults to `min(m,k)//4` so an admitted operator is guaranteed ≥ ~2× FLOP reduction
on square shapes (`2r(k+m)n ≤ 2mkn/2`).

## Verification policy

Every number in README/bench tables is produced by `python -m demon_accel.bench` and every
law has a pytest assertion with a tolerance. Numbers are not typed by hand.
