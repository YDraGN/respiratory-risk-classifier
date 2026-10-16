"""export_firmware.py - sinh gói bàn giao cho firmware từ model đã export (models_v3).

Chạy (trong thư mục model):  python export_firmware.py
Không train lại, không đọc tập test. Đầu ra ở ../firmware:
    firmware/model/hybrid_tc_model_data.{h,cc}   model INT8 dạng C array
    firmware/include/spo2_tc_params.h            chuẩn hóa, lượng tử hóa, bảng T -> ngưỡng int8, cấu hình tầng
    firmware/test/golden_vectors.{h,json}        cửa sổ mẫu từ VAL + đặc trưng/đầu vào/đầu ra mong đợi
    firmware/test/golden_persistence.h           chuỗi đầu ra model + thời điểm cảnh báo mong đợi (persistence, cooldown)
Đầu ra model mong đợi tính bằng kernel tham chiếu của TFLite (BUILTIN_REF), giống TFLite Micro.
"""
import os, json, numpy as np, tensorflow as tf
import tc_core as C, sim_alert as SA

MODEL_DIR, DATA_DIR, OUT = "models_v3", "../data/data/dataset_tc", "../firmware"
NAME, N_VEC, SEED = "hybrid_tc", 24, 0


def c_float(v):
    if not np.isfinite(v): return "NAN"
    s = f"{float(v):.9g}"
    return s + ("f" if any(ch in s for ch in ".e") else ".0f")

def c_arr(vals, fmt, per_line=12, indent="    "):
    vals = list(vals); lines = []
    for i in range(0, len(vals), per_line): lines.append(indent + ", ".join(fmt(v) for v in vals[i:i + per_line]))
    return ",\n".join(lines)

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f: f.write(text)
    print("wrote", os.path.relpath(path, OUT), f"({os.path.getsize(path):,} B)")


meta = json.load(open(f"{MODEL_DIR}/{NAME}_meta.json", encoding="utf-8"))
nm = C.load_norm(f"{MODEL_DIR}/{NAME}_norm.npz")
tfl = open(f"{MODEL_DIR}/{NAME}_int8.tflite", "rb").read()
cols = [str(c) for c in nm["tab_cols"]]
assert cols == C.tab_cols() and nm["rel"] and nm["use_base"] and nm["use_pulse"], "norm không khớp tc_core"
qs, qt, qo = meta["input_seq"], meta["input_tab"], meta["output"]
TH = meta["thresholds"]; T_ALL = TH["T"]; assert T_ALL == list(range(T_ALL[0], T_ALL[-1] + 1))
tiers, crit = meta["tiers"], meta["critical"]
ver = meta["model_version"]

# ---------------------------------------------------------------- model C array
write(f"{OUT}/model/hybrid_tc_model_data.h", f"""// Sinh tự động bởi model/export_firmware.py - KHÔNG sửa tay.
// Model: {ver} | {len(tfl):,} byte | INT8, 2 đầu vào (tab, seq), đầu ra logit int8.
#pragma once
#include <stdint.h>
extern const unsigned char g_hybrid_tc_model[];
extern const unsigned int g_hybrid_tc_model_len;
""")
write(f"{OUT}/model/hybrid_tc_model_data.cc", f"""// Sinh tự động bởi model/export_firmware.py - KHÔNG sửa tay. Model: {ver}
#include "hybrid_tc_model_data.h"
alignas(16) const unsigned char g_hybrid_tc_model[] = {{
{c_arr(tfl, lambda b: f"0x{b:02x}", 16, "  ")}
}};
const unsigned int g_hybrid_tc_model_len = {len(tfl)};
""")

