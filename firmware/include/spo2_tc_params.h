// Sinh tự động bởi model/export_firmware.py - KHÔNG sửa tay.
// Nguồn: model/models_v3/hybrid_tc_meta.json + hybrid_tc_norm.npz
#pragma once
#include <stdint.h>

#define SPO2TC_MODEL_VERSION "hybrid_tc-20261016-s3"

/* ---------------- kích thước ---------------- */
#define SPO2TC_IN_MIN   60    /* số phút đầu vào */
#define SPO2TC_N_CH     4     /* kênh thô mỗi phút: spo2_mean, spo2_min, spo2_valid, pulse_mean */
#define SPO2TC_N_SEQ_CH 6     /* kênh vào model: spo2_mean-T, spo2_min-T, spo2_valid, pulse_mean, mask_spo2, mask_pulse */
#define SPO2TC_N_TAB    23
#define SPO2TC_STRIDE_MIN 5   /* chạy model mỗi 5 phút */
#define SPO2TC_HORIZON_MIN 30
#define SPO2TC_MIN_EVENT 3    /* sự kiện = SpO2 < T liên tục >= 3 phút */

/* ---------------- khoảng T hợp lệ (bác sĩ đặt, số nguyên %) ---------------- */
#define SPO2TC_T_MIN 85
#define SPO2TC_T_MAX 95
#define SPO2TC_N_T   11

/* ---------------- baseline ---------------- */
#define SPO2TC_BASE_LOOKBACK_MIN 1440  /* 24 giờ, KẾT THÚC TẠI phút bắt đầu cửa sổ 60' (không gồm 60' đầu vào) */
#define SPO2TC_BASE_SQI          0.5f  /* chỉ dùng phút có spo2_valid >= 0.5 */
#define SPO2TC_BASE_MIN_HOURS    1.0f  /* dưới mức này: baseline = SpO2 trung bình cửa sổ, base_hours = 0 */

/* ---------------- đặc trưng bảng (thứ tự cố định) ----------------
 *   [ 0] sp_mean_rT
 *   [ 1] sp_p10_rT
 *   [ 2] mn_min60_rT
 *   [ 3] mn_min10_rT
 *   [ 4] sp_last5_rT
 *   [ 5] frac_lt_T
 *   [ 6] frac_lt_T2
 *   [ 7] frac_lt_T4
 *   [ 8] sp_slope60
 *   [ 9] sp_slope30
 *   [10] sp_d_last10_first30
 *   [11] sp_std60
 *   [12] sp_std15
 *   [13] pu_mean
 *   [14] pu_slope30
 *   [15] pu_d_last10_first30
 *   [16] pu_std60
 *   [17] base_rT
 *   [18] last5_rbase
 *   [19] base_hours
 *   [20] valid_mean
 *   [21] sp_nan_frac
 *   [22] T
 */
static const float SPO2TC_SEQ_MU[SPO2TC_N_CH] = { 7.06351995f, 6.2429328f, 0.982367814f, 82.3615723f };
static const float SPO2TC_SEQ_SD[SPO2TC_N_CH] = { 3.87275076f, 4.33371544f, 0.112911247f, 16.8969135f };
static const float SPO2TC_TAB_MED[SPO2TC_N_TAB] = {
    6.98875046f, 6.0f, 2.0f, 5.0f, 7.0f, 0.0f,
    0.0f, 0.0f, 0.0f, 0.0f, 0.00111109414f, 0.713706732f,
    0.436181784f, 81.5652771f, -0.0018541601f, -0.0699208602f, 2.91857243f, 7.28333282f,
    -0.0833358765f, 19.083334f, 1.0f, 0.0f, 90.0f
};
static const float SPO2TC_TAB_MU[SPO2TC_N_TAB] = {
    7.06069183f, 5.95909023f, 1.2987833f, 4.49071646f, 7.04399252f, 0.0334741361f,
    0.0900495127f, 0.205119461f, -9.76754454e-05f, -0.000531334023f, -0.0131780887f, 0.966717601f,
    0.648430943f, 82.3697739f, -0.00135666598f, -0.0453911871f, 4.28225994f, 7.4543314f,
    -0.409955949f, 16.0040588f, 0.982367754f, 0.00729333283f, 90.0060654f
};
static const float SPO2TC_TAB_SD[SPO2TC_N_TAB] = {
    3.63572669f, 4.13874531f, 7.12489796f, 5.35246992f, 3.81048775f, 0.129330143f,
    0.229171276f, 0.347422451f, 0.0366947912f, 0.069702588f, 1.55908227f, 0.94893074f,
    0.844398022f, 15.7410841f, 0.287594169f, 6.38241005f, 4.46530437f, 3.4880836f,
    1.99180353f, 8.15763474f, 0.0602951869f, 0.0328123458f, 2.87989712f
};
#define SPO2TC_TAB_CLIP 5.0f

/* ---------------- lượng tử hóa (q = round_half_even(x / scale) + zero_point, kẹp [-128, 127]) ---------------- */
#define SPO2TC_SEQ_SCALE 0.0678765178f
#define SPO2TC_SEQ_ZP    39
#define SPO2TC_TAB_SCALE 0.0392156877f
#define SPO2TC_TAB_ZP    -1
#define SPO2TC_OUT_SCALE 0.0433892645f   /* logit = (q_out - OUT_ZP) * OUT_SCALE */
#define SPO2TC_OUT_ZP    125

/* ---------------- bảng T -> ngưỡng (chỉ số = T - SPO2TC_T_MIN) ----------------
 * Vượt ngưỡng khi q_out > *_THR_Q[T - T_MIN]. Chọn trên điểm INT8 của val theo báo giả/ngày thật. */
static const int8_t SPO2TC_WATCH_THR_Q[SPO2TC_N_T] = {     54, 54, 63, 71, 79, 87, 94, 101, 106, 110, 110 };
static const int8_t SPO2TC_ALERT_THR_Q[SPO2TC_N_T] = {     71, 71, 79, 86, 93, 101, 105, 109, 114, 117, 117 };
static const float SPO2TC_WATCH_THR_P[SPO2TC_N_T] = {
    0.043913f, 0.043913f, 0.063558f, 0.091154f, 0.121937f, 0.161272f,
    0.206678f, 0.260891f, 0.304831f, 0.352641f, 0.352641f
};
static const float SPO2TC_ALERT_THR_P[SPO2TC_N_T] = {
    0.091154f, 0.091154f, 0.119633f, 0.155489f, 0.203144f, 0.260891f,
    0.300254f, 0.342800f, 0.393197f, 0.414082f, 0.414082f
};

/* ---------------- cấu hình tầng mặc định (server có thể ghi đè qua config) ---------------- */
#define SPO2TC_WATCH_K 1
#define SPO2TC_WATCH_N 1
#define SPO2TC_WATCH_COOLDOWN_MIN 30
#define SPO2TC_ALERT_K 1
#define SPO2TC_ALERT_N 1
#define SPO2TC_ALERT_COOLDOWN_MIN 60
#define SPO2TC_CRIT_T 85
#define SPO2TC_CRIT_N_MIN 3
#define SPO2TC_CRIT_SQI 0.5f
#define SPO2TC_CRIT_HR_LOW 40
#define SPO2TC_CRIT_HR_HIGH 140
#define SPO2TC_CRIT_COOLDOWN_MIN 30
