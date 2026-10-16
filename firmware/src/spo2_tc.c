/* spo2_tc.c - xem spo2_tc.h. Bản tham chiếu: model/tc_core.py, model/sim_alert.py. */
#include "spo2_tc.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define W SPO2TC_IN_MIN
static int fin(double v) { return !isnan(v) && !isinf(v); }

/* ======================================================================================
 * 1. Đặc trưng (khớp tc_core.base_stats / make_features)
 * ====================================================================================== */
static double nanmean_ch(const float x[W][SPO2TC_N_CH], int ch, int i0, int i1) {
  double s = 0; int n = 0;
  for (int i = i0; i < i1; i++) if (fin(x[i][ch])) { s += x[i][ch]; n++; }
  return n ? s / n : NAN;
}
static double nanstd_ch(const float x[W][SPO2TC_N_CH], int ch, int i0, int i1) {   /* ddof = 0 như numpy */
  double m = nanmean_ch(x, ch, i0, i1), s = 0; int n = 0;
  if (!fin(m)) return NAN;
  for (int i = i0; i < i1; i++) if (fin(x[i][ch])) { double d = x[i][ch] - m; s += d * d; n++; }
  return sqrt(s / n);
}
static double nanmin_ch(const float x[W][SPO2TC_N_CH], int ch, int i0, int i1) {
  double m = INFINITY;
  for (int i = i0; i < i1; i++) if (fin(x[i][ch]) && x[i][ch] < m) m = x[i][ch];
  return isinf(m) ? NAN : m;
}
static int cmp_d(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }
static double nanpercentile_ch(const float x[W][SPO2TC_N_CH], int ch, double q) {  /* numpy method='linear' */
  double v[W]; int n = 0;
  for (int i = 0; i < W; i++) if (fin(x[i][ch])) v[n++] = x[i][ch];
  if (!n) return NAN;
  qsort(v, n, sizeof(double), cmp_d);
  double pos = (n - 1) * q / 100.0; int lo = (int)floor(pos), hi = lo + 1 < n ? lo + 1 : lo; double t = pos - lo;
  double d = v[hi] - v[lo];
  return t >= 0.5 ? v[hi] - d * (1 - t) : v[lo] + d * t;      /* cùng công thức nội suy của numpy */
}
static double slope_ch(const float x[W][SPO2TC_N_CH], int ch, int lo) {             /* hồi quy tuyến tính, đơn vị/phút */
  double n = 0, Sx = 0, Sy = 0, Sxy = 0, Sxx = 0;
  for (int i = lo; i < W; i++) {
    if (!fin(x[i][ch])) continue;
    double t = i - lo, y = x[i][ch]; n++; Sx += t; Sy += y; Sxy += y * t; Sxx += t * t;
  }
  double den = n * Sxx - Sx * Sx;
  if (n < 5 || den == 0) return NAN;
  return (n * Sxy - Sx * Sy) / den;
}
static double frac_below(const float x[W][SPO2TC_N_CH], double thr) {
  int nv = 0, nb = 0;
  for (int i = 0; i < W; i++) if (fin(x[i][0])) { nv++; nb += x[i][0] < thr; }
  return (double)nb / (nv ? nv : 1);
}

