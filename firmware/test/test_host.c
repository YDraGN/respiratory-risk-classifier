/* test_host.c - kiểm tra thư viện spo2_tc trên PC, so với kết quả Python.
 * Build & chạy (trong thư mục firmware):
 *   gcc -std=c99 -O2 -Wall -Iinclude src/spo2_tc.c test/test_host.c -lm -o test_host && ./test_host
 * Thoát với mã 0 khi mọi kiểm tra đạt. Sai lệch +-1 bậc lượng tử ở vài phần tử là chấp nhận được
 * (làm tròn float32 khác thứ tự phép tính); con số được in ra để theo dõi.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "spo2_tc.h"
#include "golden_vectors.h"
#include "golden_persistence.h"

static int fails = 0;
#define CHECK(cond, ...) do { if (!(cond)) { fails++; printf("  FAIL: " __VA_ARGS__); printf("\n"); } } while (0)

static void test_golden(void) {
  int tab_exact = 0, tab_off1 = 0, seq_exact = 0, seq_off1 = 0, n_tab = 0, n_seq = 0; double max_df = 0;
  for (int g = 0; g < SPO2TC_N_GOLDEN; g++) {
    const spo2tc_golden_t *v = &SPO2TC_GOLDEN[g];
    float tab[SPO2TC_N_TAB]; int8_t tq[SPO2TC_N_TAB], sq[SPO2TC_IN_MIN * SPO2TC_N_SEQ_CH];
    const float (*x)[SPO2TC_N_CH] = (const float (*)[SPO2TC_N_CH])v->x;
    spo2tc_features(x, v->T, v->baseline, v->base_hours, tab);
    spo2tc_quantize_tab(tab, tq); spo2tc_quantize_seq(x, v->T, sq);
    for (int i = 0; i < SPO2TC_N_TAB; i++) {
      float a = tab[i], b = v->tab_f[i];
      if (isnan(a) || isnan(b)) CHECK(isnan(a) && isnan(b), "vector %d feature %d: C=%g, Python=%g", g, i, a, b);
      else { double d = fabs(a - b) / (1 + fabs(b)); if (d > max_df) max_df = d;
             CHECK(d < 1e-4, "vector %d feature %d: C=%.7g, Python=%.7g", g, i, a, b); }
      int dq = abs(tq[i] - v->tab_q[i]); n_tab++; tab_exact += dq == 0; tab_off1 += dq == 1;
      CHECK(dq <= 1, "vector %d tab_q %d: C=%d, Python=%d", g, i, tq[i], v->tab_q[i]);
    }
    for (int i = 0; i < SPO2TC_IN_MIN * SPO2TC_N_SEQ_CH; i++) {
      int dq = abs(sq[i] - v->seq_q[i]); n_seq++; seq_exact += dq == 0; seq_off1 += dq == 1;
      CHECK(dq <= 1, "vector %d seq_q %d: C=%d, Python=%d", g, i, sq[i], v->seq_q[i]);
    }
    spo2tc_config_t c; spo2tc_config_default(&c); spo2tc_config_set_T(&c, v->T);
    CHECK((v->out_q > c.watch_thr_q) == v->watch && (v->out_q > c.alert_thr_q) == v->alert, "vector %d: ngưỡng T=%d", g, v->T);
  }
  printf("[golden] %d vector | đặc trưng: sai số tương đối lớn nhất %.2e | tab_q khớp %d/%d (lệch 1: %d) | seq_q khớp %d/%d (lệch 1: %d)\n",
         SPO2TC_N_GOLDEN, max_df, tab_exact, n_tab, tab_off1, seq_exact, n_seq, seq_off1);
}

/* model giả: trả q theo thời điểm đánh giá, lấy từ chuỗi Python */
typedef struct { const spo2tc_engine_t *e; } pctx_t;
static int8_t model_from_series(const int8_t *tq, const int8_t *sq, void *ctx) {
  (void)tq; (void)sq; long t = ((pctx_t *)ctx)->e->t;
  for (int i = 0; i < SPO2TC_P_LEN; i++) if (SPO2TC_P_T[i] == t) return (int8_t)SPO2TC_P_Q[i];
  return -128;
}

