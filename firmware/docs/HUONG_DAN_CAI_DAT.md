# Hướng dẫn cài đặt model trên MAX30102 + ESP32-C3 SuperMini

Tài liệu dành cho team phần cứng: đi từ nối dây tới khi vòng đeo chạy model `hybrid_tc` và phát cảnh báo. Đặc tả công thức đầy đủ nằm ở [SPEC.md](SPEC.md); tổng quan gói nằm ở [../README.md](../README.md).

> **Trạng thái kiểm chứng.**
> - **Đã kiểm chứng trên PC:** thư viện C (`src/spo2_tc.c`) và các header sinh tự động khớp tuyệt đối với Python; biên dịch và link được dưới C99 và C++17.
> - **Chưa chạy trên thiết bị thật:** project `examples/esp32c3`, các đoạn code driver MAX30102 và xử lý tín hiệu trong tài liệu này. Đây là khung để team hoàn thiện; các giá trị đánh dấu ⚠ phải được đo và chỉnh trên phần cứng thật.

## 0. Tổng quan luồng dữ liệu

```text
MAX30102 (RED + IR, 100 mẫu/s, trung bình 4 → 25 Hz)
   │  I2C 400 kHz, đọc FIFO
   ▼
Xử lý MỖI GIÂY (team phần cứng viết):  phát hiện có ngón tay/cổ tay, SQI, SpO2 từ tỷ số R, nhịp tim, làm mượt SpO2 8 giây
   │  giây không hợp lệ → bỏ qua (không ghi 0)
   ▼
Gộp MỖI PHÚT:  spo2_mean, spo2_min, spo2_valid, pulse_mean   (NAN nếu không có giây hợp lệ)
   │
   ▼
spo2tc_engine_push_minute()   ← thư viện có sẵn trong gói
   ├─ mỗi phút: luật CRITICAL, ALERT theo luật, UNKNOWN
   └─ mỗi 5 phút: 23 đặc trưng + chuỗi 60' → TFLite Micro (model 20 KB) → so với bảng ngưỡng của T
   ▼
level + thông báo (WATCH / ALERT / CRITICAL) → gửi server / rung / LED
```

## 1. Phần cứng

### 1.1 Linh kiện

| Linh kiện | Ghi chú |
|---|---|
| ESP32-C3 SuperMini | RISC-V 160 MHz, 400 KB SRAM, 4 MB flash, USB-C có sẵn USB Serial/JTAG |
| Module MAX30102 | loại có LDO 1,8 V và điện trở kéo lên I2C (module tím GY-MAX30102 hoặc module đen) |
| Pin LiPo 300 mAh + mạch sạc | cấp vào chân 5V/BAT theo sơ đồ nguồn của board |
| Dây nối ngắn | I2C càng ngắn càng tốt (< 10 cm) |

### 1.2 Nối dây

Mình đề xuất **tránh các chân strapping** của ESP32-C3 (GPIO2, GPIO8, GPIO9). Trên SuperMini, GPIO8 nối với LED và GPIO9 là nút BOOT.

| MAX30102 | ESP32-C3 SuperMini | Ghi chú |
|---|---|---|
| VIN | 3V3 | |
| GND | GND | |
| SDA | **GPIO6** | đổi trong `#define PIN_SDA` nếu cần |
| SCL | **GPIO7** | `#define PIN_SCL` |
| INT | **GPIO5** | không bắt buộc (có thể đọc FIFO theo chu kỳ) |

> ⚠ **Module tím GY-MAX30102:** nhiều module bán lẻ nối điện trở kéo lên I2C vào đường 1,8 V. Mạch vẫn thường chạy được với ESP32 3,3 V nhưng không ổn định. Nếu `PART_ID` đọc sai hoặc I2C lỗi chập chờn, hãy chuyển điện trở kéo lên sang 3,3 V (theo hướng dẫn sửa module) hoặc dùng module khác.

### 1.3 Đặt cảm biến trên cổ tay

