"""DemonLinear: nn.Linear served from its admitted low-rank stratum, else the original path.

Weight W: (out x in). y = x @ W^T + b. Served: y = ((x @ V) * S) @ U^T + b, where W ≈ U S Vt.
The probe runs once (on the weight's device) and is re-run after `refit()` (e.g. after an
optimizer step). On CUDA the fallback path is cuBLAS, untouched.

Honest scope: a layer is served only if its weight is numerically of rank
<= max_admissible_rank (about min(out,in)/4 on square shapes) at the tolerance, where the
tolerance is the certified ||W - W_hat||_2 / ||W||_F. Random-init dense layers and layers
with slowly decaying spectra are refused. The output error relative to ||W x|| is NOT
certified (it can be O(1) for inputs concentrated on the dropped directions). The served
path gives no gradient to `linear.weight` (factors are detached buffers): refit() after
each optimizer step if training.
"""
from __future__ import annotations
import torch
from torch import nn
from .gate import admit, Admission, Status


class DemonLinear(nn.Module):
    def __init__(self, linear: nn.Linear, tol: float = 1e-3, batch_hint: int = 1024,
                 speedup_min: float = 2.0, r_max: int | None = None, seed: int = 0):
        super().__init__()
        self.linear = linear
        self.tol, self.batch_hint, self.speedup_min, self.r_max, self.seed = tol, batch_hint, speedup_min, r_max, seed
        self.admission: Admission | None = None
        self.register_buffer("_U", torch.empty(0), persistent=False)
        self.register_buffer("_S", torch.empty(0), persistent=False)
        self.register_buffer("_Vt", torch.empty(0), persistent=False)
        self.refit()

    @torch.no_grad()
    def refit(self) -> Admission:
        W = self.linear.weight.detach()
        # cost model: product W^T-side with batch_hint rows == W (out x in) @ X^T (in x batch)
        adm = admit(W, tol=self.tol, n_cols=self.batch_hint, r_max=self.r_max,
                    speedup_min=self.speedup_min, seed=self.seed)
        self.admission = adm
        if adm.admitted:
            op = adm.operator
            self._U, self._S, self._Vt = op.U.contiguous(), op.S.contiguous(), op.Vt.contiguous()
        return adm

    @property
    def served(self) -> bool:
        return self.admission is not None and self.admission.admitted

    def forward(self, x):
        if not self.served:
            return self.linear(x)
        t = (x @ self._Vt.T) * self._S           # (..., r)
        y = t @ self._U.T                         # (..., out)
        if self.linear.bias is not None:
            y = y + self.linear.bias
        return y

    def extra_repr(self):
        a = self.admission
        return f"status={a.status.value}, rank={a.rank}, rel_err_bound={a.rel_err_bound:.2e}, flops_speedup={a.speedup_flops:.1f}x"


def wrap_model(model: nn.Module, **kw) -> dict:
    """Replace every nn.Linear in `model` in place by DemonLinear. Returns {name: Admission}."""
    report = {}
    for name, mod in list(model.named_modules()):
        if isinstance(mod, DemonLinear):          # idempotent: never re-wrap the inner Linear
            continue
        for cname, child in list(mod.named_children()):
            if isinstance(child, nn.Linear) and not isinstance(child, DemonLinear):
                dl = DemonLinear(child, **kw)
                setattr(mod, cname, dl)
                report[f"{name}.{cname}".lstrip(".")] = dl.admission
    return report
