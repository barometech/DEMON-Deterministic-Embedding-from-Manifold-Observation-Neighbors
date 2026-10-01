"""demon_accel: stratified-admission acceleration of matrix products.

Accelerates products whose operator (or trajectory) lives on a low-dimensional stratum,
certifies the error (Eckart-Young / randomized a posteriori estimate), and refuses
(falls back to the backend GEMM) otherwise.  See docs/DESIGN.md.
"""
from .sketch import range_finder, residual_estimate, sketch_svd
from .lowrank import LowRankOperator
from .gate import Admission, Status, admit
from .gemm import matmul
from .anchors import AnchorIterator

__all__ = [
    "range_finder", "residual_estimate", "sketch_svd",
    "LowRankOperator", "Admission", "Status", "admit", "matmul", "AnchorIterator",
]
__version__ = "0.1.0"