- Cảm biến áp sát da ở **mặt trên cổ tay**, cách xương cổ tay khoảng 1–2 cm. Dây đeo vừa chặt, không để ánh sáng ngoài lọt vào.
- Đo phản xạ ở cổ tay cho tín hiệu AC yếu hơn nhiều so với đầu ngón tay, nên dòng LED thường phải cao hơn (mục 4).
- Model được train trên dữ liệu đo ở **đầu ngón tay** của bệnh nhân ICU. Phải hiệu chuẩn và đối chiếu với máy kẹp ngón tay (mục 8) trước khi tin vào số liệu.

## 2. Cài phần mềm (Windows)

1. Cài **ESP-IDF ≥ 5.1**, chọn một trong hai cách:
   - Bộ cài *ESP-IDF Tools Installer* cho Windows của Espressif (tạo sẵn "ESP-IDF PowerShell"), hoặc
   - VS Code + extension **ESP-IDF** (Espressif), chọn *Express install*.
2. Mở **ESP-IDF PowerShell** (hoặc terminal ESP-IDF trong VS Code) và kiểm tra:

   ```bash
   idf.py --version
   ```

3. Lấy code của repo (nhánh `nganntb/data-reprocess`). Thư mục cần dùng là `firmware/`.
4. Component **esp-tflite-micro** được ESP-IDF tự tải ở lần build đầu, theo `examples/esp32c3/main/idf_component.yml`. Máy cần có mạng ở lần build đó.

## 3. Build và nạp project mẫu (chưa có cảm biến)

Bước này kiểm tra model chạy đúng trên chip, trước khi viết driver.

```bash
cd firmware/examples/esp32c3
```
```bash
idf.py set-target esp32c3
```
```bash
idf.py menuconfig
```

Trong `menuconfig`, đặt:

| Mục | Giá trị | Lý do |
|---|---|---|
| Component config → ESP System Settings → Channel for console output | **USB Serial/JTAG** | SuperMini không có chip USB-UART riêng |
| Component config → ESP System Settings → CPU frequency | 160 MHz | |
| Serial flasher config → Flash size | 4 MB | |
| Compiler options → Optimization Level | Performance (-O2) | không bật tùy chọn kiểu `-ffast-math` |

Build, nạp và xem log (thay `COM5` bằng cổng thật trong Device Manager):

```bash
idf.py build flash monitor -p COM5
```

Nếu không nạp được: giữ nút **BOOT**, cắm USB (hoặc bấm RESET trong lúc giữ BOOT), thả nút, rồi nạp lại.

**Log mong đợi:**

```text
I (xxx) model: hybrid_tc-20261016-s3 | model 20216 byte | arena dùng NNNN / 32768 byte
I (xxx) spo2tc: self-test: 24/24 khớp | trung bình NNNN us/lần (tiền xử lý + model)
```

- **Bắt buộc 24/24.** Nếu lệch, xem mục 7 trong [../README.md](../README.md).
- Ghi lại `arena dùng` và `us/lần` vào báo cáo. Sau đó giảm `kArenaSize` trong `main/model_runner.cc` xuống khoảng 1,2 × giá trị đo được.

## 4. Driver MAX30102

### 4.1 Cấu hình thanh ghi đề xuất

| Thanh ghi | Địa chỉ | Giá trị | Ý nghĩa |
|---|---|---|---|
| MODE_CONFIG | 0x09 | 0x40 rồi 0x03 | reset, sau đó chế độ SpO2 (RED + IR) |
| FIFO_CONFIG | 0x08 | 0x5F | trung bình 4 mẫu, cho phép ghi vòng, ngắt khi FIFO gần đầy |
| SPO2_CONFIG | 0x0A | 0x27 | dải ADC 4096 nA, 100 mẫu/s, độ rộng xung 411 µs (18 bit) |
| LED1_PA (RED) | 0x0C | ⚠ 0x24 (≈ 7 mA) | tăng cho cổ tay; chỉnh sao cho DC của IR ở khoảng giữa dải ADC |
| LED2_PA (IR) | 0x0D | ⚠ 0x24 | như trên |
| PART_ID | 0xFF | đọc phải ra 0x15 | kiểm tra kết nối |

