# demon_accel

Stratified-admission acceleration of matrix products. Computational instance of
Popovich, *Contraction versus Recurrence* (arXiv:2607.14885, §5).

**What it does.** Probes an operator `A` with a randomized range finder, decides whether
`A` lives on a low-rank stratum at your tolerance, and if so serves `A @ B` from the
factors in `2r(k+m)n` flops with a certified residual. If not, it refuses and the product
goes to the backend GEMM (OpenBLAS / MKL / cuBLAS) untouched.

**What it does not do.** It does not make dense full-rank GEMM faster. Nothing does; those
kernels are at the hardware roofline. The gain exists only where structure exists, and the
gate's job is to say *no* when it does not.

```python
import numpy as np
from demon_accel import admit, matmul, AnchorIterator

adm = admit(A, tol=1e-6, n_cols=B.shape[1])   # probe once per reused operator
print(adm.status, adm.rank, adm.rel_err_bound, adm.speedup_flops)
C, adm = matmul(A, B, admission=adm)          # served or BLAS, your choice is made for you

it = AnchorIterator(A, rank=8, tol=1e-9)      # iterated products y_{k+1} = A y_k
ys = it.run(y0, 500)                          # O(n r) per step after r+2 anchors, drift-checked
```

```python
from demon_accel.torch_ext import wrap_model
report = wrap_model(model, tol=1e-3, batch_hint=512)   # every nn.Linear -> DemonLinear
for name, adm in report.items(): print(name, adm.status.value, adm.rank, f"{adm.speedup_flops:.1f}x")
```

Design: `docs/DESIGN.md`. Measured numbers: `docs/BENCH.md` (generated, not typed).
Tests: `python -m pytest tests -q`.
