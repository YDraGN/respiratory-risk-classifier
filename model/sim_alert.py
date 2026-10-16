"""sim_alert.py - mô phỏng tầng Watch / Alert / Critical trên timeline từng record.

Model chỉ cho điểm rủi ro mỗi 5 phút. Module này đặt lên trên điểm đó một lớp quyết định:
    ngưỡng  +  persistence (k lần vượt trong n lần đánh giá gần nhất)  +  cooldown
rồi đo: báo giả/ngày, tỷ lệ bắt được, thời gian báo trước, phân phối theo bệnh nhân.
Đây là số cảnh báo THẬT; báo giả đếm theo cửa sổ (eval_utils) chỉ là cận trên.

Quy ước thời gian (phút, theo chỉ số của clean_1min):
    - Cửa sổ có start s -> thời điểm dự đoán t = s + 60.
    - Sự kiện tại ngưỡng T = SpO2 < T liên tục >= 3 phút (tc_core.event_mask); onset e = phút đầu tiên của đoạn.
      clean_events=True: bỏ các đoạn mà mạch mất tín hiệu hoặc lệch HR ECG > 10 (D2), khớp nhãn khi train.
    - Cảnh báo tại t là ĐÚNG nếu có onset trong [t, t + 30); thời gian báo trước = e - t.

Tầng Critical luôn là LUẬT (không ML): SpO2 < crit_T liên tục n_min phút với SQI tốt, hoặc mạch ngoài [hr_low, hr_high]
liên tục n_min phút (critical_times / critical_stats).
"""
import numpy as np, pandas as pd, matplotlib.pyplot as plt
import tc_core as C

IN_MIN, HORIZON, STRIDE = 60, 30, 5
SPO2_THR, MIN_EVENT = 88, 3          # mặc định của bản cũ (ngưỡng cố định 88%)


# ---------- dựng timeline ----------
def event_onsets(spo2, thr=SPO2_THR, min_len=MIN_EVENT, pulse=None, hr=None):
    return C.event_onsets(spo2, thr, min_len=min_len, pulse=pulse, hr=hr)

def load_records(recs, clean_dir):
    """Đọc clean_1min một lần cho mọi record (dùng lại khi dựng timeline cho nhiều T)."""
    out = {}
    for r in np.unique(recs):
        d = pd.read_parquet(f"{clean_dir}/{r}.parquet")
        out[r] = {c: d[c].to_numpy(float) if c in d else np.full(len(d), np.nan)
                  for c in ("spo2_mean", "spo2_valid", "pulse_mean", "hr_mean")}
    return out

def build_timelines(scores, meta, clean_dir=None, T=SPO2_THR, cache=None, clean_events=False, mask=None):
    """scores: điểm rủi ro cho từng cửa sổ (cùng thứ tự với meta). meta: dict có 'pid', 'rec', 'start'.
    T: ngưỡng định nghĩa sự kiện. cache: kết quả load_records (nếu không có thì đọc từ clean_dir).
    mask: chỉ giữ các cửa sổ này (ví dụ OK[:, T] - đầu vào chưa có sự kiện < T).
    Trả về dict rec -> {pid, T, t, s, onsets, spo2, valid, pulse, valid_h}."""
    rec, start, pid = (np.asarray(meta[k]) for k in ("rec", "start", "pid"))
    scores = np.asarray(scores, float)
    if mask is not None: rec, start, pid, scores = rec[mask], start[mask], pid[mask], scores[mask]
    cache = cache if cache is not None else load_records(rec, clean_dir)
    tl = {}
    for r in np.unique(rec):
        i = np.where(rec == r)[0]; o = i[np.argsort(start[i])]; c = cache[r]; sp = c["spo2_mean"]
        ons = (C.event_onsets(sp, T, pulse=c["pulse_mean"], hr=c["hr_mean"]) if clean_events else C.event_onsets(sp, T))
        tl[r] = dict(pid=pid[o[0]], T=T, t=start[o].astype(int) + IN_MIN, s=scores[o], onsets=ons, spo2=sp,
                     valid=c["spo2_valid"], pulse=c["pulse_mean"], valid_h=np.isfinite(sp).sum() / 60)
    return tl