Địa chỉ I2C 7 bit là `0x57`. Mỗi mẫu FIFO dài 6 byte: 3 byte RED rồi 3 byte IR, mỗi giá trị lấy 18 bit (`& 0x3FFFF`). Con trỏ FIFO: `FIFO_WR_PTR` 0x04, `OVF_COUNTER` 0x05, `FIFO_RD_PTR` 0x06, dữ liệu ở `FIFO_DATA` 0x07. Với cấu hình trên, tốc độ đầu ra hiệu dụng là **25 mẫu/s**.

### 4.2 Khung code (ESP-IDF ≥ 5.2, driver `i2c_master`)

```c
// max30102.c - khung mẫu, CHƯA chạy trên thiết bị
#include "driver/i2c_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#define PIN_SDA 6
#define PIN_SCL 7
#define MAX_ADDR 0x57
static i2c_master_dev_handle_t dev;

static esp_err_t wr(uint8_t reg, uint8_t v) { uint8_t b[2] = {reg, v}; return i2c_master_transmit(dev, b, 2, 100); }
static esp_err_t rd(uint8_t reg, uint8_t *v, size_t n) { return i2c_master_transmit_receive(dev, &reg, 1, v, n, 100); }

esp_err_t max30102_init(void) {
  i2c_master_bus_config_t bc = { .i2c_port = 0, .sda_io_num = PIN_SDA, .scl_io_num = PIN_SCL,
                                 .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
                                 .flags.enable_internal_pullup = true };
  i2c_master_bus_handle_t bus; ESP_ERROR_CHECK(i2c_new_master_bus(&bc, &bus));
  i2c_device_config_t dc = { .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = MAX_ADDR, .scl_speed_hz = 400000 };
  ESP_ERROR_CHECK(i2c_master_bus_add_device(bus, &dc, &dev));
  uint8_t id = 0; rd(0xFF, &id, 1); if (id != 0x15) return ESP_ERR_NOT_FOUND;
  wr(0x09, 0x40); vTaskDelay(pdMS_TO_TICKS(10));        // reset
  wr(0x04, 0); wr(0x05, 0); wr(0x06, 0);                // xóa FIFO
  wr(0x08, 0x5F); wr(0x0A, 0x27); wr(0x0C, 0x24); wr(0x0D, 0x24);
  return wr(0x09, 0x03);                                // chế độ SpO2
}

// Đọc mọi mẫu đang có trong FIFO; trả về số mẫu (tối đa 32).
int max30102_read(uint32_t *red, uint32_t *ir, int max_n) {
  uint8_t w, r; rd(0x04, &w, 1); rd(0x06, &r, 1);
  int n = (w - r) & 0x1F; if (n > max_n) n = max_n;
  for (int i = 0; i < n; i++) {
    uint8_t b[6]; rd(0x07, b, 6);
    red[i] = ((b[0] << 16) | (b[1] << 8) | b[2]) & 0x3FFFF;
    ir[i]  = ((b[3] << 16) | (b[4] << 8) | b[5]) & 0x3FFFF;
  }
  return n;
}
```

Gọi `max30102_read` mỗi khoảng 200–500 ms, hoặc khi chân INT báo FIFO gần đầy, rồi đẩy mẫu vào bộ đệm 25 Hz của bước xử lý mỗi giây.

## 5. Xử lý mỗi giây (team phần cứng hoàn thiện)

Model đã được train trên SpO₂ của **monitor ICU**: giá trị đã làm mượt khoảng 8 giây, và giây nhiễu đã bị loại. Mỗi giây, xử lý trên cửa sổ trượt khoảng 4–8 giây gần nhất (100–200 mẫu ở 25 Hz):

