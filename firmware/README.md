# Gói bàn giao firmware: cảnh báo sớm hạ SpO₂ (hybrid_tc)

Model `hybrid_tc-20261016-s3` · ESP32-C3 + MAX30102 · TFLite Micro INT8

Gói này chứa mọi thứ để chạy model cảnh báo sớm hạ SpO₂ trên vòng đeo:
- model INT8 dạng C array;
- thư viện C tiền xử lý và máy trạng thái cảnh báo, đã kiểm chứng khớp tuyệt đối với Python;
- vector kiểm thử mẫu;
- project mẫu ESP-IDF.

- **Bắt đầu từ đây:** [docs/HUONG_DAN_CAI_DAT.md](docs/HUONG_DAN_CAI_DAT.md) hướng dẫn từng bước cho MAX30102 + ESP32-C3 SuperMini: nối dây, cài ESP-IDF, build/nạp, driver cảm biến, gộp phút, kiểm tra trên thiết bị.
- Đặc tả chi tiết từng công thức nằm ở [docs/SPEC.md](docs/SPEC.md).

## 1. Model làm gì

Mỗi 5 phút, model nhận **60 phút SpO₂ và mạch gần nhất**, **ngưỡng T do bác sĩ đặt** (85–95%) và **baseline** của bệnh nhân. Đầu ra là xác suất trong 30 phút tới SpO₂ < T liên tục ≥ 3 phút.

- Một model dùng cho mọi T. Bác sĩ đổi T thì server chỉ gửi config mới; **không cần nạp lại firmware**.
- Bốn trạng thái cảnh báo:

| Trạng thái | Nguồn | Khi nào |
|---|---|---|
| `CRITICAL` | luật cố định | SpO₂ < 85% liên tục 3 phút với SQI tốt, hoặc mạch ngoài [40, 140] liên tục 3 phút |
| `ALERT` | luật **hoặc** ML | đang có sự kiện SpO₂ < T ≥ 3 phút, hoặc xác suất của model vượt ngưỡng Alert |
| `WATCH` | ML | xác suất của model vượt ngưỡng Watch |
| `UNKNOWN` | chất lượng tín hiệu | thiếu > 30% phút SpO₂ trong 60', hoặc SQI trung bình 10' gần nhất < 0,5 |

Kết quả trên tập test (dữ liệu MIMIC, bệnh nhân ICU): ở T = 88%, tầng Alert bắt được 23% sự kiện, báo trước ≥ 5 phút (trung vị 13 phút), với 1,17 báo giả/ngày. Luật đơn giản ở cùng mức báo giả chỉ bắt được 4%.

## 2. Nội dung gói

| Đường dẫn | Nội dung | Sửa tay? |
|---|---|---|
| `model/hybrid_tc_model_data.{h,cc}` | model INT8 dạng C array (20.216 byte, căn 16 byte) | không, sinh tự động |
| `include/spo2_tc_params.h` | chuẩn hóa, lượng tử hóa, **bảng T → ngưỡng int8**, cấu hình tầng mặc định | không, sinh tự động |
| `include/spo2_tc.h`, `src/spo2_tc.c` | thư viện C99: 23 đặc trưng, lượng tử hóa, baseline 24 giờ, máy trạng thái | có thể, nhưng phải chạy lại test |
| `test/golden_vectors.{h,json}` | 24 cửa sổ thật (tập val) kèm đặc trưng, đầu vào int8 và đầu ra int8 mong đợi | không |
| `test/golden_persistence.h` | chuỗi đầu ra model kèm thời điểm cảnh báo mong đợi (persistence, cooldown) | không |
| `test/test_host.c` | kiểm thử trên PC | |
| `examples/esp32c3/` | project ESP-IDF mẫu: TFLite Micro, tự kiểm tra lúc boot, vòng lặp mỗi phút | có, đây là điểm bắt đầu |
| `docs/HUONG_DAN_CAI_DAT.md` | hướng dẫn cài đặt trên MAX30102 + ESP32-C3 SuperMini | |
| `docs/SPEC.md` | đặc tả: dữ liệu vào, công thức, ngưỡng, logic cảnh báo, config | |
| `docs/CONFIG_VA_API.md` | nhận ngưỡng bác sĩ đặt, áp dụng vào engine, danh tính thiết bị, API gửi dữ liệu (BLE/WiFi), mẫu prompt nhờ AI | |