# ---------- lớp quyết định ----------
def fire_times(t, s, thr, k=1, n=1, cooldown=0):
    """Cảnh báo phát ra khi: điểm hiện tại > thr, và trong n lần đánh giá gần nhất (cửa sổ thời gian n*5 phút)
    có >= k lần vượt ngưỡng, và đã qua `cooldown` phút kể từ lần phát trước. (Bản vector hóa, cùng kết quả bản deque.)"""
    t = np.asarray(t); exc = np.asarray(s) > thr
    if not exc.any(): return np.zeros(0, dtype=int)
    ce = np.r_[0, np.cumsum(exc)]
    lo = np.searchsorted(t, t - n * STRIDE, side="right")              # điểm còn trong cửa sổ: t0 > ti - n*STRIDE
    cand = np.where(exc & (ce[1:] - ce[lo] >= k))[0]
    fires, last = [], -10 ** 9
    for i in cand:
        if t[i] - last >= cooldown: fires.append(t[i]); last = t[i]
    return np.array(fires, dtype=int)

def run_stream(tl, thr, k=1, n=1, cooldown=0, min_lead=5, min_hours=12):
    """Mô phỏng một luồng cảnh báo trên mọi record; trả về (tóm tắt, bảng theo bệnh nhân, thời gian báo trước).
    thr: một số, hoặc dict rec -> ngưỡng (khi mỗi bệnh nhân có T riêng)."""
    per = {}; leads = []
    tot = dict(alerts=0, tp=0, ev=0, ev_eval=0, ev_caught=0, ev_useful=0)
    for r, d in tl.items():
        t, ons = d["t"], d["onsets"]
        f = fire_times(t, d["s"], thr[r] if isinstance(thr, dict) else thr, k, n, cooldown)
        if len(f) and len(ons):                         # cảnh báo đúng/giả
            j = np.searchsorted(ons, f, side="left")
            nxt = np.where(j < len(ons), ons[np.minimum(j, len(ons) - 1)], 10 ** 9)
            tp = int(((nxt - f) < HORIZON).sum())
        else: tp = 0
        ev_eval = ev_caught = ev_useful = 0
        for e in ons:                                   # bắt được sự kiện?
            i = np.searchsorted(t, e - HORIZON, side="right")       # điểm đánh giá đầu tiên trong (e-30, e]
            if i < len(t) and t[i] <= e: ev_eval += 1
            else: continue
            j = np.searchsorted(f, e - HORIZON, side="right")
            if j < len(f) and f[j] <= e:
                ev_caught += 1; leads.append(e - f[j]); ev_useful += int(e - f[j] >= min_lead)
        hrs = len(t) * STRIDE / 60
        p = per.setdefault(d["pid"], dict(hours=0., alerts=0, fa=0, events=0))
        p["hours"] += hrs; p["alerts"] += len(f); p["fa"] += len(f) - tp; p["events"] += len(ons)
        for a, b in (("alerts", len(f)), ("tp", tp), ("ev", len(ons)), ("ev_eval", ev_eval),
                     ("ev_caught", ev_caught), ("ev_useful", ev_useful)): tot[a] += b
    pt = pd.DataFrame(per).T; pt["fa_per_day"] = pt["fa"] / pt["hours"].clip(lower=1e-9) * 24
    ok = pt[pt["hours"] >= min_hours]; hrs_all = pt["hours"].sum()
    fa_sorted = ok["fa"].sort_values(ascending=False); top = max(int(np.ceil(0.1 * len(ok))), 1)
    leads = np.array(leads)
    summ = dict(alerts_per_day=tot["alerts"] / hrs_all * 24, FA_per_day=(tot["alerts"] - tot["tp"]) / hrs_all * 24,
                precision=tot["tp"] / max(tot["alerts"], 1),
                caught_any=tot["ev_caught"] / max(tot["ev_eval"], 1), caught_useful=tot["ev_useful"] / max(tot["ev_eval"], 1),
                missed=1 - tot["ev_caught"] / max(tot["ev_eval"], 1),
                lead_med=np.median(leads) if len(leads) else np.nan, lead_p25=np.percentile(leads, 25) if len(leads) else np.nan,
                FA_day_pt_med=ok["fa_per_day"].median(), FA_day_pt_p90=ok["fa_per_day"].quantile(.9),
                FA_day_pt_max=ok["fa_per_day"].max(), top10pct_share=fa_sorted.iloc[:top].sum() / max(fa_sorted.sum(), 1),
                events=tot["ev"], evaluable=tot["ev_eval"], patients=len(ok), hours=hrs_all)
    return summ, pt, leads