void spo2tc_features(const float x[W][SPO2TC_N_CH], int T, float baseline, float base_hours, float tab[SPO2TC_N_TAB]) {
  const float Tf = (float)T;
  /* mức tuyệt đối (float64 rồi ép float32, như DataFrame.astype("float32")) */
  float sp_mean = (float)nanmean_ch(x, 0, 0, W), sp_p10 = (float)nanpercentile_ch(x, 0, 10);
  float mn60 = (float)nanmin_ch(x, 1, 0, W), mn10 = (float)nanmin_ch(x, 1, 50, W);
  double last5_d = nanmean_ch(x, 0, 55, W); float last5 = (float)last5_d;
  /* baseline: thiếu hoặc < BASE_MIN_HOURS -> SpO2 trung bình cửa sổ, giờ = 0 */
  double b = baseline, h = fin(base_hours) ? base_hours : 0.0;
  if (!fin(b) || h < SPO2TC_BASE_MIN_HOURS) { b = nanmean_ch(x, 0, 0, W); h = 0.0; }
  int nan_sp = 0; for (int i = 0; i < W; i++) nan_sp += !fin(x[i][0]);

  float *f = tab;
  f[0] = sp_mean - Tf;  f[1] = sp_p10 - Tf;  f[2] = mn60 - Tf;  f[3] = mn10 - Tf;  f[4] = last5 - Tf;
  f[5] = (float)frac_below(x, T);  f[6] = (float)frac_below(x, T + 2);  f[7] = (float)frac_below(x, T + 4);
  f[8] = (float)slope_ch(x, 0, 0);  f[9] = (float)slope_ch(x, 0, 30);
  f[10] = (float)(nanmean_ch(x, 0, 50, W) - nanmean_ch(x, 0, 0, 30));
  f[11] = (float)nanstd_ch(x, 0, 0, W);  f[12] = (float)nanstd_ch(x, 0, 45, W);
  f[13] = (float)nanmean_ch(x, 3, 0, W);  f[14] = (float)slope_ch(x, 3, 30);
  f[15] = (float)(nanmean_ch(x, 3, 50, W) - nanmean_ch(x, 3, 0, 30));  f[16] = (float)nanstd_ch(x, 3, 0, W);
  f[17] = (float)b - Tf;  f[18] = (float)(last5_d - b);  f[19] = (float)h;
  f[20] = (float)nanmean_ch(x, 2, 0, W);  f[21] = (float)((double)nan_sp / W);
  f[22] = Tf;
}

static int8_t q8(float v, float scale, int zp) {
  float r = rintf(v / scale) + (float)zp;            /* rintf: làm tròn về số chẵn gần nhất, như numpy.round */
  return (int8_t)(r < -128.f ? -128 : r > 127.f ? 127 : r);
}

void spo2tc_quantize_tab(const float tab[SPO2TC_N_TAB], int8_t tab_q[SPO2TC_N_TAB]) {
  for (int i = 0; i < SPO2TC_N_TAB; i++) {
    float v = isnan(tab[i]) ? SPO2TC_TAB_MED[i] : tab[i];
    v = (v - SPO2TC_TAB_MU[i]) / SPO2TC_TAB_SD[i];
    v = v < -SPO2TC_TAB_CLIP ? -SPO2TC_TAB_CLIP : v > SPO2TC_TAB_CLIP ? SPO2TC_TAB_CLIP : v;
    tab_q[i] = q8(v, SPO2TC_TAB_SCALE, SPO2TC_TAB_ZP);
  }
}

void spo2tc_quantize_seq(const float x[W][SPO2TC_N_CH], int T, int8_t seq_q[W * SPO2TC_N_SEQ_CH]) {
  for (int i = 0; i < W; i++) {
    int8_t *o = seq_q + i * SPO2TC_N_SEQ_CH;
    for (int c = 0; c < SPO2TC_N_CH; c++) {
      float v = x[i][c];
      if (c < 2) v -= (float)T;                       /* spo2_mean - T, spo2_min - T */
      v = (v - SPO2TC_SEQ_MU[c]) / SPO2TC_SEQ_SD[c];
      o[c] = q8(isnan(v) ? 0.f : v, SPO2TC_SEQ_SCALE, SPO2TC_SEQ_ZP);
    }
    o[4] = q8(isnan(x[i][0]) ? 1.f : 0.f, SPO2TC_SEQ_SCALE, SPO2TC_SEQ_ZP);   /* mask spo2_mean */
    o[5] = q8(isnan(x[i][3]) ? 1.f : 0.f, SPO2TC_SEQ_SCALE, SPO2TC_SEQ_ZP);   /* mask pulse_mean */
  }
}

