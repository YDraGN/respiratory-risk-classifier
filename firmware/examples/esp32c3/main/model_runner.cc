// model_runner.cc - TFLite Micro cho hybrid_tc. Yêu cầu component espressif/esp-tflite-micro.
#include "model_runner.h"

#include <string.h>
#include "esp_log.h"
#include "hybrid_tc_model_data.h"
#include "spo2_tc_params.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

static const char *TAG = "model";

namespace {
// Đo thực tế bằng model_runner_arena_used() rồi giảm xuống (giữ dư ~20%).
constexpr size_t kArenaSize = 32 * 1024;
alignas(16) uint8_t g_arena[kArenaSize];

const tflite::Model *g_model = nullptr;
tflite::MicroInterpreter *g_interp = nullptr;
TfLiteTensor *g_tab = nullptr;   // [1, 23]   int8
TfLiteTensor *g_seq = nullptr;   // [1, 60, 6] int8
TfLiteTensor *g_out = nullptr;   // [1, 1]    int8
}  // namespace

extern "C" int model_runner_init(void) {
  g_model = tflite::GetModel(g_hybrid_tc_model);
  if (g_model->version() != TFLITE_SCHEMA_VERSION) {
    ESP_LOGE(TAG, "schema %lu khác %d", (unsigned long)g_model->version(), TFLITE_SCHEMA_VERSION);
    return -1;
  }
  // Đúng 7 op mà model dùng (kiểm tra bằng tf.lite.Interpreter._get_ops_details()).
  static tflite::MicroMutableOpResolver<7> resolver;
  resolver.AddConv2D();
  resolver.AddExpandDims();
  resolver.AddReshape();
  resolver.AddMaxPool2D();
  resolver.AddMean();
  resolver.AddConcatenation();
  resolver.AddFullyConnected();

  static tflite::MicroInterpreter interp(g_model, resolver, g_arena, kArenaSize);
  g_interp = &interp;
  if (g_interp->AllocateTensors() != kTfLiteOk) { ESP_LOGE(TAG, "AllocateTensors thất bại, tăng kArenaSize"); return -2; }

  // Thứ tự đầu vào do converter quyết định (meta.json: input_order). Nhận diện theo số chiều cho chắc.
  for (size_t i = 0; i < g_interp->inputs_size(); i++) {
    TfLiteTensor *t = g_interp->input(i);
    if (t->dims->size == 2) g_tab = t; else if (t->dims->size == 3) g_seq = t;
  }
  g_out = g_interp->output(0);
  if (!g_tab || !g_seq || g_tab->type != kTfLiteInt8 || g_seq->type != kTfLiteInt8 ||
      g_tab->dims->data[1] != SPO2TC_N_TAB || g_seq->dims->data[1] != SPO2TC_IN_MIN || g_seq->dims->data[2] != SPO2TC_N_SEQ_CH) {
    ESP_LOGE(TAG, "đầu vào model không đúng kích thước mong đợi");
    return -3;
  }
  // Thang lượng tử trong model phải khớp header (đổi model mà quên sinh lại header là lỗi hay gặp).
  if (g_tab->params.zero_point != SPO2TC_TAB_ZP || g_seq->params.zero_point != SPO2TC_SEQ_ZP ||
      g_out->params.zero_point != SPO2TC_OUT_ZP) {
    ESP_LOGE(TAG, "zero_point trong model khác spo2_tc_params.h - chạy lại model/export_firmware.py");
    return -4;
  }
  ESP_LOGI(TAG, "%s | model %u byte | arena dùng %u / %u byte", SPO2TC_MODEL_VERSION, g_hybrid_tc_model_len,
           (unsigned)g_interp->arena_used_bytes(), (unsigned)kArenaSize);
  return 0;
}

extern "C" int8_t model_runner_invoke(const int8_t *tab_q, const int8_t *seq_q, void *ctx) {
  (void)ctx;
  memcpy(g_tab->data.int8, tab_q, SPO2TC_N_TAB);
  memcpy(g_seq->data.int8, seq_q, SPO2TC_IN_MIN * SPO2TC_N_SEQ_CH);
  if (g_interp->Invoke() != kTfLiteOk) { ESP_LOGE(TAG, "Invoke thất bại"); return -128; }   // -128 = rủi ro thấp nhất
  return g_out->data.int8[0];
}

extern "C" size_t model_runner_arena_used(void) { return g_interp ? g_interp->arena_used_bytes() : 0; }
