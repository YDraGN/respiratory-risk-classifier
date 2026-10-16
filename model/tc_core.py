"""tc_core.py - lõi dùng chung cho model nhận ngưỡng T ("threshold-conditioned", viết tắt tc).

    f(60 phút lịch sử, T, baseline) -> logit của P(SpO2 < T liên tục >= 3 phút trong 30 phút tới)

Module này là "nguồn sự thật" duy nhất cho: định nghĩa sự kiện (có làm sạch bằng HR), đặc trưng tương đối
so với T, chuỗi đầu vào, chuẩn hóa, và cách lấy mẫu T khi train. Notebook data/model và firmware phải khớp
đúng các hàm ở đây.

Hợp đồng dữ liệu `dataset_tc/{train,val,test}.npz` (do 03_windowing + 04_splitting sinh ra):
    X               (n, 60, 4) float32   kênh = spo2_mean, spo2_min, spo2_valid, pulse_mean (đơn vị gốc, NaN = thiếu)
    T_list          (K,)       int       các ngưỡng đã gắn nhãn, ví dụ 86..94 (gồm cả 89, 91 để kiểm tra nội suy)
    Y               (n, K)     int8      Y[i, j] = 1 nếu có sự kiện < T_list[j] trong 30 phút tới
    OK              (n, K)     bool      OK[i, j] = đầu vào 60 phút chưa có sự kiện < T_list[j]
    baseline        (n,)       float32   trung vị spo2_mean 6-24 giờ trước cửa sổ (NaN nếu không đủ dữ liệu)
    baseline_hours  (n,)       float32   số giờ dữ liệu hợp lệ dùng để tính baseline
    pid, rec, start (n,)                 bệnh nhân, record, phút bắt đầu cửa sổ
"""
import os, warnings, numpy as np, pandas as pd

IN_MIN, HORIZON, STRIDE, MIN_EVENT = 60, 30, 5, 3
CH = ["spo2_mean", "spo2_min", "spo2_valid", "pulse_mean"]
T_TRAIN = (86, 87, 88, 90, 92, 93, 94)
T_HOLDOUT = (89, 91)                                    # không train, chỉ để kiểm tra nội suy
T_ALL = tuple(sorted(T_TRAIN + T_HOLDOUT))
T_RANGE = (85, 95)                                      # khoảng T hợp lệ gửi lên web/firmware
FB_GRID = np.arange(80, 101)                            # ngưỡng nguyên của bảng frac_below dựng sẵn (cần tới T+4+1)
PULSE_TOL = 10.0                                        # D2: phút có |PULSE-HR| > 10 hoặc mất mạch là phút không sạch
BASE_MIN_HOURS = 1.0                                    # baseline dựa trên < 1 giờ dữ liệu thì coi như không có


# ======================================================================================
# 1. Nhãn
# ======================================================================================
def runs(mask):
    """Các đoạn True liên tiếp: trả về (start, end) với end không tính."""
    d = np.diff(np.r_[0, np.asarray(mask, int), 0])
    return np.where(d == 1)[0], np.where(d == -1)[0]

def clean_minutes(pulse, hr=None, tol=PULSE_TOL):
    """Phút "sạch" để xác nhận sự kiện (D2): có mạch từ SpO2, và nếu có HR từ ECG thì |pulse - hr| <= tol.
    Phút thiếu HR không bị phạt (không phải record nào cũng có ECG)."""
    pu = np.asarray(pulse, float); ok = np.isfinite(pu)
    if hr is not None:
        d = np.abs(pu - np.asarray(hr, float)); ok &= ~(np.isfinite(d) & (d > tol))
    return ok