float spo2tc_logit(int8_t q) { return ((int)q - SPO2TC_OUT_ZP) * SPO2TC_OUT_SCALE; }
float spo2tc_prob(int8_t q) { return 1.f / (1.f + expf(-spo2tc_logit(q))); }

/* ======================================================================================
 * 2. Baseline
 * ====================================================================================== */
void spo2tc_baseline_init(spo2tc_baseline_t *b) { b->head = 0; b->count = 0; }

void spo2tc_baseline_push(spo2tc_baseline_t *b, float spo2_mean, float spo2_valid) {
  b->v[b->head] = (fin(spo2_mean) && spo2_valid >= SPO2TC_BASE_SQI) ? spo2_mean : NAN;
  b->head = (b->head + 1) % SPO2TC_BASE_RING;
  if (b->count < SPO2TC_BASE_RING) b->count++;
}

static int cmp_f(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }
void spo2tc_baseline_get(const spo2tc_baseline_t *b, float *median, float *hours) {
  static float tmp[SPO2TC_BASE_LOOKBACK_MIN];
  int n_old = b->count - SPO2TC_IN_MIN, n = 0;                 /* bỏ 60 phút gần nhất (đầu vào của model) */
  for (int k = 0; k < n_old; k++) {
    int idx = (b->head - b->count + k + 2 * SPO2TC_BASE_RING) % SPO2TC_BASE_RING;   /* từ cũ tới mới */
    if (fin(b->v[idx])) tmp[n++] = b->v[idx];
  }
  *hours = n / 60.f;
  if (!n) { *median = NAN; return; }
  qsort(tmp, n, sizeof(float), cmp_f);
  *median = (n % 2) ? tmp[n / 2] : 0.5f * (tmp[n / 2 - 1] + tmp[n / 2]);
}

/* ======================================================================================
 * 3. Máy trạng thái (khớp sim_alert.fire_times / critical_times)
 * ====================================================================================== */
void spo2tc_config_default(spo2tc_config_t *c) {
  memset(c, 0, sizeof *c);
  c->watch_k = SPO2TC_WATCH_K; c->watch_n = SPO2TC_WATCH_N; c->watch_cooldown = SPO2TC_WATCH_COOLDOWN_MIN;
  c->alert_k = SPO2TC_ALERT_K; c->alert_n = SPO2TC_ALERT_N; c->alert_cooldown = SPO2TC_ALERT_COOLDOWN_MIN;
  c->crit_T = SPO2TC_CRIT_T; c->crit_n_min = SPO2TC_CRIT_N_MIN; c->crit_sqi = SPO2TC_CRIT_SQI;
  c->hr_low = SPO2TC_CRIT_HR_LOW; c->hr_high = SPO2TC_CRIT_HR_HIGH; c->crit_cooldown = SPO2TC_CRIT_COOLDOWN_MIN;
  spo2tc_config_set_T(c, 88);
}

int spo2tc_config_set_T(spo2tc_config_t *c, int T) {
  if (T < SPO2TC_T_MIN || T > SPO2TC_T_MAX) return -1;
  c->T = T; c->watch_thr_q = SPO2TC_WATCH_THR_Q[T - SPO2TC_T_MIN]; c->alert_thr_q = SPO2TC_ALERT_THR_Q[T - SPO2TC_T_MIN];
  return 0;
}

static void reset_stream_state(spo2tc_engine_t *e) {
  e->low_run = e->low_clean = 0; e->ev_n = e->ev_head = 0; e->ml_level = SPO2TC_NORMAL;
  memset(e->evflag, 0, sizeof e->evflag);
}

