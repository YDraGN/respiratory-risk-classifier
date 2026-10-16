/* spo2_tc.h - tiền xử lý và lớp quyết định cảnh báo cho model hybrid_tc (vòng đeo ESP32-C3).
 *
 * Thư viện C99 thuần, không cấp phát động, không phụ thuộc TFLite. Việc chạy model do bên gọi
 * cung cấp qua callback spo2tc_model_fn (xem examples/esp32c3/model_runner.cc).
 *
 * Bản tham chiếu là model/tc_core.py (Python). Mọi công thức ở đây phải khớp với file đó;
 * test/test_host.c so từng số với test/golden_vectors.h.
 *
 * Dữ liệu vào: MỖI PHÚT một bản ghi 4 kênh (NAN = thiếu, KHÔNG dùng 0):
 *   [0] spo2_mean   SpO2 trung bình các giây hợp lệ trong phút (%)
 *   [1] spo2_min    SpO2 nhỏ nhất các giây hợp lệ trong phút (%)
 *   [2] spo2_valid  tỷ lệ giây hợp lệ trong phút (0..1, không bao giờ NAN)
 *   [3] pulse_mean  nhịp mạch trung bình (bpm) từ PPG
 */
#pragma once
#include <stdint.h>
#include "spo2_tc_params.h"

