"""torch_ext.py: DemonLinear and wrap_model."""
from __future__ import annotations

import numpy as np
import pytest
import torch
from torch import nn

from demon_accel import Admission, Status
from demon_accel.torch_ext import DemonLinear, wrap_model

OUT, IN = 256, 512
OVERSAMPLE = 8

def _lowrank_linear(out=OUT, inp=IN, r=4, bias=True, seed=0):
    g = torch.Generator().manual_seed(seed)
    U = torch.linalg.qr(torch.randn(out, r, generator=g))[0]
    V = torch.linalg.qr(torch.randn(inp, r, generator=g))[0]
    s = torch.linspace(1.0, 0.5, r)
    lin = nn.Linear(inp, out, bias=bias)
    with torch.no_grad():
        lin.weight.copy_((U * s) @ V.T)
        if bias:
            lin.bias.copy_(torch.randn(out, generator=g) * 0.1)
    return lin


def _random_linear(out=OUT, inp=IN, seed=1):
    torch.manual_seed(seed)
    return nn.Linear(inp, out)


def _mlp(seed=2, lowrank_first=False):
    torch.manual_seed(seed)
    l0 = _lowrank_linear(128, 64, r=4, seed=seed) if lowrank_first else nn.Linear(64, 128)
    return nn.Sequential(l0, nn.ReLU(), nn.Linear(128, 128), nn.ReLU(), nn.Linear(128, 10))


# ----------------------------------------------------------------------------- served path
@pytest.mark.parametrize("shape", [(32, IN), (4, 16, IN)], ids=["B_in", "B_T_in"])
def test_lowrank_linear_is_served_and_matches(shape):
    lin = _lowrank_linear()
    dl = DemonLinear(lin, tol=1e-3)
    assert dl.served and dl.admission.status is Status.ADMIT
    assert dl.admission.rank == 4                       # exact numerical rank is served
    assert dl._U.dtype == torch.float32 and dl._U.shape == (OUT, dl.admission.rank)
    x = torch.randn(*shape, generator=torch.Generator().manual_seed(3))
    with torch.no_grad():
        y, y_ref = dl(x), lin(x)
    assert y.shape == y_ref.shape == (*shape[:-1], OUT)
    assert torch.allclose(y, y_ref, atol=1e-5, rtol=1e-5), float((y - y_ref).abs().max())


def test_lowrank_linear_without_bias():
    lin = _lowrank_linear(bias=False, seed=4)
    dl = DemonLinear(lin, tol=1e-3)
    assert dl.served
    x = torch.randn(8, IN, generator=torch.Generator().manual_seed(5))
    with torch.no_grad():
        assert torch.allclose(dl(x), lin(x), atol=1e-5, rtol=1e-5)


def test_served_cost_model_uses_batch_hint():
    lin = _lowrank_linear()
    dl = DemonLinear(lin, tol=1e-3, batch_hint=777)
    a = dl.admission
    assert a.flops_full == 2 * OUT * IN * 777
    assert a.flops_served == a.operator.flops_matmul(777)
    assert a.speedup_flops >= 2.0


def test_r_max_and_speedup_min_forwarded():
    lin = _lowrank_linear()
    assert DemonLinear(lin, tol=1e-3, speedup_min=1e9).served is False
    assert DemonLinear(lin, tol=1e-3, speedup_min=1e9).admission.status is Status.REFUSE_NO_SPEEDUP
    dl = DemonLinear(lin, tol=1e-3, r_max=4)
    assert dl.served and dl.admission.rank == 4


# ----------------------------------------------------------------------------- fallback path
def test_random_linear_not_served_and_bitwise_identical():
    lin = _random_linear()
    dl = DemonLinear(lin, tol=1e-3)
    assert not dl.served
    assert dl.admission.status is Status.REFUSE_FULL_RANK
    for shape in [(32, IN), (4, 16, IN)]:
        x = torch.randn(*shape, generator=torch.Generator().manual_seed(6))
        with torch.no_grad():
            assert torch.equal(dl(x), lin(x))


def test_state_dict_has_no_factor_buffers():
    dl = DemonLinear(_lowrank_linear())
    keys = set(dl.state_dict().keys())
    assert keys == {"linear.weight", "linear.bias"}


def test_extra_repr_mentions_status():
    dl = DemonLinear(_lowrank_linear())
    r = repr(dl)
    assert "status=ADMIT" in r and "rank=" in r and "flops_speedup=" in r
    assert "status=REFUSE_FULL_RANK" in repr(DemonLinear(_random_linear()))


# ----------------------------------------------------------------------------- gradients
def test_gradients_flow_through_served_path():
    lin = _lowrank_linear()
    dl = DemonLinear(lin, tol=1e-3)
    assert dl.served
    x = torch.randn(16, IN, generator=torch.Generator().manual_seed(7), requires_grad=True)
    loss = dl(x).square().mean()
    loss.backward()
    assert x.grad is not None and torch.isfinite(x.grad).all() and x.grad.abs().sum() > 0
    assert lin.bias.grad is not None and torch.isfinite(lin.bias.grad).all()
    # the input gradient agrees with the exact layer's gradient
    x2 = x.detach().clone().requires_grad_(True)
    lin(x2).square().mean().backward()
    assert torch.allclose(x.grad, x2.grad, atol=1e-5, rtol=1e-4)


