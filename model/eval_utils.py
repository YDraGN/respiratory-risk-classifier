"""eval_utils.py - bộ đánh giá dùng chung cho mọi mô hình (LightGBM, CNN, GRU, quy tắc...).

Quy trình:
    thr  = pick_thresholds(s_val, y_val)                  # ngưỡng chọn trên VAL
    card = evaluate("lgbm", s, y, thr, meta, s_val=..., y_val=...)   # s,y: tập đang xét (val hoặc test)
    report([card1, card2]) ; plot_curves([...]) ; plot_confusions([...], fa=1)

Mọi mô hình chỉ cần xuất 1 mảng điểm rủi ro `s` (càng cao càng nguy cơ) cho từng cửa sổ.

Model nhận ngưỡng T (bản tc): điểm là ma trận S (n, K) theo d["T_list"]; dùng by_T, thr_table, smooth_table,
monotonic_report và các hàm plot_*_by_T ở cuối file.
"""
import numpy as np, pandas as pd, matplotlib.pyplot as plt
from sklearn.metrics import (average_precision_score, roc_auc_score, precision_recall_curve,
                             roc_curve, brier_score_loss)
from sklearn.isotonic import IsotonicRegression

STRIDE = 5                         # phút giữa 2 cửa sổ (khớp 03_windowing)
FA_LEVELS = (0.04, 0.5, 1, 2)      # cảnh báo giả / giờ; 0.04/h ~ 1 báo động giả mỗi ngày


# ---------- nền tảng ----------
def neg_hours(y, stride=STRIDE):
    return (y == 0).sum() * stride / 60

def thr_for_fa(s, y, fa, stride=STRIDE):
    """Ngưỡng sao cho cảnh báo giả <= fa lần/giờ trên chính tập (s, y) đưa vào."""
    neg = np.sort(s[y == 0])[::-1]
    return neg[min(int(fa * neg_hours(y, stride)), len(neg) - 1)]

def pick_thresholds(s_val, y_val, fa_levels=FA_LEVELS, stride=STRIDE):
    """Chọn ngưỡng trên VAL. Dùng lại đúng các ngưỡng này cho test."""
    return {fa: float(thr_for_fa(s_val, y_val, fa, stride)) for fa in fa_levels}

def episode_ids(y, rec, start, gap=2 * STRIDE):
    """Gom các cửa sổ dương liên tiếp (cùng record) thành 1 'đợt sự kiện'. -1 = cửa sổ âm."""
    ep = np.full(len(y), -1)
    idx = np.where(y == 1)[0]
    if len(idx) == 0: return ep
    o = idx[np.lexsort((start[idx], rec[idx]))]
    new = np.r_[True, (rec[o][1:] != rec[o][:-1]) | (start[o][1:] - start[o][:-1] > gap)]
    ep[o] = np.cumsum(new) - 1
    return ep

def ece_quantile(y, p, bins=10):
    q = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    b = np.clip(np.searchsorted(q, p, side="right") - 1, 0, len(q) - 2)
    return sum((b == i).mean() * abs(y[b == i].mean() - p[b == i].mean()) for i in np.unique(b))


# ---------- tính chỉ số ----------
def _op(s, y, t, ep, hrs):
    p = s > t
    tp, fp = int((p & (y == 1)).sum()), int((p & (y == 0)).sum())
    fn, tn = int((~p & (y == 1)).sum()), int((~p & (y == 0)).sum())
    pos_ep = ep >= 0
    ev = pd.Series(p[pos_ep]).groupby(ep[pos_ep]).max().mean() if pos_ep.any() else np.nan
    return dict(thr=t, precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1),
                specificity=tn / max(tn + fp, 1), f1=2 * tp / max(2 * tp + fp + fn, 1),
                accuracy=(tp + tn) / len(y), event_recall=ev,
                FA_per_h=fp / hrs, FA_per_day=24 * fp / hrs, tn=tn, fp=fp, fn=fn, tp=tp)

