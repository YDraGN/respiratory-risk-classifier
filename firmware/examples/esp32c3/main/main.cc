// main.cc - ví dụ tích hợp hybrid_tc trên ESP32-C3 (ESP-IDF).
// 1) Tự kiểm tra lúc boot: chạy 24 vector mẫu, so q_out với Python (phải khớp 24/24).
// 2) Vòng lặp mỗi phút: lấy bản ghi phút từ cảm biến -> spo2tc_engine_push_minute -> gửi trạng thái.
// Phần cảm biến (sensor_read_minute) và gửi dữ liệu (publish_*) là việc của team phần cứng: thay các hàm stub bên dưới.
#include <math.h>
#include <stdio.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "model_runner.h"
#include "spo2_tc.h"
#include "golden_vectors.h"

static const char *TAG = "spo2tc";

// ------------------------------------------------------------------ tự kiểm tra
static int self_test(void) {
  int ok = 0; int64_t t0 = esp_timer_get_time();
  for (int g = 0; g < SPO2TC_N_GOLDEN; g++) {
    const spo2tc_golden_t *v = &SPO2TC_GOLDEN[g];
    const float(*x)[SPO2TC_N_CH] = (const float(*)[SPO2TC_N_CH])v->x;
    float tab[SPO2TC_N_TAB]; int8_t tq[SPO2TC_N_TAB], sq[SPO2TC_IN_MIN * SPO2TC_N_SEQ_CH];
    spo2tc_features(x, v->T, v->baseline, v->base_hours, tab);
    spo2tc_quantize_tab(tab, tq);
    spo2tc_quantize_seq(x, v->T, sq);
    int8_t q = model_runner_invoke(tq, sq, NULL);
    if (q == v->out_q) ok++;
    else ESP_LOGW(TAG, "vector %d: q_out %d, mong đợi %d", g, q, v->out_q);
  }
  int64_t us = (esp_timer_get_time() - t0) / SPO2TC_N_GOLDEN;
  ESP_LOGI(TAG, "self-test: %d/%d khớp | trung bình %lld us/lần (tiền xử lý + model)", ok, SPO2TC_N_GOLDEN, us);
  return ok == SPO2TC_N_GOLDEN ? 0 : -1;
}

// ------------------------------------------------------------------ STUB: thay bằng code thật
// Gộp 60 giây gần nhất thành một bản ghi phút. Giây không hợp lệ (SQI kém, mất tiếp xúc) phải bị bỏ,
// KHÔNG ghi 0. spo2_valid = số giây hợp lệ / 60. Không có giây hợp lệ nào -> spo2_mean = spo2_min = NAN.
static void sensor_read_minute(float m[SPO2TC_N_CH]) {
  m[SPO2TC_SPO2_MEAN] = 97.0f; m[SPO2TC_SPO2_MIN] = 96.0f; m[SPO2TC_SPO2_VALID] = 1.0f; m[SPO2TC_PULSE_MEAN] = 72.0f;
}
static void publish_minute(const float m[SPO2TC_N_CH], const spo2tc_result_t *r) { (void)m; (void)r; }
static void publish_notification(const char *tier, const spo2tc_result_t *r) {
  ESP_LOGW(TAG, "THÔNG BÁO %s | p=%.3f q=%d", tier, r->prob, r->out_q);
}
// Khi nhận config mới từ server (dev/{id}/config): kiểm tra version, gọi spo2tc_engine_set_T, cập nhật cfg tầng.

static const char *LEVEL[] = {"UNKNOWN", "NORMAL", "WATCH", "ALERT", "CRITICAL"};

extern "C" void app_main(void) {
  if (model_runner_init() != 0 || self_test() != 0) {
    ESP_LOGE(TAG, "model không sẵn sàng - không chạy ML, chỉ còn luật CRITICAL");
  }
  static spo2tc_engine_t engine;
  spo2tc_config_t cfg; spo2tc_config_default(&cfg);
  spo2tc_config_set_T(&cfg, 88);                    // T mặc định; server ghi đè bằng config của bác sĩ
  spo2tc_engine_init(&engine, &cfg);

  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(60 * 1000));
    float m[SPO2TC_N_CH]; sensor_read_minute(m);
    spo2tc_result_t r = spo2tc_engine_push_minute(&engine, m, model_runner_invoke, NULL);
    publish_minute(m, &r);
    if (r.notify_critical) publish_notification(r.critical_reason == 1 ? "CRITICAL_SPO2" : "CRITICAL_HR", &r);
    if (r.notify_alert) publish_notification(r.alert_by_rule ? "ALERT_RULE" : "ALERT_ML", &r);
    if (r.notify_watch) publish_notification("WATCH", &r);
    if (r.ran_model) ESP_LOGI(TAG, "phút %ld | %s | p=%.3f | baseline %.1f (%.1f h)", engine.t, LEVEL[r.level], r.prob, r.baseline, r.base_hours);
  }
}