1. **Có tiếp xúc da không:** DC của IR > ⚠ ngưỡng (đo thực tế khi đeo và khi tháo). Không có tiếp xúc thì giây đó không hợp lệ.
2. **Tách DC và AC:** lọc thông thấp (hoặc trung bình trượt khoảng 1 s) để lấy DC; AC = tín hiệu − DC, rồi lọc thông dải 0,5–4 Hz.
3. **Nhịp tim:** phát hiện đỉnh trên AC của IR; HR = 60 / khoảng cách đỉnh trung bình. Chỉ nhận HR trong khoảng 30–220.
4. **Tỷ số R** = (AC_rms_RED / DC_RED) / (AC_rms_IR / DC_IR).
5. **SpO₂ = a − b·R** ⚠. Hệ số a, b **phải hiệu chuẩn** cho cổ tay và LED của mạch (mục 8). Công thức phổ biến cho đầu ngón tay (khoảng 110 − 25R) chỉ dùng tạm để chạy thử.
6. **SQI** (giây hợp lệ khi đạt hết): có tiếp xúc; chỉ số tưới máu (AC/DC của IR) > ⚠ ngưỡng; các khoảng giữa hai đỉnh đều nhau (độ lệch chuẩn / trung bình < ⚠ 0,15–0,2); R nằm trong khoảng hợp lý; SpO₂ ∈ [50, 100]. Nếu có gia tốc kế, thêm điều kiện không có cử động mạnh.
7. **Làm mượt 8 giây:** SpO₂ báo ra của giây = trung bình SpO₂ các giây hợp lệ trong 8 giây gần nhất. Không có giây hợp lệ nào thì giây đó không hợp lệ.

> **Quy tắc vàng:** giây không hợp lệ thì **bỏ qua**, không ghi 0 và không giữ giá trị cũ. Model hiểu giá trị thiếu qua `NAN` và `spo2_valid`.

## 6. Gộp phút và nối vào model

Bộ gộp này nối trực tiếp với thư viện, theo đúng định nghĩa dữ liệu train:

```c
// minute_agg.c - gộp 60 giây thành một bản ghi phút cho spo2tc_engine_push_minute
#include <math.h>
#include "spo2_tc.h"
typedef struct { double sp_sum, pu_sum; float sp_min; int n_sp, n_pu, n_sec; } minute_acc_t;

void acc_reset(minute_acc_t *a) { a->sp_sum = a->pu_sum = 0; a->sp_min = INFINITY; a->n_sp = a->n_pu = a->n_sec = 0; }

// gọi MỖI GIÂY; valid = kết quả SQI của giây đó
void acc_add_second(minute_acc_t *a, int valid, float spo2_smoothed, float hr) {
  a->n_sec++;
  if (!valid) return;                                       // giây kém: bỏ, không ghi 0
  a->sp_sum += spo2_smoothed; a->n_sp++;
  if (spo2_smoothed < a->sp_min) a->sp_min = spo2_smoothed;
  if (isfinite(hr)) { a->pu_sum += hr; a->n_pu++; }
}

// gọi khi đủ 60 giây
void acc_finish(const minute_acc_t *a, float m[SPO2TC_N_CH]) {
  m[SPO2TC_SPO2_MEAN]  = a->n_sp ? (float)(a->sp_sum / a->n_sp) : NAN;
  m[SPO2TC_SPO2_MIN]   = a->n_sp ? a->sp_min : NAN;
  m[SPO2TC_SPO2_VALID] = a->n_sp / 60.0f;                   // tỷ lệ giây hợp lệ, không bao giờ NAN
  m[SPO2TC_PULSE_MEAN] = a->n_pu ? (float)(a->pu_sum / a->n_pu) : NAN;
}
```

Trong `examples/esp32c3/main/main.cc`, thay hàm stub `sensor_read_minute()` bằng `acc_finish()` của phút vừa xong. Vòng lặp mỗi phút đã gọi sẵn engine và xử lý thông báo:

```c
spo2tc_result_t r = spo2tc_engine_push_minute(&engine, m, model_runner_invoke, NULL);
if (r.notify_critical) { /* rung mạnh + gửi CRITICAL */ }
if (r.notify_alert)    { /* gửi ALERT (r.alert_by_rule: do luật hay do ML) */ }
if (r.notify_watch)    { /* gửi WATCH */ }
```

**Đổi T khi bác sĩ cập nhật config** (xem mục 6 trong SPEC):

```c
if (spo2tc_engine_set_T(&engine, new_T) != 0) { /* T ngoài 85..95: từ chối, báo lỗi về server */ }
```

