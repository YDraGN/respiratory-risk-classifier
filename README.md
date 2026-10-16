# Cảnh báo sớm hạ SpO₂ cho vòng đeo tay (Model 1)

> Một phần của hệ thống AIoT theo dõi bệnh hô hấp mạn tính (đội IT x MECH, VEDC 2026).
> Repo này là **Model 1**: dự đoán đợt hạ SpO₂ từ SpO₂ và mạch đo bằng MAX30102 trên
> vòng đeo ESP32-C3. Model chạy trên thiết bị (TFLite Micro INT8).
> Model 2 (ống nghe số, phân loại âm phổi) nằm ngoài repo này.

![Python](https://img.shields.io/badge/Python-3.10-1a1a2e?style=flat-square&logo=python&logoColor=3776AB&labelColor=1a1a2e&color=3776AB)
![TensorFlow](https://img.shields.io/badge/TFLite-INT8-1a1a2e?style=flat-square&logo=tensorflow&logoColor=FF6F00&labelColor=1a1a2e&color=FF6F00)
![License](https://img.shields.io/badge/license-Private%20%2F%20All%20Rights%20Reserved-1a1a2e?style=flat-square&labelColor=1a1a2e&color=6c757d)

## Bài toán

$$f(\text{60 phút lịch sử},\ T,\ \text{baseline}) \rightarrow P(\text{SpO₂} < T \text{ liên tục} \ge 3 \text{ phút trong 30 phút tới})$$

- **T là đầu vào của model**, do bác sĩ đặt cho từng bệnh nhân (COPD 88–92%, phần lớn bệnh khác 94–98%, theo BTS 2017). Một model dùng cho mọi T trong khoảng 85–95%; đổi T chỉ cần gửi config mới xuống thiết bị, không train lại.
- Mọi đầu vào được biểu diễn **tương đối so với T**. Baseline là thống kê chạy liên tục do firmware tự tính, không phải trọng số riêng của từng bệnh nhân.
- Tầng cảnh báo: **WATCH / ALERT** từ ML (ngưỡng xác suất theo bảng T → ngưỡng, có persistence và cooldown); **CRITICAL** luôn là luật cố định (SpO₂ < ngưỡng Critical trong N phút với SQI tốt, hoặc nhịp tim ngoài khoảng); **UNKNOWN** khi tín hiệu kém hoặc mất dữ liệu.

## Dữ liệu

[MIMIC-III Waveform Database Matched Subset](https://physionet.org/content/mimic3wdb-matched/1.0/), phần numerics (truy cập mở, không cần credential). Chỉ dùng record **1 Hz**, đưa về lưới 1 phút. Kênh dùng làm đầu vào: `spo2_mean`, `spo2_min`, `spo2_valid`, `pulse_mean`, đúng định dạng firmware gửi lên mỗi phút. HR từ ECG chỉ dùng để làm sạch nhãn.

## Cấu trúc thư mục

```text
data/
  01_download.ipynb      tải numerics từ PhysioNet          -> data/raw_numerics, download_manifest.parquet
  02_standardise.ipynb   lưới 1 phút, lọc giá trị phi sinh lý -> data/clean_1min
  03_windowing.ipynb     nhãn nhiều T, baseline, cửa sổ 60'   -> data/windows_tc
  04_splitting.ipynb     chia train/val/test theo bệnh nhân   -> data/dataset_tc, splits_tc.parquet
  screening_checkpoint.parquet   danh sách bệnh nhân đã sàng lọc (đầu vào của 01, không commit)
  data/                  toàn bộ dữ liệu sinh ra (không commit)
model/
  tc_core.py             nhãn, đặc trưng tương đối với T, chuẩn hóa, lấy mẫu T (dùng chung data/model/firmware)
  eval_utils.py          AUPRC theo T có CI theo bệnh nhân, bảng ngưỡng, đơn điệu, hiệu chuẩn, đồ thị
  sim_alert.py           mô phỏng Watch/Alert/Critical: báo giả/ngày thật, thời gian báo trước
  test_tc_core.py        unit test (chạy: cd model && python test_tc_core.py)
  10_lightgbm_tc.ipynb   đối chứng: LightGBM có ràng buộc đơn điệu, luật, độ quan trọng đặc trưng
  11_hybrid_tc.ipynb     model chính: hybrid Conv1D + Dense nhận T, đánh giá G1–G10, export INT8
  models_v3/             model xuất cho thiết bị (.tflite, _norm.npz, _meta.json)
  export_firmware.py     sinh gói firmware/ từ models_v3 (C array, header, vector kiểm thử)
  runs/                  checkpoint và điểm số từng lần train (không commit)
firmware/                gói bàn giao cho team phần cứng: thư viện C, model INT8, ví dụ ESP32-C3 (xem firmware/README.md)
archive/v2_thr88/        bản cũ ngưỡng cố định 88% (notebook, model, kết quả đã báo cáo)
```

## Cách chạy

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Chạy notebook theo số thứ tự. Mỗi notebook chạy trong chính thư mục của nó và có resume: bước nào đã có đầu ra thì được bỏ qua.

1. `data/01_download.ipynb`, `data/02_standardise.ipynb`: chỉ cần khi chưa có `data/data/raw_numerics` và `clean_1min`.
2. `data/03_windowing.ipynb`, rồi `data/04_splitting.ipynb`, tạo ra `data/data/dataset_tc/`.
3. `model/10_lightgbm_tc.ipynb`: lưu điểm của LightGBM để notebook sau so sánh.
4. `model/11_hybrid_tc.ipynb`: train 3 seed, chọn ngưỡng trên val, **mở test một lần**, export vào `model/models_v3/`.
5. `cd model && python export_firmware.py`: sinh lại gói `firmware/` mỗi khi model đổi, rồi chạy kiểm thử C (xem `firmware/README.md`).

## Quy ước đánh giá

- Chia train/val/test 70/15/15 **theo bệnh nhân**. Val dùng để dừng sớm và chọn ngưỡng; test chỉ mở một lần khi đã chốt cấu hình.
- Chỉ số chính là AUPRC theo từng T, có khoảng tin cậy bootstrap theo bệnh nhân và hệ số so với ngẫu nhiên. T = 89 và 91 không dùng để train, chỉ để kiểm tra nội suy.
- Báo giả được báo cáo là **số cảnh báo thật sau persistence và cooldown** (`sim_alert`); con số đếm theo cửa sổ chỉ là cận trên.

## Hạn chế

MIMIC là bệnh nhân ICU đo bằng đầu kẹp ngón tay, còn thiết bị đo phản xạ ở cổ tay, nên có domain shift. Dữ liệu này chỉ dùng để tiền huấn luyện và chứng minh khái niệm. Chưa có mã ICD-9 nên chưa đánh giá riêng theo nhóm bệnh.

## Tuyên bố miễn trừ

Đây là nguyên mẫu nghiên cứu, chỉ để **hỗ trợ quyết định lâm sàng**. Không phải thiết bị y tế đã được chứng nhận và không được dùng làm căn cứ duy nhất để chẩn đoán hay điều trị.

## Giấy phép

- Mã nguồn: bảo lưu mọi quyền.
- Dữ liệu bên thứ ba (MIMIC-III Waveform Database Matched Subset, ODbL v1.0): xem [THIRD-PARTY-LICENSE.md](./THIRD-PARTY-LICENSE.md). Khi sử dụng phải ghi nguồn và trích dẫn theo yêu cầu trong file đó.