File sinh tự động được tạo bởi `model/export_firmware.py` từ `model/models_v3/`. Khi có model mới thì chạy lại script này, **không sửa tay**.

## 3. Phần cứng phải cung cấp gì

Mô hình được train trên dữ liệu có đúng định dạng dưới đây. Lệch định dạng là nguồn sai số lớn nhất khi chuyển sang thiết bị.

**Mỗi giây**, firmware tính từ RED/IR thô của MAX30102:
- SpO₂ đã làm mượt khoảng 8 giây (giống monitor ICU);
- nhịp mạch (HR từ PPG);
- SQI (chất lượng tín hiệu).

Giây không hợp lệ (SQI kém, mất tiếp xúc, cử động) bị **bỏ qua**, không ghi 0.

**Mỗi phút**, gộp 60 giây thành 4 số rồi gọi `spo2tc_engine_push_minute`:

| Kênh | Định nghĩa | Khi không có giây hợp lệ |
|---|---|---|
| `spo2_mean` | trung bình SpO₂ các giây hợp lệ (%) | `NAN` |
| `spo2_min` | nhỏ nhất SpO₂ các giây hợp lệ (%) | `NAN` |
| `spo2_valid` | số giây hợp lệ / 60 | `0` (không bao giờ `NAN`) |
| `pulse_mean` | trung bình nhịp mạch các giây hợp lệ (bpm) | `NAN` |

> **Quan trọng:** dùng `NAN` (trong `<math.h>`) cho giá trị thiếu. Ghi 0 sẽ bị hiểu thành SpO₂ = 0% và gây báo động giả.

## 4. Các bước tích hợp

**Bước 1. Chạy kiểm thử trên PC** (cần gcc), trong thư mục `firmware/`:

```bash
gcc -std=c99 -O2 -Wall -Iinclude src/spo2_tc.c test/test_host.c -lm -o test_host
```
```bash
./test_host
```

Kết quả mong đợi: `tab_q khớp 552/552`, `seq_q khớp 8640/8640`, cả 4 cấu hình persistence `khớp Python`, và dòng cuối `KẾT QUẢ: tất cả kiểm tra đạt`.

**Bước 2. Build project mẫu** (ESP-IDF ≥ 5.1). Component `espressif/esp-tflite-micro` được tải tự động qua `idf_component.yml`.

```bash
cd examples/esp32c3 && idf.py set-target esp32c3 && idf.py build flash monitor
```

**Bước 3. Kiểm tra log lúc boot.** Cần thấy hai dòng:
- `arena dùng N / 32768 byte`: ghi lại N, rồi giảm `kArenaSize` trong `model_runner.cc` xuống khoảng 1,2 × N;
- `self-test: 24/24 khớp | trung bình ... us/lần`: bắt buộc **24/24**. Nếu lệch, xem mục 7.

**Bước 4. Viết `sensor_read_minute()`** trong `main.cc`, gộp dữ liệu giây thành phút theo bảng ở mục 3.

**Bước 5. Nhận config từ server** (chi tiết: [docs/CONFIG_VA_API.md](docs/CONFIG_VA_API.md), §2–5).
- Config đến qua BLE CONFIG (app ghi), HTTPS `GET /api/app/v1/device/config`, hoặc MQTT `dev/{device_id}/config` (tùy chọn).
- Kiểm tra → lưu NVS → áp dụng ở đầu phút trong task chạy engine. Chỉ gọi `spo2tc_engine_set_T` khi **T đổi**; đổi tầng/critical thì gán thẳng `engine.cfg`.
- `model_version` hoặc ngưỡng INT8 gửi kèm không khớp firmware: vẫn áp dụng T cho luật, tắt ML, ack `applied_rules_only`.
- Báo lại `config_version` (STATUS qua BLE, hoặc `POST /api/app/v1/ingest/config-ack` qua WiFi).

