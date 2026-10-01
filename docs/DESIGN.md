# demon_accel — design

Scope: accelerate matrix products **only where the operator or the trajectory lives on a
low-dimensional stratum**, certify the error, and refuse (fall back to BLAS/cuBLAS) otherwise.
This is the computational instance of Popovich, *Contraction versus Recurrence*
(arXiv:2607.14885, §5 "anchor-subspace acceleration" and "stratified admission").

Every statement below that carries a number was measured by `python -m demon_accel.bench`
or asserted by a test in `tests/`; see `docs/BENCH.md` for the full tables and the commit
hash they were produced on.

## What is and is not claimed

* Dense full-rank GEMM is not accelerated. BLAS/cuBLAS are near roofline there; the gate
  returns `REFUSE_*` and the product is served by the backend's own GEMM.
* Acceleration happens on two strata:
  1. **Operator stratum** (fresh inputs): `A ≈ U S Vᵀ` with served rank `r`. Serving `A @ B`
     costs `2r(k n + m n) + r n` flops instead of `2 m k n`. The one-time probe is **not**
     amortised by the gate (it is reported as `Admission.probe_flops`); measured break-even is
     ~1 dense call for `r ≤ 32` at `n ≥ 1024` and ~20 dense calls for `r = 128` at `n = 1024`.
     A *refusal* is expensive: the ladder walks to the admissibility bound before giving up,
     measured 14–28 dense GEMMs of wall time on 1024–2048 square Gaussian matrices
     (1.9× dense in flops; the rest is QR / skinny-GEMM inefficiency). Probe reused operators,
     not one-shot products.
  2. **Trajectory stratum** (iterated products `y_{k+1} = A y_k`): the first `r+2` iterates span
     an `A`-invariant subspace containing the trajectory (Krylov + Cayley–Hamilton in the
     `r`-dimensional coordinates). A served step costs `4 n r` flops; **including the exact
     checks the per-step cost is `O(n r + n² / check_every)`**, not `O(n r)`. Measured on
     `n = 1024`, `r = 8`, 500 steps: 12.3× fewer flops than dense iteration (checks are the
     dominant cost), 3–5× wall.

## Certificate (what `rel_err_bound ≤ tol` means)

Randomized range finder (Halko–Martinsson–Tropp 2011, Alg. 4.4 with `q` power iterations)
gives an orthonormal `Q` of width `l = r + 8`. A posteriori (HMT Lemma 4.1, 10 Gaussian
probes, failure probability `≤ 1e-10` per rung, `≤ n_rungs · 1e-10` over the ladder since the
probes are reused):

    ‖A − Q Qᵀ A‖₂ ≤ bound.

