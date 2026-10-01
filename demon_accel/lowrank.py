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
        """(U S Vt) @ B in 2 r (k n + m n) flops instead of 2 m k n."""
        T = self.Vt @ B                          # (r x n)
        T = T * self.S[:, None]                  # scale rows
        return self.U @ T                        # (m x n)

    __matmul__ = matmul

    def rmatmul(self, X):
        """X @ (U S Vt) for X: (p x m)."""
        T = (X @ self.U) * self.S[None, :]
        return T @ self.Vt

    def dense(self):
        return (self.U * self.S[None, :]) @ self.Vt

    def flops_matmul(self, n: int) -> int:
        m, k = self.shape
        r = self.rank
        return 2 * r * (k * n + m * n) + r * n

    @staticmethod
    def flops_dense(m: int, k: int, n: int) -> int:
        return 2 * m * k * n