def evaluate(name, s, y, thr, meta, s_val=None, y_val=None, is_prob=True, n_boot=200, seed=0):
    """meta: dict có 'pid', 'rec', 'start' (mảng cùng độ dài với y).
    s_val/y_val: dùng để fit isotonic (hiệu chuẩn) nếu is_prob=True."""
    s, y = np.asarray(s, float), np.asarray(y).astype(int)
    pid, rec, start = (np.asarray(meta[k]) for k in ("pid", "rec", "start"))
    ep, hrs = episode_ids(y, rec, start), neg_hours(y)
    ops = {fa: _op(s, y, t, ep, hrs) for fa, t in thr.items()}

    # khoảng tin cậy 95% của AUPRC, lấy mẫu lại theo bệnh nhân
    codes, uniq = pd.factorize(pid); idx_by = [np.where(codes == i)[0] for i in range(len(uniq))]
    rng, bs = np.random.default_rng(seed), []
    for _ in range(n_boot):
        ii = np.concatenate([idx_by[i] for i in rng.integers(0, len(uniq), len(uniq))])
        if y[ii].sum(): bs.append(average_precision_score(y[ii], s[ii]))
    lo, hi = np.percentile(bs, [2.5, 97.5])

    summ = dict(model=name, AUPRC=average_precision_score(y, s), AUPRC_CI=f"{lo:.3f}-{hi:.3f}",
                lift_vs_random=average_precision_score(y, s) / y.mean(), AUROC=roc_auc_score(y, s),
                prevalence=y.mean(), n_pos=int(y.sum()), n_patients=len(uniq))
    cal = None
    if is_prob:
        summ.update(Brier=brier_score_loss(y, s), ECE=ece_quantile(y, s))
        if s_val is not None:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(s_val, y_val)
            cal = iso.predict(s)
            summ.update(Brier_isotonic=brier_score_loss(y, cal), ECE_isotonic=ece_quantile(y, cal))
    return dict(name=name, s=s, y=y, summary=summ, ops=ops, ep=ep, hrs=hrs, is_prob=is_prob, cal=cal)


# ---------- bảng ----------
def report(cards, fa_show=None):
    """In 2 bảng: (1) chỉ số tổng quát, (2) chỉ số tại từng mức cảnh báo giả."""
    t1 = pd.DataFrame([c["summary"] for c in cards]).set_index("model")
    rows = {(c["name"], fa): {k: v for k, v in o.items() if k not in ("tn", "fp", "fn", "tp")}
            for c in cards for fa, o in c["ops"].items() if fa_show is None or fa in fa_show}
    t2 = pd.DataFrame(rows).T; t2.index.names = ["model", "FA/giờ"]
    return t1, t2


# ---------- biểu đồ ----------
def plot_curves(cards, title=""):
    """Nhóm 1 (phân biệt): PR, ROC, độ nhạy theo FA/giờ.  Nhóm 2 (tin cậy): reliability."""
    fig, ax = plt.subplots(1, 4, figsize=(22, 4.6))
    for c in cards:
        s, y, n = c["s"], c["y"], c["name"]
        p, r, _ = precision_recall_curve(y, s); ax[0].plot(r, p, label=f"{n} ({c['summary']['AUPRC']:.3f})")
        f, t, _ = roc_curve(y, s); ax[1].plot(f, t, label=f"{n} ({c['summary']['AUROC']:.3f})")
        neg = np.sort(s[y == 0])[::-1]; fas = np.geomspace(0.02, 5, 40)
        thrs = [neg[min(int(f_ * c["hrs"]), len(neg) - 1)] for f_ in fas]
        ln, = ax[2].plot(fas, [(s > t_)[y == 1].mean() for t_ in thrs], label=f"{n} (cửa sổ)")
        pe = c["ep"] >= 0
        ax[2].plot(fas, [pd.Series((s > t_)[pe]).groupby(c["ep"][pe]).max().mean() for t_ in thrs],
                   "--", color=ln.get_color(), label=f"{n} (đợt sự kiện)")
        if c["is_prob"]:
            q = np.unique(np.quantile(s, np.linspace(0, 1, 11)))
            b = np.clip(np.searchsorted(q, s, side="right") - 1, 0, len(q) - 2)
            xs = [s[b == i].mean() for i in np.unique(b)]; ys = [y[b == i].mean() for i in np.unique(b)]
            ax[3].plot(xs, ys, "o-", color=ln.get_color(), label=n)
    ax[0].axhline(cards[0]["y"].mean(), ls="--", c="gray"); ax[0].set(xlabel="Recall", ylabel="Precision", title="Đường PR")
    ax[1].plot([0, 1], [0, 1], "--", c="gray"); ax[1].set(xlabel="1 - Specificity", ylabel="Recall", title="Đường ROC")
    ax[2].set(xscale="log", xlabel="Cảnh báo giả / giờ", ylabel="Độ nhạy", title="Độ nhạy theo mức cảnh báo giả")
    for fa, ls in ((0.04, ":"),):  # mốc ~1 báo động giả/ngày
        ax[2].axvline(fa, ls=ls, c="k", lw=.8); ax[2].text(fa, .02, " ~1/ngày", fontsize=8)
    m = max(cards[0]["y"].mean() * 8, 0.05)
    ax[3].plot([0, m], [0, m], "--", c="gray"); ax[3].set(xscale="log", yscale="log", xlabel="Xác suất dự đoán (trung bình bin)",
                                                           ylabel="Tỷ lệ dương thực", title="Hiệu chuẩn (reliability)")
    for a in ax: a.legend(fontsize=8)
    fig.suptitle(title); plt.tight_layout(); plt.show()