def event_mask(spo2, T, min_len=MIN_EVENT, pulse=None, hr=None, tol=PULSE_TOL, return_noise=False):
    """True cho mọi phút thuộc một đoạn spo2 < T liên tục >= min_len phút.
    Nếu có `pulse` (và `hr` từ ECG): đoạn chỉ được tính là sự kiện khi có >= min_len phút sạch (clean_minutes);
    ngược lại coi là nhiễu (mạch mất tín hiệu hoặc lệch HR trong lúc SpO2 tụt). HR chỉ dùng để làm sạch nhãn.
    Quy tắc đếm phút sạch (không dùng tỷ lệ) bảo đảm nhãn LỒNG NHAU theo T: đoạn < T nằm trong đoạn < T' > T,
    nên có sự kiện ở T thì chắc chắn có sự kiện ở T'."""
    spo2 = np.asarray(spo2, float)
    low = np.nan_to_num(spo2, nan=100) < T
    cm = np.r_[0, np.cumsum(clean_minutes(pulse, hr, tol))] if pulse is not None else None
    ev, noise = np.zeros(len(spo2), bool), 0
    for s, e in zip(*runs(low)):
        if e - s < min_len: continue
        if cm is not None and cm[e] - cm[s] < min_len: noise += 1; continue
        ev[s:e] = True
    return (ev, noise) if return_noise else ev

def event_onsets(spo2, T, **kw):
    """Phút bắt đầu của từng sự kiện (cùng định nghĩa với event_mask)."""
    return runs(event_mask(spo2, T, **kw))[0]


# ======================================================================================
# 2. Dữ liệu
# ======================================================================================
def load_split(data_dir, k):
    z = np.load(f"{data_dir}/{k}.npz", allow_pickle=False)
    d = {c: z[c] for c in z.files}
    d["T_list"] = d["T_list"].astype(int); n, K = len(d["X"]), len(d["T_list"])
    assert d["X"].shape[1:] == (IN_MIN, len(CH)), d["X"].shape
    assert d["Y"].shape == d["OK"].shape == (n, K), (d["Y"].shape, d["OK"].shape)
    d["Y"], d["OK"] = d["Y"].astype(np.int8), d["OK"].astype(bool)
    # nhãn lồng nhau: có sự kiện < T thì cũng có sự kiện < T' > T (cửa sổ phải OK ở cả hai T)
    both = d["OK"][:, :-1] & d["OK"][:, 1:]
    viol = (d["Y"][:, :-1] > d["Y"][:, 1:]) & both
    if viol.any(): warnings.warn(f"{k}: {int(viol.sum())} cặp nhãn không đơn điệu theo T, kiểm tra 03_windowing")
    return d

def col(d, T):
    """Chỉ số cột của T trong d['T_list']."""
    i = np.where(d["T_list"] == T)[0]; assert len(i), f"T={T} không có trong {d['T_list']}"; return int(i[0])

def label_table(splits, T_list=T_ALL):
    """Bảng số cửa sổ OK, số dương, tỷ lệ dương và số bệnh nhân dương theo T, cho từng tập."""
    rows = []
    for k, d in splits.items():
        for T in T_list:
            j = col(d, T); ok = d["OK"][:, j]; y = d["Y"][ok, j]
            rows.append(dict(split=k, T=T, train_T=T in T_TRAIN, n_ok=int(ok.sum()), n_pos=int(y.sum()),
                             prevalence=y.mean() if len(y) else np.nan, pat_pos=len(np.unique(d["pid"][ok][y == 1]))))
    return pd.DataFrame(rows)


# ======================================================================================
# 3. Đặc trưng bảng
# ======================================================================================
TAB_REL = ["sp_mean_rT", "sp_p10_rT", "mn_min60_rT", "mn_min10_rT", "sp_last5_rT", "frac_lt_T", "frac_lt_T2", "frac_lt_T4"]
TAB_TREND = ["sp_slope60", "sp_slope30", "sp_d_last10_first30", "sp_std60", "sp_std15"]
TAB_PULSE = ["pu_mean", "pu_slope30", "pu_d_last10_first30", "pu_std60"]
TAB_BASE = ["base_rT", "last5_rbase", "base_hours"]
TAB_QUAL = ["valid_mean", "sp_nan_frac"]
TAB_THR = ["T"]
GROUPS = dict(rel=TAB_REL, trend=TAB_TREND, pulse=TAB_PULSE, base=TAB_BASE, qual=TAB_QUAL, thr=TAB_THR)
LEVELS = ["sp_mean", "sp_p10", "mn_min60", "mn_min10", "sp_last5"]       # mức tuyệt đối, sẽ trừ T
ABS_FRAC = (95, 92, 90)                                                   # ablation "tuyệt đối": ngưỡng cố định như bản cũ
# chiều ảnh hưởng theo T: +1 = tăng khi đặc trưng tăng. Dùng cho monotone_constraints của LightGBM.
MONO = {**{c: -1 for c in TAB_REL[:5]}, "frac_lt_T": 1, "frac_lt_T2": 1, "frac_lt_T4": 1, "base_rT": -1, "T": 1}