void spo2tc_engine_init(spo2tc_engine_t *e, const spo2tc_config_t *cfg) {
  memset(e, 0, sizeof *e);
  e->cfg = *cfg;
  for (int i = 0; i < W; i++) for (int c = 0; c < SPO2TC_N_CH; c++) e->win[i][c] = NAN;
  spo2tc_baseline_init(&e->base);
  e->last_crit = e->last_watch = e->last_alert = -1000000;
  e->last_base = NAN; e->last_prob = NAN;
  reset_stream_state(e);
}

int spo2tc_engine_set_T(spo2tc_engine_t *e, int T) {
  if (spo2tc_config_set_T(&e->cfg, T)) return -1;
  reset_stream_state(e);              /* nhãn sự kiện của 60' gần nhất sẽ dựng lại theo T mới */
  return 0;
}

/* đếm số lần vượt trong các đánh giá có t0 > t - n*STRIDE (gồm lần hiện tại) */
static int count_recent(const spo2tc_engine_t *e, const uint8_t *flag, long t, int n) {
  int c = 0;
  for (int k = 0; k < e->ev_n; k++) {
    int idx = (e->ev_head - 1 - k + SPO2TC_MAX_EVAL) % SPO2TC_MAX_EVAL;
    if (e->ev_t[idx] <= t - (long)n * SPO2TC_STRIDE_MIN) break;
    c += flag[idx];
  }
  return c;
}