**Bước 6. Gửi dữ liệu lên server** (chi tiết: [docs/CONFIG_VA_API.md](docs/CONFIG_VA_API.md), §6–7).
- Chế độ BLE: notify RECORDS / ASSESSMENT / STATUS cho app (đặc tả byte: `docs/ble-gatt.md` ở repo app).
- Chế độ WiFi: HTTPS `POST /api/app/v1/ingest/{telemetry,assessments,status}` với header `x-device-id`, `x-device-key`; xóa flash theo `acked_seq`.
- Thông báo `notify_alert` / `notify_critical` gửi **ngay**; kèm `prob`, `baseline`, `base_hours`, `config_version`, `model_version`.

## 5. Tài nguyên ước tính

| Hạng mục | Kích thước | Ghi chú |
|---|---|---|
| Flash: model | 20 KB | C array `g_hybrid_tc_model` |
| Flash: vector kiểm thử | khoảng 40 KB | có thể bỏ sau khi nghiệm thu (bỏ `self_test`) |
| RAM: tensor arena | ≤ 32 KB, dự kiến 12–16 KB | activation tối đa khoảng 10 KB; đo bằng log ở bước 3 |
| RAM: engine | khoảng 13 KB | cửa sổ 60' (1 KB) + vòng baseline 1.500 phút (6 KB) + mảng tạm tính trung vị (5,8 KB) |
| CPU | mỗi 5 phút một lần | đo bằng `self-test` (in ra µs/lần); dự kiến vài chục ms ở 160 MHz |

## 6. Nghiệm thu (checklist)

- [ ] `test_host` trên PC: tất cả kiểm tra đạt.
- [ ] Thiết bị: `self-test 24/24 khớp`; ghi lại arena đã dùng và thời gian mỗi lần chạy.
- [ ] Đặt T = 85 và T = 95 thì được chấp nhận; T = 84 và T = 97 bị từ chối, cấu hình cũ được giữ.
- [ ] Rút cảm biến 15 phút thì trạng thái chuyển sang `UNKNOWN`, không có thông báo ALERT/CRITICAL giả.
- [ ] Mô phỏng `spo2_mean = 82` trong 3 phút (SQI tốt) thì `CRITICAL` phát đúng ở phút thứ 3, và không phát lại trong 30 phút.
- [ ] `spo2_mean` thiếu thì ghi `NAN`, không ghi 0 (kiểm tra log bản ghi phút).
- [ ] Sau khi mất điện hoặc khởi động lại, engine bắt đầu từ trạng thái rỗng: cần 60 phút để có dự đoán ML đầu tiên, và 1 giờ để có baseline. Trong lúc đó vẫn có `CRITICAL` theo luật.

## 7. Khi kết quả không khớp

| Triệu chứng | Nguyên nhân hay gặp |
|---|---|
| `self-test` lệch ở **mọi** vector | header và model khác phiên bản (chạy lại `export_firmware.py` rồi build lại); nhầm thứ tự đầu vào `tab`/`seq` |
| lệch ở **một vài** vector | thư viện C bị sửa mà chưa chạy `test_host`; trình biên dịch bật `-ffast-math` (làm hỏng `isnan` và làm tròn) — **không bật** |
| `AllocateTensors` lỗi | `kArenaSize` quá nhỏ |
| `zero_point trong model khác` | header cũ: chạy lại `export_firmware.py` |
| báo giả nhiều trên thiết bị thật | xem mục 8 trong SPEC: ghi 0 thay vì `NAN`, SpO₂ chưa làm mượt 8 giây, SQI quá dễ dãi |

## 8. Các điểm cần hai team cùng chốt

1. **Ngưỡng Critical mặc định là 85%** (cấu hình được). Bác sĩ có thể muốn đặt theo từng bệnh nhân.
2. **ALERT theo luật** (đang có sự kiện SpO₂ < T ≥ 3 phút) là lựa chọn thiết kế của gói này. Trong khoảng thời gian đó model không chạy, vì đầu vào đã chứa sự kiện, giống lúc train.
3. **Baseline tham chiếu do bác sĩ xác nhận** mới chỉ để hiển thị. Model luôn dùng baseline tự tính, để giống lúc train.
4. **Domain shift:** model được train trên dữ liệu ICU đo ở đầu ngón tay; thiết bị đo phản xạ ở cổ tay. Cần đo thử khoảng 10 tình nguyện viên, so với máy kẹp ngón tay, trước khi tin vào ngưỡng.
