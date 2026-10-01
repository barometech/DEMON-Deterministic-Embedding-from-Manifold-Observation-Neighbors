"""matmul(A, B, tol): served from the admitted stratum, else the backend GEMM."""
from __future__ import annotations
from .gate import admit, Admission


def matmul(A, B, tol: float = 1e-6, admission: Admission | None = None, **gate_kw):
    """Return (C, admission). If `admission` is given (a previous probe of A) it is reused,
    which is the intended pattern for reused operators (weights, fixed transfer matrices)."""
    if admission is None:
        admission = admit(A, tol=tol, n_cols=B.shape[1] if B.ndim == 2 else 1, **gate_kw)
    if admission.admitted:
        return admission.operator.matmul(B), admission
    return A @ B, admission