spo2tc_result_t spo2tc_engine_push_minute(spo2tc_engine_t *e, const float m[SPO2TC_N_CH], spo2tc_model_fn model, void *ctx) {
  spo2tc_result_t r; memset(&r, 0, sizeof r);
  const spo2tc_config_t *c = &e->cfg;
  float sp = m[0], va = isnan(m[2]) ? 0.f : m[2], pu = m[3];

  /* --- đẩy phút vào cửa sổ 60' và baseline --- */
  for (int k = 0; k < SPO2TC_N_CH; k++) e->win[e->win_head][k] = m[k];
  e->evflag[e->win_head] = 0;
  e->win_head = (e->win_head + 1) % W;
  spo2tc_baseline_push(&e->base, sp, va);
  e->t++;
  long t = e->t;

  /* --- sự kiện < T đang diễn ra (cùng định nghĩa nhãn, sạch = có mạch) --- */
  int low = fin(sp) && sp < (float)c->T;           /* NAN tính là không thấp, như event_mask */
  if (low) { e->low_run++; e->low_clean += fin(pu); } else { e->low_run = e->low_clean = 0; }
  int event_now = e->low_run >= SPO2TC_MIN_EVENT && e->low_clean >= SPO2TC_MIN_EVENT;
  if (event_now)                                   /* đánh dấu cả đoạn tụt trong cửa sổ là sự kiện */
    for (int k = 0; k < e->low_run && k < W; k++) e->evflag[(e->win_head - 1 - k + W) % W] = 1;

  /* --- CRITICAL (luật) --- */
  int sp_bad = fin(sp) && sp < (float)c->crit_T && va >= c->crit_sqi;
  e->crit_run = sp_bad ? e->crit_run + 1 : 0;
  int hr_bad = fin(pu) && ((c->hr_low > 0 && pu < c->hr_low) || (c->hr_high > 0 && pu > c->hr_high));
  e->hr_run = hr_bad ? e->hr_run + 1 : 0;
  int crit_trig = 0;
  if (e->crit_run == c->crit_n_min) crit_trig = 1;
  else if (e->hr_run == c->crit_n_min) crit_trig = 2;
  if (crit_trig && t - e->last_crit >= c->crit_cooldown) { r.notify_critical = 1; r.critical_reason = crit_trig; e->last_crit = t; }
  int crit_active = e->crit_run >= c->crit_n_min || e->hr_run >= c->crit_n_min;
  if (crit_active && !r.critical_reason) r.critical_reason = e->crit_run >= c->crit_n_min ? 1 : 2;

  /* --- chất lượng tín hiệu (UNKNOWN) --- */
  int n_sp = 0; double v10 = 0;
  for (int k = 0; k < W; k++) {
    int idx = (e->win_head - 1 - k + W) % W;
    n_sp += fin(e->win[idx][0]);
    if (k < 10) v10 += fin(e->win[idx][2]) ? e->win[idx][2] : 0;
  }
  int full = t >= W;
  int poor = !full || n_sp * 10 < 7 * W || v10 / 10 < 0.5;   /* train chỉ dùng cửa sổ có >= 70% phút có SpO2 */

  /* --- ML mỗi 5 phút --- */
  int rule_alert = event_now;
  if (full && t % SPO2TC_STRIDE_MIN == 0) {
    int has_event = 0;
    for (int k = 0; k < W; k++) has_event |= e->evflag[k];
    e->ml_level = SPO2TC_NORMAL;
    if (!poor && !has_event && model) {
      float x[W][SPO2TC_N_CH], tab[SPO2TC_N_TAB]; int8_t tq[SPO2TC_N_TAB], sq[W * SPO2TC_N_SEQ_CH];
      for (int k = 0; k < W; k++) memcpy(x[k], e->win[(e->win_head + k) % W], sizeof x[k]);   /* cũ -> mới */
      float bm, bh;
      if (e->base_frozen) { bm = e->frozen_base; bh = e->frozen_hours; } else spo2tc_baseline_get(&e->base, &bm, &bh);
      spo2tc_features(x, c->T, bm, bh, tab); spo2tc_quantize_tab(tab, tq); spo2tc_quantize_seq(x, c->T, sq);
      int8_t q = model(tq, sq, ctx);
      e->last_q = q; e->last_prob = spo2tc_prob(q); e->last_base = bm; e->last_hours = bh; r.ran_model = 1;
      int ew = q > c->watch_thr_q, ea = q > c->alert_thr_q;
      e->ev_t[e->ev_head] = t; e->ev_w[e->ev_head] = (uint8_t)ew; e->ev_a[e->ev_head] = (uint8_t)ea;
      e->ev_head = (e->ev_head + 1) % SPO2TC_MAX_EVAL; if (e->ev_n < SPO2TC_MAX_EVAL) e->ev_n++;
      int pw = ew && count_recent(e, e->ev_w, t, c->watch_n) >= c->watch_k;
      int pa = ea && count_recent(e, e->ev_a, t, c->alert_n) >= c->alert_k;
      if (pw && t - e->last_watch >= c->watch_cooldown) { r.notify_watch = 1; e->last_watch = t; }
      if (pa && t - e->last_alert >= c->alert_cooldown) { r.notify_alert = 1; e->last_alert = t; }
      e->ml_level = pa ? SPO2TC_ALERT : pw ? SPO2TC_WATCH : SPO2TC_NORMAL;
    }
  }
  if (rule_alert && e->low_run == SPO2TC_MIN_EVENT && t - e->last_alert >= c->alert_cooldown) {
    r.notify_alert = 1; e->last_alert = t;
  }

  /* --- trạng thái hiển thị --- */
  if (crit_active) r.level = SPO2TC_CRITICAL;
  else if (rule_alert || e->ml_level == SPO2TC_ALERT) r.level = SPO2TC_ALERT;
  else if (e->ml_level == SPO2TC_WATCH) r.level = SPO2TC_WATCH;
  else if (poor) r.level = SPO2TC_UNKNOWN;
  else r.level = SPO2TC_NORMAL;
  r.alert_by_rule = rule_alert;

  /* baseline đóng băng khi đang ALERT/CRITICAL để đợt tụt không kéo baseline xuống */
  int freeze = r.level >= SPO2TC_ALERT;
  if (freeze && !e->base_frozen) { spo2tc_baseline_get(&e->base, &e->frozen_base, &e->frozen_hours); e->base_frozen = 1; }
  else if (!freeze) e->base_frozen = 0;

  r.out_q = e->last_q; r.prob = e->last_prob; r.baseline = e->last_base; r.base_hours = e->last_hours;
  return r;
}