#ifdef __cplusplus
extern "C" {
#endif

enum { SPO2TC_SPO2_MEAN = 0, SPO2TC_SPO2_MIN = 1, SPO2TC_SPO2_VALID = 2, SPO2TC_PULSE_MEAN = 3 };

/* ------------------------------------------------------------------------------------------
 * 1. Tiền xử lý một cửa sổ 60 phút (hàm thuần, dùng được cả ở server)
 * x: 60 phút x 4 kênh, x[0] là phút cũ nhất. T: ngưỡng bác sĩ đặt (%).
 * baseline/base_hours: từ spo2tc_baseline_get (NAN/0 nếu chưa có).
 * ------------------------------------------------------------------------------------------ */
void spo2tc_features(const float x[SPO2TC_IN_MIN][SPO2TC_N_CH], int T, float baseline, float base_hours,
                     float tab[SPO2TC_N_TAB]);                          /* 23 đặc trưng trước chuẩn hóa */
void spo2tc_quantize_tab(const float tab[SPO2TC_N_TAB], int8_t tab_q[SPO2TC_N_TAB]);
void spo2tc_quantize_seq(const float x[SPO2TC_IN_MIN][SPO2TC_N_CH], int T,
                         int8_t seq_q[SPO2TC_IN_MIN * SPO2TC_N_SEQ_CH]);  /* [phút][6 kênh] */
float spo2tc_logit(int8_t out_q);                                       /* (q - zp) * scale */
float spo2tc_prob(int8_t out_q);                                        /* sigmoid(logit), chỉ để hiển thị */

/* ------------------------------------------------------------------------------------------
 * 2. Baseline: trung vị SpO2 các phút tín hiệu tốt trong 24 giờ KẾT THÚC tại đầu cửa sổ 60' đầu vào
 *    (tức là bỏ 60 phút gần nhất), đúng như lúc train. Bộ nhớ: (1440 + 60) float = 6 KB.
 * ------------------------------------------------------------------------------------------ */
#define SPO2TC_BASE_RING (SPO2TC_BASE_LOOKBACK_MIN + SPO2TC_IN_MIN)
typedef struct {
  float v[SPO2TC_BASE_RING];   /* SpO2 của phút tốt, NAN nếu phút kém */
  int head, count;
} spo2tc_baseline_t;
void spo2tc_baseline_init(spo2tc_baseline_t *b);
void spo2tc_baseline_push(spo2tc_baseline_t *b, float spo2_mean, float spo2_valid);
/* median = NAN và hours = 0 nếu chưa có phút tốt nào. Dùng một mảng tạm tĩnh 1440 float. */
void spo2tc_baseline_get(const spo2tc_baseline_t *b, float *median, float *hours);

/* ------------------------------------------------------------------------------------------
 * 3. Máy trạng thái: đẩy một phút vào, nhận trạng thái và thông báo
 * ------------------------------------------------------------------------------------------ */
typedef enum { SPO2TC_UNKNOWN = 0, SPO2TC_NORMAL, SPO2TC_WATCH, SPO2TC_ALERT, SPO2TC_CRITICAL } spo2tc_level_t;

typedef struct {
  int T;                                   /* ngưỡng bác sĩ đặt, [SPO2TC_T_MIN, SPO2TC_T_MAX] */
  int8_t watch_thr_q, alert_thr_q;         /* điền bởi spo2tc_config_set_T (hoặc server gửi xuống) */
  int watch_k, watch_n, watch_cooldown;    /* persistence k/n (n lần đánh giá = n*5 phút), cooldown (phút) */
  int alert_k, alert_n, alert_cooldown;
  int crit_T, crit_n_min;                  /* Critical: SpO2 < crit_T liên tục crit_n_min phút với SQI tốt */
  float crit_sqi;
  int hr_low, hr_high;                     /* hoặc mạch ngoài [hr_low, hr_high] liên tục crit_n_min phút; 0 = tắt */
  int crit_cooldown;
} spo2tc_config_t;

void spo2tc_config_default(spo2tc_config_t *c);           /* giá trị trong spo2_tc_params.h, T = 88 */
int  spo2tc_config_set_T(spo2tc_config_t *c, int T);      /* 0 nếu hợp lệ, -1 nếu T ngoài khoảng (không đổi gì) */

typedef int8_t (*spo2tc_model_fn)(const int8_t *tab_q, const int8_t *seq_q, void *ctx);

typedef struct {
  spo2tc_level_t level;        /* trạng thái hiện tại để hiển thị */
  int notify_watch, notify_alert, notify_critical;   /* 1 = gửi thông báo ở phút này (đã qua persistence + cooldown) */
  int critical_reason;         /* 1 = SpO2, 2 = nhịp tim */
  int alert_by_rule;           /* 1 = ALERT do luật (sự kiện đang diễn ra), không phải do ML */
  int ran_model;               /* 1 = phút này có chạy model */
  int8_t out_q; float prob;    /* đầu ra model ở lần chạy gần nhất */
  float baseline, base_hours;  /* baseline đã dùng ở lần chạy gần nhất */
} spo2tc_result_t;

#define SPO2TC_MAX_EVAL 16
typedef struct {
  spo2tc_config_t cfg;
  float win[SPO2TC_IN_MIN][SPO2TC_N_CH];  /* vòng 60 phút gần nhất */
  uint8_t evflag[SPO2TC_IN_MIN];          /* phút thuộc sự kiện < T (đầu vào không hợp lệ cho model) */
  int win_head;
  long t;                                 /* số phút đã đẩy vào */
  spo2tc_baseline_t base;
  float frozen_base, frozen_hours; int base_frozen;
  int low_run, low_clean, crit_run, hr_run;
  long last_crit, last_watch, last_alert;
  long ev_t[SPO2TC_MAX_EVAL]; uint8_t ev_w[SPO2TC_MAX_EVAL], ev_a[SPO2TC_MAX_EVAL]; int ev_n, ev_head;
  spo2tc_level_t ml_level; int8_t last_q; float last_prob, last_base, last_hours;
} spo2tc_engine_t;

void spo2tc_engine_init(spo2tc_engine_t *e, const spo2tc_config_t *cfg);
/* Gọi khi bác sĩ đổi T: cập nhật ngưỡng, xóa lịch sử persistence (nhãn sự kiện tính lại theo T mới). */
int  spo2tc_engine_set_T(spo2tc_engine_t *e, int T);
spo2tc_result_t spo2tc_engine_push_minute(spo2tc_engine_t *e, const float minute[SPO2TC_N_CH],
                                          spo2tc_model_fn model, void *ctx);

#ifdef __cplusplus
}
#endif