# ---------------------------------------------------------------- tham số
names = "\n".join(f" *   [{i:2d}] {c}" for i, c in enumerate(cols))
row = lambda key: c_arr(TH[key], lambda v: str(int(v)), 11)
rowf = lambda key: c_arr(TH[key], lambda v: f"{v:.6f}f", 6)
write(f"{OUT}/include/spo2_tc_params.h", f"""// Sinh tự động bởi model/export_firmware.py - KHÔNG sửa tay.
// Nguồn: model/models_v3/{NAME}_meta.json + {NAME}_norm.npz
#pragma once
#include <stdint.h>

#define SPO2TC_MODEL_VERSION "{ver}"

/* ---------------- kích thước ---------------- */
#define SPO2TC_IN_MIN   {C.IN_MIN}    /* số phút đầu vào */
#define SPO2TC_N_CH     {len(C.CH)}     /* kênh thô mỗi phút: spo2_mean, spo2_min, spo2_valid, pulse_mean */
#define SPO2TC_N_SEQ_CH 6     /* kênh vào model: spo2_mean-T, spo2_min-T, spo2_valid, pulse_mean, mask_spo2, mask_pulse */
#define SPO2TC_N_TAB    {len(cols)}
#define SPO2TC_STRIDE_MIN {C.STRIDE}   /* chạy model mỗi 5 phút */
#define SPO2TC_HORIZON_MIN {C.HORIZON}
#define SPO2TC_MIN_EVENT {C.MIN_EVENT}    /* sự kiện = SpO2 < T liên tục >= 3 phút */

/* ---------------- khoảng T hợp lệ (bác sĩ đặt, số nguyên %) ---------------- */
#define SPO2TC_T_MIN {T_ALL[0]}
#define SPO2TC_T_MAX {T_ALL[-1]}
#define SPO2TC_N_T   {len(T_ALL)}

/* ---------------- baseline ---------------- */
#define SPO2TC_BASE_LOOKBACK_MIN 1440  /* 24 giờ, KẾT THÚC TẠI phút bắt đầu cửa sổ 60' (không gồm 60' đầu vào) */
#define SPO2TC_BASE_SQI          0.5f  /* chỉ dùng phút có spo2_valid >= 0.5 */
#define SPO2TC_BASE_MIN_HOURS    {C.BASE_MIN_HOURS:.1f}f  /* dưới mức này: baseline = SpO2 trung bình cửa sổ, base_hours = 0 */

/* ---------------- đặc trưng bảng (thứ tự cố định) ----------------
{names}
 */
static const float SPO2TC_SEQ_MU[SPO2TC_N_CH] = {{ {c_arr(nm["seq_mu"], c_float, 4, "")} }};
static const float SPO2TC_SEQ_SD[SPO2TC_N_CH] = {{ {c_arr(nm["seq_sd"], c_float, 4, "")} }};
static const float SPO2TC_TAB_MED[SPO2TC_N_TAB] = {{
{c_arr(nm["tab_med"], c_float, 6)}
}};
static const float SPO2TC_TAB_MU[SPO2TC_N_TAB] = {{
{c_arr(nm["tab_mu"], c_float, 6)}
}};
static const float SPO2TC_TAB_SD[SPO2TC_N_TAB] = {{
{c_arr(nm["tab_sd"], c_float, 6)}
}};
#define SPO2TC_TAB_CLIP 5.0f

/* ---------------- lượng tử hóa (q = round_half_even(x / scale) + zero_point, kẹp [-128, 127]) ---------------- */
#define SPO2TC_SEQ_SCALE {qs["scale"]:.9g}f
#define SPO2TC_SEQ_ZP    {qs["zero_point"]}
#define SPO2TC_TAB_SCALE {qt["scale"]:.9g}f
#define SPO2TC_TAB_ZP    {qt["zero_point"]}
#define SPO2TC_OUT_SCALE {qo["scale"]:.9g}f   /* logit = (q_out - OUT_ZP) * OUT_SCALE */
#define SPO2TC_OUT_ZP    {qo["zero_point"]}

/* ---------------- bảng T -> ngưỡng (chỉ số = T - SPO2TC_T_MIN) ----------------
 * Vượt ngưỡng khi q_out > *_THR_Q[T - T_MIN]. Chọn trên điểm INT8 của val theo báo giả/ngày thật. */
static const int8_t SPO2TC_WATCH_THR_Q[SPO2TC_N_T] = {{ {row("watch_int8")} }};
static const int8_t SPO2TC_ALERT_THR_Q[SPO2TC_N_T] = {{ {row("alert_int8")} }};
static const float SPO2TC_WATCH_THR_P[SPO2TC_N_T] = {{
{rowf("watch_prob")}
}};
static const float SPO2TC_ALERT_THR_P[SPO2TC_N_T] = {{
{rowf("alert_prob")}
}};

/* ---------------- cấu hình tầng mặc định (server có thể ghi đè qua config) ---------------- */
#define SPO2TC_WATCH_K {tiers["watch"]["k"]}
#define SPO2TC_WATCH_N {tiers["watch"]["n"]}
#define SPO2TC_WATCH_COOLDOWN_MIN {tiers["watch"]["cooldown"]}
#define SPO2TC_ALERT_K {tiers["alert"]["k"]}
#define SPO2TC_ALERT_N {tiers["alert"]["n"]}
#define SPO2TC_ALERT_COOLDOWN_MIN {tiers["alert"]["cooldown"]}
#define SPO2TC_CRIT_T {crit["crit_T"]}
#define SPO2TC_CRIT_N_MIN {crit["n_min"]}
#define SPO2TC_CRIT_SQI {crit["sqi_min"]}f
#define SPO2TC_CRIT_HR_LOW {crit["hr_low"]}
#define SPO2TC_CRIT_HR_HIGH {crit["hr_high"]}
#define SPO2TC_CRIT_COOLDOWN_MIN {crit["cooldown"]}
""")