def thr_for_fa_day(tl, fa_day, k=1, n=1, cooldown=0, min_lead=5, n_cand=400):
    """Ngưỡng NHỎ NHẤT sao cho báo giả/ngày thật (sau persistence + cooldown) <= fa_day trên timeline này.
    Gọi trên timeline của VAL, rồi giữ nguyên ngưỡng khi áp lên test. Tìm nhị phân trên phân vị của điểm."""
    s = np.concatenate([d["s"] for d in tl.values()])
    cand = np.unique(np.quantile(s, 1 - np.geomspace(1e-5, 0.5, n_cand)))   # vùng điểm cao, dày ở đuôi
    fa = lambda th: run_stream(tl, th, k, n, cooldown, min_lead)[0]["FA_per_day"]
    lo, hi = 0, len(cand) - 1
    if fa(cand[hi]) > fa_day: return float(cand[hi])
    while lo < hi:                                       # FA/ngày gần như giảm theo ngưỡng
        mid = (lo + hi) // 2
        if fa(cand[mid]) <= fa_day: hi = mid
        else: lo = mid + 1
    return float(cand[lo])

def curve(tl, thrs, k=1, n=1, cooldown=0, min_lead=5):
    """Đường báo giả/ngày - tỷ lệ bắt được sự kiện khi quét ngưỡng (G3)."""
    return pd.DataFrame([dict(thr=th, **run_stream(tl, th, k, n, cooldown, min_lead)[0]) for th in thrs])


# ---------- tầng Critical (luật) ----------
def critical_times(d, crit_T=SPO2_THR, n_min=MIN_EVENT, sqi_min=0.5, hr_low=None, hr_high=None, cooldown=30):
    """Thời điểm luật Critical phát: SpO2 < crit_T liên tục n_min phút mà các phút đó có spo2_valid >= sqi_min,
    hoặc mạch < hr_low / > hr_high liên tục n_min phút. Phát ở phút thứ n_min của đoạn, gộp theo cooldown."""
    sp, va, pu = d["spo2"], np.nan_to_num(d["valid"], nan=0.0), d["pulse"]
    trig = []
    low = (np.nan_to_num(sp, nan=100) < crit_T) & (va >= sqi_min)
    trig += [("spo2", s + n_min - 1) for s, e in zip(*C.runs(low)) if e - s >= n_min]
    if hr_low is not None or hr_high is not None:
        out, fin = np.zeros(len(pu), bool), np.isfinite(pu)        # phút thiếu mạch không tính là bất thường
        if hr_low is not None: out |= fin & (np.nan_to_num(pu) < hr_low)
        if hr_high is not None: out |= fin & (np.nan_to_num(pu) > hr_high)
        trig += [("hr", s + n_min - 1) for s, e in zip(*C.runs(out)) if e - s >= n_min]
    trig.sort(key=lambda x: x[1]); fires, last = [], -10 ** 9
    for why, ti in trig:
        if ti - last >= cooldown: fires.append((ti, why)); last = ti
    return fires