static void test_persistence(void) {
  for (int k = 0; k < (int)(sizeof SPO2TC_P_CASES / sizeof SPO2TC_P_CASES[0]); k++) {
    const spo2tc_pcase_t *pc = &SPO2TC_P_CASES[k];
    spo2tc_config_t c; spo2tc_config_default(&c); c.alert_thr_q = SPO2TC_P_THR; c.watch_thr_q = 127;
    c.alert_k = pc->k; c.alert_n = pc->n; c.alert_cooldown = pc->cooldown;
    static spo2tc_engine_t e; spo2tc_engine_init(&e, &c); pctx_t ctx = { &e };
    float m[4] = { 97.f, 96.f, 1.f, 70.f }; int fires[SPO2TC_P_LEN], nf = 0;
    for (long t = 1; t <= SPO2TC_P_T[SPO2TC_P_LEN - 1]; t++) {
      spo2tc_result_t r = spo2tc_engine_push_minute(&e, m, model_from_series, &ctx);
      if (r.notify_alert && nf < SPO2TC_P_LEN) fires[nf++] = (int)t;
    }
    int same = nf == pc->n_fire;
    for (int i = 0; same && i < nf; i++) same = fires[i] == pc->fire[i];
    CHECK(same, "persistence k=%d n=%d cooldown=%d: C %d lần, Python %d lần", pc->k, pc->n, pc->cooldown, nf, pc->n_fire);
    printf("[persistence] k=%d n=%d cooldown=%2d: %d cảnh báo, %s\n", pc->k, pc->n, pc->cooldown, nf, same ? "khớp Python" : "KHÔNG khớp");
  }
}

static int8_t model_low(const int8_t *a, const int8_t *b, void *c) { (void)a; (void)b; (void)c; return -128; }