# ---------------------------------------------------------------- vector kiểm thử
it = tf.lite.Interpreter(model_content=tfl, experimental_op_resolver_type=tf.lite.experimental.OpResolverType.BUILTIN_REF)
it.allocate_tensors()
det = {len(d["shape"]): d for d in it.get_input_details()}; out = it.get_output_details()[0]
q = lambda x, s, z: np.clip(np.round(x / s) + z, -128, 127).astype(np.int8)

d = C.load_split(DATA_DIR, "val"); rng = np.random.default_rng(SEED); n = len(d["X"])
nan_frac = np.isnan(d["X"][:, :, 0]).mean(1); nob = ~np.isfinite(d["baseline"]) | (d["baseline_hours"] < C.BASE_MIN_HOURS)
j92 = C.col(d, 92); pos = np.where(d["Y"][:, j92].astype(bool) & d["OK"][:, j92])[0]
pick = np.r_[rng.choice(pos, 8, replace=False), rng.choice(np.where(nan_frac > .1)[0], 4, replace=False),
             rng.choice(np.where(nob)[0], 4, replace=False), rng.choice(n, N_VEC - 16, replace=False)]
Ts = [T_ALL[i % len(T_ALL)] for i in range(N_VEC)]
V = []
for i, T in zip(pick, Ts):
    X = d["X"][i:i + 1]; b, h = d["baseline"][i:i + 1], d["baseline_hours"][i:i + 1]
    F = C.make_features(X, T, b, h)                                     # bản tham chiếu
    tab = C.norm_tab(F, nm)[0]; seq = C.make_seq(X, T, nm["seq_mu"], nm["seq_sd"])[0]
    tq, sq = q(tab, qt["scale"], qt["zero_point"]), q(seq, qs["scale"], qs["zero_point"])
    it.set_tensor(det[2]["index"], tq[None]); it.set_tensor(det[3]["index"], sq[None]); it.invoke()
    oq = int(it.get_tensor(out["index"])[0, 0])
    V.append(dict(rec=str(d["rec"][i]), start=int(d["start"][i]), T=int(T), baseline=float(b[0]), base_hours=float(h[0]),
                  x=X[0].tolist(), tab_f=F.to_numpy()[0].tolist(), tab_q=tq.tolist(), seq_q=sq.ravel().tolist(), out_q=oq,
                  watch=bool(oq > TH["watch_int8"][T - T_ALL[0]]), alert=bool(oq > TH["alert_int8"][T - T_ALL[0]])))