def plot_confusions(cards, fa=1):
    """Nhóm 3 (điểm vận hành): ma trận nhầm lẫn của từng mô hình tại mức cảnh báo giả `fa`."""
    fig, ax = plt.subplots(1, len(cards), figsize=(4.8 * len(cards), 4.4), squeeze=False)
    for a, c in zip(ax[0], cards):
        o = c["ops"][fa]; cm = np.array([[o["tn"], o["fp"]], [o["fn"], o["tp"]]])
        a.imshow(cm / cm.sum(1, keepdims=True), cmap="Blues", vmin=0, vmax=1)
        for i in range(2):
            for j in range(2): a.text(j, i, f"{cm[i, j]:,}\n({cm[i, j] / cm[i].sum():.1%})", ha="center", va="center")
        a.set_xticks([0, 1]); a.set_xticklabels(["dự đoán âm", "dự đoán dương"])
        a.set_yticks([0, 1]); a.set_yticklabels(["thực âm", "thực dương"])
        a.set_title(f"{c['name']} @ {fa} CBG/giờ\nprec {o['precision']:.2f} | recall {o['recall']:.2f} | F1 {o['f1']:.2f}")
    plt.tight_layout(); plt.show()


# ======================================================================================
# Đánh giá nhiều ngưỡng T (model nhận T). Điểm của mỗi model là ma trận S (n, K), cột j ứng với d["T_list"][j];
# chỉ các ô OK[i, j] được tính (đầu vào chưa có sự kiện < T). d: dict từ tc_core.load_split.
# ======================================================================================
def logit(p):
    p = np.clip(p, 1e-7, 1 - 1e-7); return np.log(p / (1 - p))

def _j(d, T): return int(np.where(d["T_list"] == T)[0][0])

def _by_pid(pid):
    codes, uniq = pd.factorize(pid); return [np.where(codes == i)[0] for i in range(len(uniq))]

def boot_auprc(y, s, pid, n_boot=200, seed=0):
    by, rng, out = _by_pid(pid), np.random.default_rng(seed), []
    for _ in range(n_boot):
        ii = np.concatenate([by[i] for i in rng.integers(0, len(by), len(by))])
        if y[ii].sum(): out.append(average_precision_score(y[ii], s[ii]))
    return np.percentile(out, [2.5, 97.5]) if out else (np.nan, np.nan)

def paired_diff(s1, s2, y, pid, n_boot=200, seed=0):
    """Khoảng tin cậy 95% của chênh AUPRC (s1 - s2), lấy mẫu lại theo bệnh nhân (cặp)."""
    by, rng, d = _by_pid(pid), np.random.default_rng(seed), []
    for _ in range(n_boot):
        ii = np.concatenate([by[i] for i in rng.integers(0, len(by), len(by))])
        if y[ii].sum(): d.append(average_precision_score(y[ii], s1[ii]) - average_precision_score(y[ii], s2[ii]))
    return np.percentile(d, [2.5, 97.5])

def by_T(name, S, d, T_list=None, n_boot=200, holdout=()):
    """Một dòng mỗi T: AUPRC (CI 95% bootstrap theo bệnh nhân), AUROC, tỷ lệ dương, hệ số so với ngẫu nhiên."""
    T_list = d["T_list"] if T_list is None else T_list; rows = []
    for T in T_list:
        j = _j(d, T); ok = d["OK"][:, j] & np.isfinite(S[:, j])
        y, s, pid = d["Y"][ok, j].astype(int), S[ok, j], d["pid"][ok]
        if y.sum() == 0: continue
        lo, hi = boot_auprc(y, s, pid, n_boot) if n_boot else (np.nan, np.nan)
        ap = average_precision_score(y, s)
        rows.append(dict(model=name, T=int(T), holdout=int(T) in holdout, AUPRC=ap, lo=lo, hi=hi, AUROC=roc_auc_score(y, s),
                         prevalence=y.mean(), lift=ap / y.mean(), n_pos=int(y.sum()), pat_pos=len(np.unique(pid[y == 1]))))
    return pd.DataFrame(rows)