The served operator is the rank-`r'` truncation of `Q Qᵀ A`. Because `(I − Q Qᵀ)A` and
`Q QᵀA − (Q QᵀA)_{r'}` have orthogonal column spaces,

    ‖A − Â‖₂ ≤ sqrt(bound² + s_{r'+1}²),      s = singular values of Qᵀ A,

and `rel_err_bound` is this quantity divided by `‖A‖_F` (`norm="fro"`, default) or by a
lower estimate of `‖A‖₂` (`norm="spectral"`, conservative). Consequences for the served
product `Ĉ = Â B`:

| guaranteed | not guaranteed |
|---|---|
| `‖Ĉ − AB‖₂ ≤ tol ‖A‖_F ‖B‖₂` (fro) | `‖A − Â‖₂ / ‖A‖₂ ≤ tol` under `norm="fro"` (off by up to `sqrt(stable rank)`) |
| `‖Ĉ − AB‖_F ≤ tol ‖A‖_F ‖B‖_F` (fro) | `‖Ĉ − AB‖ / ‖AB‖ ≤ tol`: for `B` concentrated on the dropped directions this ratio is `O(1)` |
| `‖Ĉ − AB‖₂ ≤ tol ‖A‖₂ ‖B‖₂` (spectral) | `‖A − Â‖_F / ‖A‖_F ≤ tol` (Lemma 4.1 is spectral; held in all trials but not proved here) |

The probe reacts to the **Frobenius tail** `(Σ_{j>l} σ_j²)^{1/2}`, so operators with slowly
decaying spectra are refused pessimistically (measured ~70× for `σ_j = 1/j`). This is a
property of the certificate, not a bug; it is the price of a bound that holds with
probability `1 − 1e-10` instead of in expectation.

**Optimality.** Eckart–Young (Frobenius) and Mirsky (all unitarily invariant norms) give the
lower bound `σ_{r+1}` / `(Σ_{j>r} σ_j²)^{1/2}` for any rank-`r` approximation, i.e. the distance
to the rank-`r` determinantal variety. The randomized sketch attains it only up to the HMT
factors (expectation bounds Thm 10.5/10.6, Cor 10.10 for the power scheme); measured
spectral ratio to the optimum 1.0–1.45 at `l = 16`, `q = 1`. The statement "achievable
error equals the distance to the variety" is true of the exact truncated SVD, not of this
library's sketch.

## Admission rule

```
r_adm = floor(2 m k / (speedup_min · (2(m+k) + 1)))       # exact condition for >= speedup_min FLOP gain
ladder on probed width l = 8+8, 16+8, 32+8, ... while bound/‖A‖ > tol and ladder rank < r_max (default r_adm)
if bound/‖A‖ > tol                      -> REFUSE_FULL_RANK   (tolerance not met within the rank budget)
r' = smallest truncation with sqrt(bound² + s_{r'+1}²)/‖A‖ <= tol
if r' > r_max or flops_served(r') · speedup_min > flops_dense -> REFUSE_NO_SPEEDUP
else                                    -> ADMIT(rank = r', certificate)
```

On square shapes `r_adm = floor(m²/(4m+1)) = m/4 − 1`, so rank exactly `m/4` is never
admissible at `speedup_min = 2` (the `+ r n` term). Rectangular shapes are handled by the same
formula (e.g. `4096×256`: `r_adm = 120`).

## Probe cost

Per rung of width `l`: range finder `2 m k l (2q+1)`, 10 probe vectors `20 m k`; once:
`Qᵀ A` costs `2 m k l`. Summed over the ladder (widths roughly doubling) this is
`≈ 0.07` dense GEMMs for exact rank 8 at `n = 1024` and `≈ 1.9` dense GEMMs for a refusal.
`Admission.probe_flops` reports the per-call estimate.

## torch (`DemonLinear`, `wrap_model`)

A layer is served only if its weight is numerically of rank `≤ r_adm` at the tolerance,
with the tolerance measured as above (`‖W − Ŵ‖₂ / ‖W‖_F`). Random-init dense layers and
layers with slowly decaying spectra are refused; the shipped defaults therefore serve
essentially no trained dense layer unless it was made low-rank (factorised, pruned, LoRA
merged, or heavily tolerant `tol`). The output error relative to `‖W x‖` is not certified.
The served path gives no gradient to `linear.weight`; call `refit()` after optimizer steps.
On CUDA the refused path is cuBLAS, untouched. GPU execution: NOT VERIFIED in this
environment (no GPU).

## Numerics

float64 primary. float32: the probe's noise floor is ~4e-6 relative, so exact low-rank
float32 operators are refused at `tol = 1e-6` and admitted at `1e-3`. Real dtypes only
(`.T` is not conjugated). Integer inputs must be cast to float by the caller.

## Trajectory stratum: what the check certifies

The check compares the served step with one exact application of `A` to the *previous
served* iterate. It certifies local invariance, not global error: measured global error
over 500 steps reached ~200 × tol for eigenvalue moduli in `[0.3, 1]` without triggering a
refusal, while moduli all in `[0.9, 1]` triggered needless refusals. For a global budget use
`tol / n_steps`; for long runs re-anchor from exact iterates.

## Verification policy

Numbers in README/BENCH tables come from `python -m demon_accel.bench` (and the scripts
listed in BENCH.md); laws have pytest assertions with tolerances (`python -m pytest tests -q`,
113 tests at the time of writing). Numbers are not typed by hand.