def tab_cols(use_base=True, use_pulse=True):
    return [c for g, cs in GROUPS.items() for c in cs
            if (use_base or g != "base") and (use_pulse or g != "pulse")]

def slope(Y, lo):
    """Độ dốc hồi quy tuyến tính (đơn vị/phút) trên Y[:, lo:], bỏ qua NaN."""
    y = Y[:, lo:]; t = np.arange(y.shape[1], dtype=np.float64); m = ~np.isnan(y)
    n, yy = m.sum(1), np.where(m, y, 0).astype(np.float64)
    Sx, Sy, Sxy, Sxx = (m * t).sum(1), yy.sum(1), (yy * t).sum(1), (m * t * t).sum(1)
    den = n * Sxx - Sx ** 2
    with np.errstate(all="ignore"): s = (n * Sxy - Sx * Sy) / den
    s[(n < 5) | (den == 0)] = np.nan
    return s

def frac_below(SP, thr):
    """Tỷ lệ phút hợp lệ có SpO2 < thr. thr: số hoặc mảng (n,)."""
    v = ~np.isnan(SP); thr = np.asarray(thr, float).reshape(-1, 1) if np.ndim(thr) else thr
    return ((SP < thr) & v).sum(1) / np.maximum(v.sum(1), 1)

def fill_baseline(baseline, base_hours, sp_mean):
    """Không có baseline (NaN hoặc < BASE_MIN_HOURS giờ) -> dùng SpO2 trung bình của chính cửa sổ, giờ = 0.
    Firmware làm y hệt khi mới đeo."""
    b = np.asarray(baseline, float).copy(); h = np.nan_to_num(np.asarray(base_hours, float), nan=0.0).copy()
    miss = ~np.isfinite(b) | (h < BASE_MIN_HOURS)
    b[miss], h[miss] = sp_mean[miss], 0.0
    return b, h

def base_stats(X, baseline, base_hours):
    """Phần KHÔNG phụ thuộc T, tính một lần cho mỗi cửa sổ. Ghép với T bằng tab_from_stats (nhanh khi train)."""
    SP, MN, VA, PU = (X[:, :, i].astype(np.float64) for i in range(4))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lv = dict(sp_mean=np.nanmean(SP, 1), sp_p10=np.nanpercentile(SP, 10, axis=1), mn_min60=np.nanmin(MN, 1),
                  mn_min10=np.nanmin(MN[:, 50:], 1), sp_last5=np.nanmean(SP[:, 55:], 1))
        inv = dict(sp_slope60=slope(SP, 0), sp_slope30=slope(SP, 30),
                   sp_d_last10_first30=np.nanmean(SP[:, 50:], 1) - np.nanmean(SP[:, :30], 1),
                   sp_std60=np.nanstd(SP, 1), sp_std15=np.nanstd(SP[:, 45:], 1),
                   pu_mean=np.nanmean(PU, 1), pu_slope30=slope(PU, 30),
                   pu_d_last10_first30=np.nanmean(PU[:, 50:], 1) - np.nanmean(PU[:, :30], 1), pu_std60=np.nanstd(PU, 1),
                   valid_mean=np.nanmean(VA, 1), sp_nan_frac=np.isnan(SP).mean(1))
    b, h = fill_baseline(baseline, base_hours, lv["sp_mean"])
    inv["last5_rbase"] = lv["sp_last5"] - b; inv["base_hours"] = h
    fb = np.stack([frac_below(SP, t) for t in FB_GRID], 1).astype(np.float32)
    return dict(lv=pd.DataFrame(lv).astype("float32"), inv=pd.DataFrame(inv).astype("float32"),
                fb=fb, base=b.astype("float32"))