def mean_auprc(S, d, T_list):
    """Trung bình AUPRC trên các T (tiêu chí dừng sớm)."""
    return float(np.mean([average_precision_score(d["Y"][d["OK"][:, j], j], S[d["OK"][:, j], j])
                          for j in (_j(d, T) for T in T_list)]))

def thr_table(S, d, T_list, fa_levels=FA_LEVELS):
    """Bảng ngưỡng xác suất theo T, chọn trên VAL theo báo giả/giờ ĐẾM THEO CỬA SỔ (chỉ là cận trên).
    Ngưỡng cho thiết bị chọn bằng sim_alert.thr_for_fa_day (cảnh báo thật sau persistence và cooldown)."""
    out = {}
    for T in T_list:
        j = _j(d, T); ok = d["OK"][:, j]
        out[int(T)] = pick_thresholds(S[ok, j], d["Y"][ok, j], fa_levels)
    return pd.DataFrame(out).T.rename_axis("T")

def smooth_table(tab, T_out=range(85, 96), method="isotonic"):
    """Làm mượt bảng T -> ngưỡng xác suất trên thang logit rồi nội suy cho mọi T nguyên trong T_out.
    isotonic: ép ngưỡng không giảm theo T (tỷ lệ sự kiện tăng theo T); linear: hồi quy tuyến tính theo T; none: nội suy thẳng.
    Ngoài khoảng T đã có: isotonic/none giữ giá trị biên, linear ngoại suy."""
    T = tab.index.to_numpy(float); Tq = np.asarray(list(T_out), float); out = {}
    for c in tab.columns:
        z = logit(tab[c].to_numpy(float))
        if method == "linear": a, b = np.polyfit(T, z, 1); zq = a * Tq + b
        else:
            zs = IsotonicRegression(increasing=True).fit(T, z).predict(T) if method == "isotonic" else z
            zq = np.interp(Tq, T, zs)
        out[c] = 1 / (1 + np.exp(-zq))
    return pd.DataFrame(out, index=pd.Index(Tq.astype(int), name="T"))

def fit_calibrators(S, d, T_list):
    """Isotonic theo từng T, CHỈ để hiển thị xác suất trên dashboard (không dùng cho quyết định cảnh báo)."""
    cal = {}
    for T in T_list:
        j = _j(d, T); ok = d["OK"][:, j]
        cal[int(T)] = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(S[ok, j], d["Y"][ok, j])
    return cal

def monotonic_report(P, T_list, tol=1e-6):
    """P: (n, K) xác suất của CÙNG cửa sổ ở các T (tính cho mọi cửa sổ, không lọc OK).
    Tỷ lệ cặp T kề nhau / mọi cặp (Ta < Tb) có P(Ta) > P(Tb) + tol, và tỷ lệ cửa sổ có ít nhất một vi phạm."""
    o = np.argsort(T_list); P = P[:, o]; K = P.shape[1]
    bad = P[:, :-1] > P[:, 1:] + tol
    allp = np.mean([(P[:, a] > P[:, b] + tol).mean() for a in range(K) for b in range(a + 1, K)])
    return dict(adjacent=float(bad.mean()), all_pairs=float(allp), windows_any=float(bad.any(1).mean()),
                max_drop=float(np.max(P[:, :-1] - P[:, 1:])))


# ---------- biểu đồ nhiều T ----------
def plot_auprc_by_T(df, holdout=(), title=""):
    """G1: AUPRC (CI 95%) theo T, mỗi model một màu; T không train tô nền xám. Kèm hệ số so với ngẫu nhiên."""
    fig, ax = plt.subplots(1, 2, figsize=(14, 4.4)); models = list(dict.fromkeys(df["model"]))
    for i, m in enumerate(models):
        g = df[df["model"] == m].sort_values("T"); x = g["T"] + (i - (len(models) - 1) / 2) * 0.08
        ax[0].errorbar(x, g["AUPRC"], yerr=[g["AUPRC"] - g["lo"], g["hi"] - g["AUPRC"]], fmt="o-", capsize=3, label=m)
        ax[1].plot(g["T"], g["lift"], "o-", label=m)
    g = df[df["model"] == models[0]].sort_values("T"); ax[0].plot(g["T"], g["prevalence"], "k:", label="ngẫu nhiên")
    for a in ax:
        for t in holdout: a.axvspan(t - .4, t + .4, color="gray", alpha=.15)
        a.set_xlabel("Ngưỡng T (%)"); a.legend(fontsize=8)
    ax[0].set(ylabel="AUPRC", title="AUPRC theo T (nền xám: T không train)"); ax[1].set(ylabel="AUPRC / tỷ lệ dương", title="Hệ số so với ngẫu nhiên")
    fig.suptitle(title); plt.tight_layout(); plt.show()

