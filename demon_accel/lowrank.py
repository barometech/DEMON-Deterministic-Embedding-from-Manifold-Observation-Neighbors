"""Low-rank operator served from factors, with FLOP accounting."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class LowRankOperator:
    """A ≈ U @ diag(S) @ Vt with U: (m x r), S: (r,), Vt: (r x k)."""
    U: object
    S: object
    Vt: object

    @property
    def shape(self):
        return (self.U.shape[0], self.Vt.shape[1])

    @property
    def rank(self) -> int:
        return int(self.S.shape[0])

    def matmul(self, B):
        """(U S Vt) @ B in 2 r (k n + m n) flops instead of 2 m k n. B may be 1-D (k,)."""
        T = self.Vt @ B                          # (r x n) or (r,)
        T = T * (self.S[:, None] if T.ndim == 2 else self.S)
        return self.U @ T                        # (m x n) or (m,)

    __matmul__ = matmul

    def rmatmul(self, X):
        """X @ (U S Vt) for X: (p x m) or (m,)."""
        T = X @ self.U                           # (p x r) or (r,)
        T = T * (self.S[None, :] if T.ndim == 2 else self.S)
        return T @ self.Vt

    def dense(self):
        return (self.U * self.S[None, :]) @ self.Vt

    def flops_matmul(self, n: int) -> int:
        m, k = self.shape
        return self.flops_served(m, k, n, self.rank)

    @staticmethod
    def flops_served(m: int, k: int, n: int, r: int) -> int:
        return 2 * r * (k * n + m * n) + r * n

    @staticmethod
    def flops_dense(m: int, k: int, n: int) -> int:
        return 2 * m * k * n

    @staticmethod
    def max_admissible_rank(m: int, k: int, n: int, speedup_min: float) -> int:
        """Largest r with flops_served(r) * speedup_min <= flops_dense:
        r <= 2 m k n / (speedup_min * (2 (k + m) n + n)) = 2 m k / (speedup_min * (2 (m + k) + 1))."""
        return int((2 * m * k) // (speedup_min * (2 * (m + k) + 1)))
