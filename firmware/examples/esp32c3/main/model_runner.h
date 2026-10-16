// model_runner.h - bọc TFLite Micro cho model hybrid_tc (2 đầu vào int8: tab[23], seq[60x6]; đầu ra int8 logit).
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Khởi tạo một lần lúc boot. Trả 0 nếu OK. In ra arena đã dùng để tinh chỉnh kArenaSize.
int model_runner_init(void);
// Callback đúng chữ ký spo2tc_model_fn: chạy model, trả q_out (int8).
int8_t model_runner_invoke(const int8_t *tab_q, const int8_t *seq_q, void *ctx);
size_t model_runner_arena_used(void);

#ifdef __cplusplus
}
#endif
