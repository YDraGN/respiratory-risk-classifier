# Lưu trữ: bản v2, ngưỡng cố định 88%

Bản trước khi chuyển sang model nhận ngưỡng T. Lưu lại để đối chiếu với số liệu đã báo cáo (hybrid AUPRC test 0,080, LightGBM 0,084, luật 0,050; tầng Alert 1,28 báo giả/ngày).

| File | Nội dung |
|---|---|
| `data_windowing_thr88.ipynb`, `data_splitting_thr88.ipynb` | gắn nhãn < 88%, chia tập, dùng cả record 1 mẫu/phút |
| `model_hybrid.ipynb`, `model_lightgbm.ipynb`, `model_1d_cnn.ipynb` | notebook train và đánh giá (còn nguyên output) |
| `models/`, `models_v2/` | model đã export (`hybrid_thr88_int8.tflite`, ...) |
| `runs/` | checkpoint và điểm số (không commit) |

Các notebook này **không chạy lại được ở vị trí hiện tại**: chúng đọc `../data/data/dataset_thr88` và import `eval_utils`, `sim_alert` từ thư mục `model/`. Muốn chạy lại thì chép notebook về `model/`. `sim_alert.py` hiện tại vẫn tương thích với cách gọi cũ (mặc định T = 88).