os.makedirs(f"{OUT}/test", exist_ok=True)
json.dump(dict(model_version=ver, tab_cols=cols, note="x: 60 phút x [spo2_mean, spo2_min, spo2_valid, pulse_mean], NaN = thiếu",
               vectors=V), open(f"{OUT}/test/golden_vectors.json", "w", encoding="utf-8"), allow_nan=True)
print("wrote test/golden_vectors.json")

body = []
for v in V:
    xs = c_arr([c for r in v["x"] for c in r], c_float, 8, "      ")
    body.append(f"""  {{ /* {v['rec']} @ {v['start']} */
    {v['T']}, {c_float(v['baseline'])}, {c_float(v['base_hours'])},
    {{
{xs}
    }},
    {{ {c_arr(v['tab_f'], c_float, 6, '')} }},
    {{ {c_arr(v['tab_q'], str, 23, '')} }},
    {{
{c_arr(v['seq_q'], str, 30, '      ')}
    }},
    {v['out_q']}, {int(v['watch'])}, {int(v['alert'])}
  }}""")
write(f"{OUT}/test/golden_vectors.h", f"""// Sinh tự động bởi model/export_firmware.py - KHÔNG sửa tay. Model: {ver}
// {N_VEC} cửa sổ lấy từ tập VAL. Giá trị mong đợi tính bằng tc_core.py (Python) và TFLite kernel tham chiếu.
#pragma once
#include <math.h>
#include <stdint.h>
#include "spo2_tc_params.h"
typedef struct {{
  int T; float baseline; float base_hours;
  float x[SPO2TC_IN_MIN * SPO2TC_N_CH];          /* [phút][kênh], NAN = thiếu */
  float tab_f[SPO2TC_N_TAB];                       /* đặc trưng trước chuẩn hóa (NAN được phép) */
  int8_t tab_q[SPO2TC_N_TAB];                      /* đầu vào 'tab' đã lượng tử */
  int8_t seq_q[SPO2TC_IN_MIN * SPO2TC_N_SEQ_CH];   /* đầu vào 'seq' đã lượng tử */
  int8_t out_q; int watch; int alert;
}} spo2tc_golden_t;
#define SPO2TC_N_GOLDEN {N_VEC}
static const spo2tc_golden_t SPO2TC_GOLDEN[SPO2TC_N_GOLDEN] = {{
{(','+chr(10)).join(body)}
}};
""")

# ---------------------------------------------------------------- persistence / cooldown
rng = np.random.default_rng(1); t = np.arange(60, 60 + 5 * 300, 5); sq = rng.integers(40, 128, len(t)).astype(int)
thr = TH["alert_int8"][88 - T_ALL[0]]; cases = []
for k, n_, cd in ((1, 1, 60), (2, 3, 30), (3, 4, 60), (1, 1, 0)):
    cases.append((k, n_, cd, SA.fire_times(t, sq, thr, k, n_, cd).tolist()))
cs = ",\n".join(f"  {{ {k}, {n_}, {cd}, {len(f)}, {{ {c_arr(f, str, 20, '') or '0'} }} }}" for k, n_, cd, f in cases)
write(f"{OUT}/test/golden_persistence.h", f"""// Sinh tự động bởi model/export_firmware.py. Kỳ vọng tính bằng sim_alert.fire_times (Python).
// Chuỗi q_out mỗi 5 phút tại t = 60, 65, ...; vượt ngưỡng khi q_out > {thr}.
#pragma once
#define SPO2TC_P_LEN {len(t)}
#define SPO2TC_P_THR {thr}
static const int SPO2TC_P_T[SPO2TC_P_LEN] = {{
{c_arr(t, str, 20)}
}};
static const int SPO2TC_P_Q[SPO2TC_P_LEN] = {{
{c_arr(sq, str, 20)}
}};
typedef struct {{ int k, n, cooldown, n_fire; int fire[{len(t)}]; }} spo2tc_pcase_t;
static const spo2tc_pcase_t SPO2TC_P_CASES[{len(cases)}] = {{
{cs}
}};
""")