def plot_pr_roc_by_T(Sd, d, T_list, title=""):
    """G2: đường PR (hàng trên) và ROC (hàng dưới) cho từng T. Sd: {tên model: S (n, K)}."""
    K = len(T_list); fig, ax = plt.subplots(2, K, figsize=(3.1 * K, 6.2), squeeze=False)
    for c, T in enumerate(T_list):
        j = _j(d, T); ok = d["OK"][:, j]; y = d["Y"][ok, j]
        for n, S in Sd.items():
            s = S[ok, j]; p, r, _ = precision_recall_curve(y, s); f, t, _ = roc_curve(y, s)
            ax[0, c].plot(r, p, lw=1, label=f"{n} {average_precision_score(y, s):.3f}")
            ax[1, c].plot(f, t, lw=1, label=f"{n} {roc_auc_score(y, s):.3f}")
        ax[0, c].axhline(y.mean(), ls=":", c="gray"); ax[1, c].plot([0, 1], [0, 1], ":", c="gray")
        ax[0, c].set_title(f"T = {T}"); ax[0, c].legend(fontsize=6); ax[1, c].legend(fontsize=6); ax[1, c].set_xlabel("1 - Specificity")
    ax[0, 0].set_ylabel("Precision (PR)"); ax[1, 0].set_ylabel("Recall (ROC)")
    fig.suptitle(title); plt.tight_layout(); plt.show()

def plot_reliability_by_T(S, d, T_list, cal=None, bins=10, title=""):
    """G5: đường hiệu chuẩn theo từng T (bin theo phân vị, trục log). cal: dict T -> isotonic fit trên val."""
    fig, ax = plt.subplots(1, 2 if cal else 1, figsize=(13 if cal else 6.5, 4.6), squeeze=False); ax = ax[0]; lo_ = 1.0
    for T in T_list:
        j = _j(d, T); ok = d["OK"][:, j]; y, s = d["Y"][ok, j], S[ok, j]
        pairs = [(ax[0], s)] + ([(ax[1], cal[int(T)].predict(s))] if cal else [])
        for a, v in pairs:
            q = np.unique(np.quantile(v, np.linspace(0, 1, bins + 1)))
            b = np.clip(np.searchsorted(q, v, side="right") - 1, 0, max(len(q) - 2, 0))
            xs = [v[b == i].mean() for i in np.unique(b)]; ys = [y[b == i].mean() for i in np.unique(b)]
            a.plot(xs, ys, "o-", ms=3, label=f"T={T} (ECE {ece_quantile(y, v):.3f})"); lo_ = min(lo_, max(min(xs), 1e-4))
    for a, t in zip(ax, ("Xác suất của model", "Sau isotonic theo T (chỉ để hiển thị)")):
        a.plot([lo_, 1], [lo_, 1], "--", c="gray")
        a.set(xscale="log", yscale="log", xlabel="Xác suất dự đoán (trung bình bin)", ylabel="Tỷ lệ dương thực", title=t); a.legend(fontsize=7)
    fig.suptitle(title); plt.tight_layout(); plt.show()

def plot_monotonic(P, T_list, pid=None, n_show=12, seed=0, title=""):
    """G4: đường P theo T của vài cửa sổ mẫu (mỗi bệnh nhân một cửa sổ) + histogram độ giảm lớn nhất giữa hai T kề nhau."""
    o = np.argsort(T_list); T = np.asarray(T_list)[o]; P = P[:, o]; rng = np.random.default_rng(seed)
    hi = np.argsort(-P[:, -1])[:2000]                                    # ưu tiên cửa sổ có rủi ro đáng kể cho dễ nhìn
    if pid is not None: hi = hi[np.unique(pid[hi], return_index=True)[1]]
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.2))
    for i in rng.choice(hi, min(n_show, len(hi)), replace=False): ax[0].plot(T, P[i], "o-", ms=3, lw=1)
    ax[0].set(yscale="log", xlabel="Ngưỡng T (%)", ylabel="P(sự kiện < T)", title="P theo T của cửa sổ mẫu (phải không giảm)")
    drop = np.max(P[:, :-1] - P[:, 1:], 1); ax[1].hist(np.clip(drop, -0.02, None), bins=60)
    ax[1].axvline(0, c="r", ls="--")
    ax[1].set(yscale="log", xlabel="max(P(T) - P(T+1)) của mỗi cửa sổ (> 0 là vi phạm)", title="Vi phạm đơn điệu")
    fig.suptitle(title); plt.tight_layout(); plt.show()