def critical_stats(tl, cooldown=30, min_hours=12, crit_T=None, n_min=MIN_EVENT, sqi_min=0.5, hr_low=None, hr_high=None):
    """Gánh nặng thông báo của tầng Critical (luật), theo bệnh nhân.
    crit_T=None: hành vi cũ (đếm onset sự kiện của timeline, gộp theo cooldown)."""
    per = {}
    for d in tl.values():
        if crit_T is None:
            ons = d["onsets"]; ons = ons[np.r_[True, np.diff(ons) >= cooldown]] if len(ons) else ons; n_sp, n_hr = len(ons), 0
        else:
            f = critical_times(d, crit_T, n_min, sqi_min, hr_low, hr_high, cooldown)
            n_sp, n_hr = sum(w == "spo2" for _, w in f), sum(w == "hr" for _, w in f)
        p = per.setdefault(d["pid"], dict(hours=0., n=0, n_spo2=0, n_hr=0))
        p["hours"] += d["valid_h"]; p["n"] += n_sp + n_hr; p["n_spo2"] += n_sp; p["n_hr"] += n_hr
    pt = pd.DataFrame(per).T; pt["per_day"] = pt["n"] / pt["hours"].clip(lower=1e-9) * 24
    ok = pt[pt["hours"] >= min_hours]; H = pt["hours"].sum()
    return dict(alerts_per_day=pt["n"].sum() / H * 24, spo2_per_day=pt["n_spo2"].sum() / H * 24, hr_per_day=pt["n_hr"].sum() / H * 24,
                FA_day_pt_med=ok["per_day"].median(), FA_day_pt_p90=ok["per_day"].quantile(.9), FA_day_pt_max=ok["per_day"].max(),
                pct_patients_any=(ok["n"] > 0).mean(), n_events=int(pt["n"].sum()), patients=len(ok))


# ---------- quét tham số ----------
def sweep(tl, thr_map, ks_ns=((1, 1), (2, 3), (3, 4), (4, 6)), cooldowns=(30, 60), min_lead=5):
    """thr_map: {nhãn ngân sách -> ngưỡng}. Có thể là eval_utils.pick_thresholds(...) (theo cửa sổ),
    hoặc {fa_day: thr_for_fa_day(...)} (theo cảnh báo thật)."""
    rows = []
    for fa, thr in thr_map.items():
        for (k, n) in ks_ns:
            for cd in cooldowns:
                s, _, _ = run_stream(tl, thr, k, n, cd, min_lead)
                rows.append(dict(fa_budget=fa, thr=thr, k=k, n=n, cooldown=cd, **s))
    return pd.DataFrame(rows)

def sweep_fa_day(tl, fa_days=(0.5, 1, 2, 3), ks_ns=((1, 1), (2, 3), (3, 4)), cooldowns=(30, 60), min_lead=5):
    """Với mỗi (k, n, cooldown): chọn ngưỡng đạt đúng ngân sách báo giả/ngày thật rồi đo độ nhạy.
    So sánh các cấu hình persistence ở CÙNG mức báo giả (công bằng hơn sweep theo ngưỡng cố định)."""
    rows = []
    for (k, n) in ks_ns:
        for cd in cooldowns:
            for fa in fa_days:
                th = thr_for_fa_day(tl, fa, k, n, cd, min_lead); s, _, _ = run_stream(tl, th, k, n, cd, min_lead)
                rows.append(dict(fa_budget=fa, thr=th, k=k, n=n, cooldown=cd, **s))
    return pd.DataFrame(rows)

SHOW = ["fa_budget", "k", "n", "cooldown", "alerts_per_day", "FA_per_day", "FA_day_pt_p90", "precision",
        "caught_useful", "missed", "lead_med"]


# ---------- biểu đồ ----------
def plot_tradeoff(df, max_fa=None, title=""):
    fig, ax = plt.subplots(figsize=(7.5, 5))
    for (k, n), g in df.groupby(["k", "n"]):
        ax.scatter(g["FA_per_day"], g["caught_useful"] * 100, label=f"{k}/{n} lần", s=40)
    ax.set_xscale("log"); ax.set_xlabel("Báo giả / ngày / bệnh nhân (trung bình)")
    ax.set_ylabel("% sự kiện bắt được với báo trước >= min_lead"); ax.axvline(1, ls=":", c="k")
    ax.text(1, ax.get_ylim()[0], " 1/ngày", fontsize=8); ax.set_title(title or "Đánh đổi báo giả - độ nhạy theo tham số")
    ax.legend(title="persistence"); plt.tight_layout(); plt.show()