def _assemble(lv, inv, base, T, fr, rel, cols):
    T = np.broadcast_to(np.asarray(T, np.float32), (len(lv),))
    sh = T if rel else np.zeros_like(T)
    f = {f"{c}_rT": lv[c].to_numpy() - sh for c in LEVELS}
    f.update(frac_lt_T=fr[0], frac_lt_T2=fr[1], frac_lt_T4=fr[2], base_rT=base - sh, T=T)
    f.update({c: inv[c].to_numpy() for c in inv.columns})
    return pd.DataFrame({c: f[c] for c in cols}).astype("float32")

def tab_from_stats(st, T, idx=None, rel=True, use_base=True, use_pulse=True):
    """Đặc trưng bảng cho các cửa sổ `idx` tại ngưỡng T (số nguyên hoặc mảng số nguyên cùng độ dài idx)."""
    idx = np.arange(len(st["base"])) if idx is None else np.asarray(idx)
    T = np.broadcast_to(np.asarray(T), (len(idx),))
    assert np.all(T == np.round(T)), "tab_from_stats chỉ nhận T nguyên; T lẻ dùng make_features"
    if rel: offs = (0, 2, 4); fr = [st["fb"][idx, (T + o - FB_GRID[0]).astype(int)] for o in offs]
    else: fr = [st["fb"][idx, np.full(len(idx), t - FB_GRID[0])] for t in ABS_FRAC]
    return _assemble(st["lv"].iloc[idx].reset_index(drop=True), st["inv"].iloc[idx].reset_index(drop=True),
                     st["base"][idx], T, fr, rel, tab_cols(use_base, use_pulse))

def make_features(X, T, baseline, base_hours, rel=True, use_base=True, use_pulse=True):
    """Bản tham chiếu, nhận T bất kỳ (kể cả số lẻ). Firmware tính đúng như hàm này."""
    st = base_stats(X, baseline, base_hours); SP = X[:, :, 0].astype(np.float64)
    T = np.broadcast_to(np.asarray(T, float), (len(X),))
    fr = [frac_below(SP, T + o) for o in (0, 2, 4)] if rel else [frac_below(SP, t) for t in ABS_FRAC]
    return _assemble(st["lv"], st["inv"], st["base"], T, fr, rel, tab_cols(use_base, use_pulse))


# ======================================================================================
# 4. Chuỗi và chuẩn hóa
# ======================================================================================
def make_seq(X, T, mu, sd, rel=True):
    """(n,60,4) đơn vị gốc -> (n,60,6): kênh SpO2 trừ T (nếu rel), z-score, NaN -> 0, thêm mask của spo2_mean và pulse_mean."""
    Xr = X.astype(np.float32, copy=True)
    if rel: Xr[:, :, 0:2] -= np.broadcast_to(np.asarray(T, np.float32), (len(X),))[:, None, None]
    mask = np.isnan(X[:, :, [0, 3]]).astype(np.float32)
    return np.concatenate([np.nan_to_num((Xr - mu) / sd, nan=0.0).astype(np.float32), mask], axis=2)

def fit_norm(X, st, T_choices=T_TRAIN, rel=True, use_base=True, use_pulse=True, n_sample=60000, seed=0):
    """Thống kê z-score tính trên TRAIN, sau khi đã đổi sang giá trị tương đối (T rút ngẫu nhiên trong T_choices)."""
    rng = np.random.default_rng(seed); idx = np.sort(rng.choice(len(X), min(n_sample, len(X)), replace=False))
    T = rng.choice(np.asarray(T_choices), len(idx))
    Xr = X[idx].astype(np.float64)
    if rel: Xr[:, :, 0:2] -= T[:, None, None]
    mu, sd = np.nanmean(Xr, (0, 1)), np.nanstd(Xr, (0, 1)) + 1e-6
    F = tab_from_stats(st, T, idx, rel, use_base, use_pulse)
    med = F.median(); G = F.fillna(med)
    return dict(seq_mu=mu.astype(np.float32), seq_sd=sd.astype(np.float32), tab_cols=np.array(list(F.columns), dtype=str),
                tab_med=med.to_numpy(np.float32), tab_mu=G.mean().to_numpy(np.float32), tab_sd=(G.std() + 1e-6).to_numpy(np.float32),
                rel=rel, use_base=use_base, use_pulse=use_pulse)

