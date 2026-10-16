"""Unit test cho tc_core và sim_alert. Chạy: python test_tc_core.py (trong thư mục model)."""
import numpy as np, pandas as pd
from collections import deque
import tc_core as C, sim_alert as SA


def fake_windows(n=300, seed=0):
    rng = np.random.default_rng(seed)
    sp = 95 + np.cumsum(rng.normal(0, .4, (n, 60)), 1) - rng.uniform(0, 8, (n, 1))
    sp[rng.random((n, 60)) < .05] = np.nan
    mn = sp - rng.uniform(0, 2, (n, 60)); va = np.where(np.isnan(sp), 0, rng.uniform(.5, 1, (n, 60)))
    pu = 80 + rng.normal(0, 5, (n, 60)); pu[rng.random((n, 60)) < .05] = np.nan
    X = np.stack([sp, mn, va, pu], 2).astype(np.float32)
    base = np.where(rng.random(n) < .2, np.nan, rng.uniform(90, 98, n)); bh = rng.uniform(0, 24, n)
    return X, base, bh


def test_relative_shift():
    """Đổi T một lượng dT: mọi đặc trưng *_rT và base_rT dịch đúng -dT, đặc trưng không phụ thuộc T giữ nguyên."""
    X, b, h = fake_windows()
    F1, F2 = C.make_features(X, 88, b, h), C.make_features(X, 91.5, b, h)
    for c in C.TAB_REL[:5] + ["base_rT"]:
        np.testing.assert_allclose(F2[c] - F1[c], -3.5, atol=1e-4, err_msg=c)
    for c in C.TAB_TREND + C.TAB_PULSE + C.TAB_QUAL + ["last5_rbase", "base_hours"]:
        np.testing.assert_array_equal(F1[c].to_numpy(), F2[c].to_numpy(), err_msg=c)
    assert (F2["frac_lt_T"] >= F1["frac_lt_T"]).all()                      # frac_below tăng theo T
    np.testing.assert_allclose(F2["T"] - F1["T"], 3.5)


def test_fast_path_matches_reference():
    X, b, h = fake_windows(); st = C.base_stats(X, b, h); rng = np.random.default_rng(1)
    T = rng.choice(np.array(C.T_ALL), len(X))
    for rel in (True, False):
        for ub in (True, False):
            A = C.tab_from_stats(st, T, rel=rel, use_base=ub); B = C.make_features(X, T, b, h, rel=rel, use_base=ub)
            assert list(A.columns) == list(B.columns)
            np.testing.assert_allclose(A.to_numpy(), B.to_numpy(), atol=1e-4, equal_nan=True)


def test_seq_shift():
    X, b, h = fake_windows(); mu = np.array([5, 4, .8, 80], np.float32); sd = np.array([3, 3, .2, 10], np.float32)
    S1, S2 = C.make_seq(X, 88, mu, sd), C.make_seq(X, 90, mu, sd)
    m = ~np.isnan(X[:, :, 0])
    np.testing.assert_allclose((S2 - S1)[:, :, 0][m], -2 / sd[0], atol=1e-5)
    np.testing.assert_array_equal(S1[:, :, 2:], S2[:, :, 2:])
    assert not np.isnan(S1).any() and S1.shape == (len(X), 60, 6)


def test_event_mask_noise():
    sp = np.r_[np.full(10, 97.), np.full(5, 85.), np.full(10, 97.), np.full(5, 85.), np.full(10, 97.)]
    pu = np.full(len(sp), 80.); hr = pu.copy()
    hr[25:30] = 120                                                       # đoạn 2: mạch lệch HR -> nhiễu
    pu[10:15] = np.nan                                                    # đoạn 1: mất mạch -> nhiễu
    assert C.event_mask(sp, 88).sum() == 10
    ev, nz = C.event_mask(sp, 88, pulse=pu, hr=hr, return_noise=True); assert ev.sum() == 0 and nz == 2
    hr[25:30] = 80; assert C.event_mask(sp, 88, pulse=pu, hr=hr).sum() == 5
    assert list(C.event_onsets(sp, 88)) == [10, 25]


def test_event_labels_nested_in_T():
    """Có sự kiện < T thì phải có sự kiện < T' > T (kể cả khi làm sạch bằng HR) -> nhãn đơn điệu theo T."""
    rng = np.random.default_rng(0)
    for _ in range(300):
        sp = 93 + np.cumsum(rng.normal(0, 1, 400)); sp[rng.random(400) < .05] = np.nan
        pu = 80 + rng.normal(0, 3, 400); pu[rng.random(400) < .3] = np.nan
        hr = pu + rng.choice([0, 0, 0, 25], 400)
        prev = None
        for T in C.T_ALL:
            ev = C.event_mask(sp, T, pulse=pu, hr=hr)
            if prev is not None: assert not (prev & ~ev).any(), T
            prev = ev


def _fire_ref(t, s, thr, k=1, n=1, cooldown=0):                          # bản deque cũ
    dq, fires, last = deque(), [], -10 ** 9
    for ti, fi in zip(t, s > thr):
        while dq and ti - dq[0][0] >= n * SA.STRIDE: dq.popleft()
        dq.append((ti, fi))
        if fi and ti - last >= cooldown and sum(f for _, f in dq) >= k: fires.append(ti); last = ti
    return np.array(fires, dtype=int)


def test_fire_times_matches_reference():
    rng = np.random.default_rng(0)
    for _ in range(200):
        t = np.sort(rng.choice(np.arange(0, 2000, 5), 150, replace=False)); s = rng.random(150)
        for k, n, cd in ((1, 1, 0), (2, 3, 30), (3, 4, 60), (4, 6, 0)):
            np.testing.assert_array_equal(SA.fire_times(t, s, .6, k, n, cd), _fire_ref(t, s, .6, k, n, cd))


def test_sample_epoch():
    rng = np.random.default_rng(0); n, T_list = 5000, np.array(C.T_ALL)
    lowest = rng.uniform(80, 99, n)                                       # SpO2 thấp nhất 30 phút tới
    Y = (lowest[:, None] < T_list[None]).astype(np.int8); OK = rng.random((n, len(T_list))) > .1
    d = dict(T_list=T_list, Y=Y, OK=OK)
    idx, T, y, w = C.sample_epoch(d, C.T_TRAIN, neg_rate=.5, rng=rng)
    assert set(np.unique(T)) <= set(C.T_TRAIN) and len(np.unique(idx)) == len(idx)
    j = np.searchsorted(T_list, T); assert OK[idx, j].all() and (Y[idx, j] == y).all()
    assert np.allclose(w[y == 0], 2.0) and np.allclose(w[y == 1], 1.0)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"): f(); print("OK ", name)