def plot_config(pt, leads, title=""):
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.2))
    x = np.sort(pt["fa_per_day"].to_numpy()); ax[0].plot(x, np.arange(1, len(x) + 1) / len(x), drawstyle="steps-post")
    ax[0].set(xlabel="Báo giả/ngày của bệnh nhân", ylabel="Tỷ lệ bệnh nhân (tích lũy)", title="Phân phối báo giả theo bệnh nhân")
    ax[1].hist(leads, bins=np.arange(0, HORIZON + 1, 3)); ax[1].set(xlabel="Phút báo trước sự kiện", ylabel="Số sự kiện",
                                                                  title="Thời gian báo trước")
    top = pt.sort_values("fa", ascending=False).head(10); ax[2].barh(top.index.astype(str), top["fa_per_day"])
    ax[2].invert_yaxis(); ax[2].set(xlabel="Báo giả/ngày", title="10 bệnh nhân báo giả nhiều nhất")
    fig.suptitle(title); plt.tight_layout(); plt.show()

def plot_example(tl, thr_watch, thr_alert, cfg_watch=(1, 1, 30), cfg_alert=(3, 4, 60), rec=None, span=None):
    """Vẽ 1 record: SpO2 + sự kiện thật (tại T của timeline), và điểm rủi ro + Watch/Alert. cfg = (k, n, cooldown)."""
    if rec is None: rec = max(tl, key=lambda r: len(tl[r]["onsets"]) if len(tl[r]["t"]) > 50 else -1)
    d = tl[rec]; a, b = span or (0, len(d["spo2"])); T = d.get("T", SPO2_THR)
    fig, ax = plt.subplots(2, 1, figsize=(15, 6), sharex=True)
    ax[0].plot(np.arange(len(d["spo2"])), d["spo2"], lw=.8); ax[0].axhline(T, c="r", ls="--", lw=.8)
    for e in d["onsets"]: ax[0].axvline(e, c="r", alpha=.35)
    ax[0].set_ylabel("SpO2 (%)"); ax[0].set_title(f"{rec} | T = {T}% | đỏ: onset sự kiện")
    ax[1].plot(d["t"], d["s"], ".-", ms=3, lw=.6, label="điểm rủi ro")
    for thr, cfg, c, lab in ((thr_watch, cfg_watch, "orange", "Watch"), (thr_alert, cfg_alert, "red", "Alert")):
        ax[1].axhline(thr, c=c, ls="--", lw=.8)
        f = fire_times(d["t"], d["s"], thr, *cfg); ax[1].scatter(f, np.full(len(f), thr), c=c, s=60, marker="v", label=lab, zorder=5)
    ax[1].set(yscale="log", xlabel="Phút từ đầu record", ylabel="điểm rủi ro"); ax[1].legend(); ax[1].set_xlim(a, b)
    plt.tight_layout(); plt.show()

def plot_by_T(res, leads_by_T=None, title=""):
    """G6/G7: res = DataFrame một dòng mỗi (T, tầng) từ run_stream. Vẽ báo giả/ngày, độ nhạy, precision theo T;
    leads_by_T: dict T -> mảng thời gian báo trước (histogram chồng)."""
    n = 4 if leads_by_T else 3; fig, ax = plt.subplots(1, n, figsize=(5 * n, 4.2))
    for tier, g in res.groupby("tier"):
        g = g.sort_values("T")
        ax[0].plot(g["T"], g["FA_per_day"], "o-", label=f"{tier} (TB)"); ax[0].plot(g["T"], g["FA_day_pt_p90"], "x--", label=f"{tier} (p90 BN)")
        ax[1].plot(g["T"], g["caught_useful"] * 100, "o-", label=tier); ax[2].plot(g["T"], g["precision"] * 100, "o-", label=tier)
    ax[0].set(xlabel="T (%)", ylabel="Báo giả / ngày", title="Báo giả thật theo T"); ax[1].set(xlabel="T (%)", ylabel="%", title="Bắt được, báo trước ≥ 5 phút")
    ax[2].set(xlabel="T (%)", ylabel="%", title="Precision của cảnh báo")
    if leads_by_T:
        for T, l in leads_by_T.items(): ax[3].hist(l, bins=np.arange(0, HORIZON + 1, 3), histtype="step", label=f"T={T}")
        ax[3].set(xlabel="Phút báo trước", ylabel="Số sự kiện", title="Thời gian báo trước (Alert)")
    for a in ax: a.legend(fontsize=7)
    fig.suptitle(title); plt.tight_layout(); plt.show()