static void test_rules(void) {
  spo2tc_config_t c; spo2tc_config_default(&c); static spo2tc_engine_t e;
  /* CRITICAL: SpO2 82% (< 85) với SQI tốt -> thông báo đúng ở phút thứ 3 */
  spo2tc_engine_init(&e, &c); float ok[4] = { 97, 96, 1, 70 }, low[4] = { 82, 80, 1, 75 }; int at = -1;
  for (int t = 1; t <= 100; t++) spo2tc_engine_push_minute(&e, ok, model_low, NULL);
  for (int t = 1; t <= 6; t++) { spo2tc_result_t r = spo2tc_engine_push_minute(&e, low, model_low, NULL); if (r.notify_critical && at < 0) at = t;
                                 if (t >= 3) CHECK(r.level == SPO2TC_CRITICAL && r.critical_reason == 1, "critical level phút %d", t); }
  CHECK(at == 3, "critical phải phát ở phút 3, thực tế %d", at);
  /* SpO2 82% nhưng SQI kém (0.3) -> không CRITICAL */
  spo2tc_engine_init(&e, &c); float lowbad[4] = { 82, 80, 0.3f, 75 }; int any = 0;
  for (int t = 1; t <= 100; t++) spo2tc_engine_push_minute(&e, ok, model_low, NULL);
  for (int t = 1; t <= 6; t++) any |= spo2tc_engine_push_minute(&e, lowbad, model_low, NULL).notify_critical;
  CHECK(!any, "SQI kém không được kích hoạt critical");
  /* nhịp tim 150 > 140 liên tục 3 phút -> CRITICAL lý do 2 */
  spo2tc_engine_init(&e, &c); float tachy[4] = { 96, 95, 1, 150 }; spo2tc_result_t r = { 0 };
  for (int t = 1; t <= 100; t++) spo2tc_engine_push_minute(&e, ok, model_low, NULL);
  for (int t = 1; t <= 3; t++) r = spo2tc_engine_push_minute(&e, tachy, model_low, NULL);
  CHECK(r.notify_critical && r.critical_reason == 2, "critical nhịp tim");
  /* ALERT theo luật: SpO2 86% (< T=88, > crit 85) liên tục 3 phút có mạch */
  spo2tc_engine_init(&e, &c); float mid[4] = { 86, 85.5f, 1, 72 }; at = -1;
  for (int t = 1; t <= 100; t++) spo2tc_engine_push_minute(&e, ok, model_low, NULL);
  for (int t = 1; t <= 5; t++) { r = spo2tc_engine_push_minute(&e, mid, model_low, NULL); if (r.notify_alert && at < 0) at = t; }
  CHECK(at == 3 && r.level == SPO2TC_ALERT && r.alert_by_rule, "alert theo luật phải phát ở phút 3, thực tế %d", at);
  /* cùng đợt tụt nhưng mất mạch -> coi là nhiễu, không ALERT */
  spo2tc_engine_init(&e, &c); float midnp[4] = { 86, 85.5f, 1, NAN }; any = 0;
  for (int t = 1; t <= 100; t++) spo2tc_engine_push_minute(&e, ok, model_low, NULL);
  for (int t = 1; t <= 5; t++) any |= spo2tc_engine_push_minute(&e, midnp, model_low, NULL).notify_alert;
  CHECK(!any, "tụt SpO2 khi mất mạch không được báo ALERT");
  /* UNKNOWN: mất SpO2 nhiều phút */
  spo2tc_engine_init(&e, &c); float miss[4] = { NAN, NAN, 0, NAN };
  for (int t = 1; t <= 100; t++) spo2tc_engine_push_minute(&e, ok, model_low, NULL);
  for (int t = 1; t <= 20; t++) r = spo2tc_engine_push_minute(&e, miss, model_low, NULL);
  CHECK(r.level == SPO2TC_UNKNOWN, "mất tín hiệu phải là UNKNOWN, thực tế %d", r.level);
  /* T ngoài khoảng bị từ chối */
  CHECK(spo2tc_engine_set_T(&e, 97) == -1 && e.cfg.T == 88, "T=97 phải bị từ chối");
  printf("[luật] CRITICAL SpO2/nhịp tim, ALERT theo luật, lọc nhiễu mất mạch, UNKNOWN, chặn T ngoài khoảng: đã chạy\n");
}

static void test_baseline(void) {
  static spo2tc_baseline_t b; spo2tc_baseline_init(&b); float med, h;
  for (int i = 0; i < 120; i++) spo2tc_baseline_push(&b, 95.f, 1.f);     /* 2 giờ ở 95% */
  for (int i = 0; i < 60; i++) spo2tc_baseline_push(&b, 85.f, 1.f);      /* 60' gần nhất ở 85% (đầu vào) */
  spo2tc_baseline_get(&b, &med, &h);
  CHECK(med == 95.f && fabsf(h - 2.f) < 1e-6f, "baseline phải bỏ 60' gần nhất: median %g, giờ %g", med, h);
  for (int i = 0; i < 2000; i++) spo2tc_baseline_push(&b, (i % 2) ? 96.f : 94.f, (i % 3) ? 1.f : 0.2f);
  spo2tc_baseline_get(&b, &med, &h);
  CHECK(fabsf(med - 95.f) < 1e-6f && h <= 24.f + 1e-6f, "baseline vòng 24 giờ: median %g, giờ %g", med, h);
  printf("[baseline] bỏ 60' đầu vào, giới hạn 24 giờ, bỏ phút SQI kém: đã chạy\n");
}

int main(void) {
  printf("spo2_tc host test | model %s\n", SPO2TC_MODEL_VERSION);
  test_golden(); test_persistence(); test_rules(); test_baseline();
  printf(fails ? "\nKẾT QUẢ: %d kiểm tra KHÔNG đạt\n" : "\nKẾT QUẢ: tất cả kiểm tra đạt\n", fails);
  return fails ? 1 : 0;
}