def norm_tab(F, nm):
    v = F.to_numpy(np.float32)
    v = np.where(np.isnan(v), nm["tab_med"], v)
    return np.clip((v - nm["tab_mu"]) / nm["tab_sd"], -5, 5).astype(np.float32)

def inputs(d, st, nm, T, idx=None):
    """Đầu vào cuối {'seq', 'tab'} cho các cửa sổ idx tại ngưỡng T (số hoặc mảng)."""
    idx = np.arange(len(d["X"])) if idx is None else np.asarray(idx)
    T = np.broadcast_to(np.asarray(T), (len(idx),))
    seq = make_seq(d["X"][idx], T, nm["seq_mu"], nm["seq_sd"], bool(nm["rel"]))
    tab = norm_tab(tab_from_stats(st, T, idx, bool(nm["rel"]), bool(nm["use_base"]), bool(nm["use_pulse"])), nm)
    return {"seq": seq, "tab": tab}

def save_norm(path, nm):
    np.savez(path, **{k: np.asarray(v) for k, v in nm.items()})

def load_norm(path):
    z = np.load(path, allow_pickle=False); nm = {k: z[k] for k in z.files}
    for k in ("rel", "use_base", "use_pulse"): nm[k] = bool(nm[k])
    return nm

def stats_cache(d, path):
    """base_stats có lưu đĩa (tính nanpercentile trên vài trăm nghìn cửa sổ mất cỡ một phút)."""
    if os.path.exists(path):
        z = np.load(path, allow_pickle=True)
        st = dict(lv=pd.DataFrame(z["lv"], columns=LEVELS), inv=pd.DataFrame(z["inv"], columns=list(z["inv_cols"])),
                  fb=z["fb"], base=z["base"])
        if len(st["base"]) == len(d["X"]): return st
    st = base_stats(d["X"], d["baseline"], d["baseline_hours"])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez(path, lv=st["lv"].to_numpy(), inv=st["inv"].to_numpy(), inv_cols=np.array(st["inv"].columns), fb=st["fb"], base=st["base"])
    return st


# ======================================================================================
# 5. Lấy mẫu T khi train
# ======================================================================================
def sample_epoch(d, T_choices, neg_rate=1.0, rng=None, fixed_T=None):
    """Mỗi cửa sổ nhận MỘT T ngẫu nhiên (đều trên các T trong T_choices mà cửa sổ còn OK), không nhân bản dữ liệu.
    Giữ toàn bộ mẫu dương, giữ mẫu âm với xác suất neg_rate và gán trọng số 1/neg_rate.
    fixed_T: dùng cho model riêng từng T (ablation). Trả về (idx, T, y, w), đã xáo trộn."""
    rng = rng or np.random.default_rng()
    T_choices = np.asarray([fixed_T] if fixed_T is not None else T_choices)
    J = np.array([col(d, t) for t in T_choices]); ok = d["OK"][:, J]
    n_ok = ok.sum(1); has = np.where(n_ok > 0)[0]
    # chọn ngẫu nhiên một cột OK cho mỗi hàng: hạng ngẫu nhiên trong các cột OK
    r = rng.random((len(has), len(J))); r[~ok[has]] = -1
    jj = J[r.argmax(1)]; T = d["T_list"][jj]; y = d["Y"][has, jj]
    keep = (y == 1) | (rng.random(len(has)) < neg_rate)
    w = np.where(y == 1, 1.0, 1.0 / neg_rate).astype(np.float32)
    p = rng.permutation(keep.sum())
    return has[keep][p], T[keep][p], y[keep][p].astype(np.float32), w[keep][p]