## 7. Bộ nhớ, thời gian, năng lượng

| Hạng mục | Ước tính | Cách đo |
|---|---|---|
| Flash | model 20 KB + TFLite Micro + vector kiểm thử khoảng 40 KB (bỏ được sau nghiệm thu) | `idf.py size` |
| RAM | arena ≤ 32 KB (dự kiến 12–16 KB) + engine khoảng 13 KB | log `arena dùng` |
| Thời gian | model chạy mỗi 5 phút, dự kiến vài chục ms | log `us/lần` |

Gợi ý tiết kiệm pin 300 mAh:
- Dùng **light sleep** giữa các lần đọc FIFO; FIFO 32 mẫu ở 25 Hz chứa đủ khoảng 1,2 giây dữ liệu.
- Chỉ bật WiFi/BLE theo đợt, gửi dữ liệu phút theo lô (ví dụ mỗi 5–15 phút). **Gửi thông báo ALERT/CRITICAL ngay lập tức.**
- Dòng LED là chi phí lớn nhất của cảm biến. Chỉnh xuống mức thấp nhất mà vẫn giữ SQI tốt.

## 8. Kiểm tra trên thiết bị

| # | Kiểm tra | Đạt khi |
|---|---|---|
| 1 | `self-test` lúc boot | 24/24 khớp |
| 2 | Đọc `PART_ID` | 0x15 |
| 3 | In RED/IR thô qua serial, vẽ đồ thị (ví dụ bằng Serial Plotter) | thấy rõ sóng mạch khi đeo; DC không chạm trần 262.143 |
| 4 | So SpO₂ và nhịp tim với máy kẹp ngón tay, khoảng 10 người, mỗi người vài phút nghỉ và vài phút nín thở nhẹ | sai lệch nhỏ trên đồ thị Bland-Altman; dùng dữ liệu này để **hiệu chuẩn a, b** ở mục 5 |
| 5 | Tháo vòng 15 phút | trạng thái `UNKNOWN`, không có ALERT/CRITICAL giả |
| 6 | Bản firmware thử nghiệm bơm phút giả `spo2_mean = 82` trong 3 phút | `CRITICAL` phát đúng ở phút thứ 3, không phát lại trong 30 phút |
| 7 | Đeo liên tục 24 giờ cho người khỏe | báo giả/ngày ở T = 92 nằm trong khoảng ngân sách (khoảng 1/ngày cho Alert) |
| 8 | Đổi T qua config: 85 và 95 được nhận; 84 và 97 bị từ chối | |

## 9. Xử lý sự cố

| Hiện tượng | Kiểm tra |
|---|---|
| `PART_ID` khác 0x15 hoặc I2C timeout | dây SDA/SCL, nguồn 3V3, điện trở kéo lên của module (mục 1.2) |
| IR DC chạm trần (262.143) | giảm LED_PA hoặc dải ADC |
| Sóng mạch quá nhỏ ở cổ tay | tăng LED_PA từng bước; siết dây đeo; che sáng |
| `self-test` lệch | header và model khác phiên bản → chạy lại `model/export_firmware.py`, build lại |
| Luôn `UNKNOWN` | SQI quá khắt khe, hoặc ghi `spo2_valid` sai (phải là tỷ lệ giây hợp lệ, từ 0 đến 1) |
| Báo giả nhiều | ghi 0 thay vì `NAN`; chưa làm mượt 8 giây; SQI để lọt nhiễu cử động; a, b chưa hiệu chuẩn (SpO₂ bị lệch thấp) |
| Không thấy log | console chưa chuyển sang USB Serial/JTAG (mục 3) |

## 10. Khi có model mới

1. Train lại và export vào `model/models_v3/`.
2. Chạy lại `cd model && python export_firmware.py`.
3. Chạy `test_host` trên PC (xem [../README.md](../README.md), mục 4).
4. Build, nạp, kiểm tra `self-test 24/24` và `model_version` mới trong log.
5. Server gửi config với `model_version` mới. Thiết bị từ chối config có `model_version` khác bản đang chạy.