def test_gradients_flow_through_fallback_path():
    lin = _random_linear()
    dl = DemonLinear(lin, tol=1e-3)
    x = torch.randn(16, IN, generator=torch.Generator().manual_seed(8), requires_grad=True)
    dl(x).sum().backward()
    assert x.grad is not None and lin.weight.grad is not None


# ----------------------------------------------------------------------------- refit
def test_refit_after_full_rank_update_flips_served_off():
    lin = _lowrank_linear()
    dl = DemonLinear(lin, tol=1e-3)
    assert dl.served
    with torch.no_grad():
        lin.weight.copy_(torch.randn(OUT, IN, generator=torch.Generator().manual_seed(9)) / IN ** 0.5)
    adm = dl.refit()
    assert isinstance(adm, Admission) and adm is dl.admission
    assert not dl.served and adm.status is Status.REFUSE_FULL_RANK
    x = torch.randn(8, IN, generator=torch.Generator().manual_seed(10))
    with torch.no_grad():
        assert torch.equal(dl(x), lin(x))


def test_refit_back_to_low_rank_flips_served_on():
    lin = _random_linear()
    dl = DemonLinear(lin, tol=1e-3)
    assert not dl.served
    with torch.no_grad():
        lin.weight.copy_(_lowrank_linear(seed=11).weight)
    dl.refit()
    assert dl.served
    x = torch.randn(8, IN, generator=torch.Generator().manual_seed(12))
    with torch.no_grad():
        assert torch.allclose(dl(x), lin(x), atol=1e-5, rtol=1e-5)


# ----------------------------------------------------------------------------- wrap_model
def test_wrap_model_replaces_all_linears_and_reports():
    model = _mlp(lowrank_first=True)
    x = torch.randn(5, 64, generator=torch.Generator().manual_seed(13))
    with torch.no_grad():
        y_ref = model(x)
    report = wrap_model(model, tol=1e-3)
    assert set(report.keys()) == {"0", "2", "4"}
    assert all(isinstance(a, Admission) for a in report.values())
    assert all(isinstance(m, DemonLinear) for m in (model[0], model[2], model[4]))
    assert not any(type(m) is nn.Linear for m in model.modules() if not isinstance(m, DemonLinear) and m is not model
                   and not isinstance(m, nn.ReLU) and not isinstance(m, nn.Sequential)
                   and m not in (model[0].linear, model[2].linear, model[4].linear))
    assert report["0"].admitted and model[0].served
    assert not report["2"].admitted and not report["4"].admitted
    assert report["0"] is model[0].admission
    with torch.no_grad():
        y = model(x)
    assert torch.allclose(y, y_ref, atol=1e-4, rtol=1e-4)


def test_wrap_model_nested_names():
    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = _mlp(seed=14)
            self.head = nn.Linear(10, 3)

    torch.manual_seed(14)
    net = Net()
    report = wrap_model(net, tol=1e-3)
    assert set(report.keys()) == {"body.0", "body.2", "body.4", "head"}
    assert isinstance(net.head, DemonLinear) and isinstance(net.body[0], DemonLinear)
    assert sum(1 for m in net.modules() if isinstance(m, DemonLinear)) == 4


def test_wrapped_model_backward():
    model = _mlp(seed=15, lowrank_first=True)
    wrap_model(model, tol=1e-3)
    assert model[0].served
    x = torch.randn(6, 64, generator=torch.Generator().manual_seed(16), requires_grad=True)
    model(x).sum().backward()
    assert x.grad is not None and torch.isfinite(x.grad).all()
    assert model[2].linear.weight.grad is not None       # fallback layers train normally


def test_wrap_model_is_idempotent():
    model = _mlp(seed=17)
    wrap_model(model, tol=1e-3)
    report2 = wrap_model(model, tol=1e-3)
    assert report2 == {}, f"second wrap_model re-wrapped {sorted(report2)}"
    assert isinstance(model[0].linear, nn.Linear) and not isinstance(model[0].linear, DemonLinear)


def test_small_output_random_linear_is_not_served():
    """nn.Linear(128, 10) (an MLP classifier head): r_max = 2, the certified basis has all 10 rows,
    so the tolerance is met trivially but serving 10 components is no cheaper than the dense
    product -> REFUSE_NO_SPEEDUP and the original path is used bitwise."""
    torch.manual_seed(18)
    lin = nn.Linear(128, 10)
    dl = DemonLinear(lin, tol=1e-3)
    assert not dl.served
    assert dl.admission.status is Status.REFUSE_NO_SPEEDUP and dl.admission.rank == 10
    x = torch.randn(32, 128, generator=torch.Generator().manual_seed(19))
    with torch.no_grad():
        assert torch.equal(dl(x), lin(x))
