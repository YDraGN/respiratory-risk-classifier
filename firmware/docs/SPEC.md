# Đặc tả kỹ thuật: hybrid_tc trên thiết bị

Model `hybrid_tc-20261016-s3`. Bản tham chiếu là `model/tc_core.py` (đặc trưng, nhãn) và `model/sim_alert.py` (logic cảnh báo). Bản C trong `src/spo2_tc.c` khớp tuyệt đối với hai file này trên 24 vector kiểm thử mẫu và trên 3.648 cửa sổ baseline của dữ liệu thật.

## 1. Định nghĩa bài toán

- **Sự kiện tại ngưỡng T:** `spo2_mean < T` liên tục ≥ 3 phút. Phút thiếu (`NAN`) được tính là không thấp.
- **Làm sạch nhãn lúc train:** đoạn tụt chỉ được tính là sự kiện khi có ≥ 3 phút "sạch". Phút sạch là phút có mạch, và nếu có HR từ ECG thì |mạch − HR| ≤ 10 bpm. Trên thiết bị không có ECG, nên phút sạch đơn giản là phút có `pulse_mean`.
- **Dự đoán:** tại thời điểm t, dùng 60 phút [t−60, t) để ước lượng P(có sự kiện bắt đầu trong [t, t+30)).
- **Điều kiện để chạy model:**
  - ≥ 70% phút trong 60' có `spo2_mean`;
  - 60' đầu vào **chưa** chứa sự kiện < T. Nếu đã có, tầng ALERT theo luật xử lý.
- **T hợp lệ:** số nguyên từ 85 đến 95. Model được train ở T ∈ {86, 87, 88, 90, 92, 93, 94}; ở T = 89 và 91 (không train) nó nội suy đúng. Với T = 85 và 95, bảng ngưỡng lấy giá trị biên của 86 và 94.

## 2. Dữ liệu vào mỗi phút

Mảng `float m[4] = { spo2_mean, spo2_min, spo2_valid, pulse_mean }`.

| Kênh | Đơn vị | Giá trị thiếu | Ghi chú |
|---|---|---|---|
| `spo2_mean` | % | `NAN` | trung bình các giây hợp lệ của SpO₂ đã làm mượt khoảng 8 giây |
| `spo2_min` | % | `NAN` | nhỏ nhất các giây hợp lệ |
| `spo2_valid` | 0..1 | `0` | số giây hợp lệ / 60 |
| `pulse_mean` | bpm | `NAN` | trung bình nhịp mạch từ PPG |

Cửa sổ 60' được lưu theo thứ tự cũ → mới: `x[0]` là phút t−60, `x[59]` là phút t−1.

## 3. Đầu vào model

### 3.1 `seq`: int8 [1, 60, 6]

Với mỗi phút i và mỗi kênh c ∈ {0, 1, 2, 3}:

```text
v = x[i][c] − (c < 2 ? T : 0)          // hai kênh SpO2 tính tương đối với T
z = (v − SEQ_MU[c]) / SEQ_SD[c]         // float32
z = isnan(z) ? 0 : z
seq[i][c] = q(z, SEQ_SCALE, SEQ_ZP)
seq[i][4] = q(isnan(x[i][0]) ? 1 : 0, ...)   // mask spo2_mean
seq[i][5] = q(isnan(x[i][3]) ? 1 : 0, ...)   // mask pulse_mean
```

### 3.2 `tab`: int8 [1, 23]

Các thống kê "nan" bỏ qua phút thiếu. Giá trị là NAN khi không có phút hợp lệ nào. Phép tính dùng double, sau đó ép về float.

| # | Tên | Công thức (SP = `spo2_mean`, MN = `spo2_min`, VA = `spo2_valid`, PU = `pulse_mean`, chỉ số phút 0..59) |
|---|---|---|
| 0 | `sp_mean_rT` | nanmean(SP) − T |
| 1 | `sp_p10_rT` | phân vị 10 của SP (nội suy tuyến tính như numpy) − T |
| 2 | `mn_min60_rT` | nanmin(MN) − T |
| 3 | `mn_min10_rT` | nanmin(MN[50..59]) − T |
| 4 | `sp_last5_rT` | nanmean(SP[55..59]) − T |
| 5–7 | `frac_lt_T`, `frac_lt_T2`, `frac_lt_T4` | (số phút SP < T, T+2, T+4) / max(số phút có SP, 1) |
| 8 | `sp_slope60` | độ dốc hồi quy tuyến tính của SP[0..59] theo phút (t = 0..59); NAN nếu < 5 điểm |
| 9 | `sp_slope30` | như trên với SP[30..59] (t = 0..29) |
| 10 | `sp_d_last10_first30` | nanmean(SP[50..59]) − nanmean(SP[0..29]) |
| 11 | `sp_std60` | độ lệch chuẩn tổng thể (ddof = 0) của SP[0..59] |
| 12 | `sp_std15` | độ lệch chuẩn của SP[45..59] |
| 13 | `pu_mean` | nanmean(PU) |
| 14 | `pu_slope30` | độ dốc của PU[30..59] |
| 15 | `pu_d_last10_first30` | nanmean(PU[50..59]) − nanmean(PU[0..29]) |
| 16 | `pu_std60` | độ lệch chuẩn của PU |
| 17 | `base_rT` | b − T |
| 18 | `last5_rbase` | nanmean(SP[55..59]) − b |
| 19 | `base_hours` | h |
| 20 | `valid_mean` | mean(VA) |
| 21 | `sp_nan_frac` | số phút SP thiếu / 60 |
| 22 | `T` | T |

Trong đó (b, h) lấy từ baseline (§4). Nếu b là NAN hoặc h < 1 giờ thì dùng **b = nanmean(SP), h = 0**.

Sau đó: `v = isnan(f) ? TAB_MED[i] : f`, rồi `z = clip((v − TAB_MU[i]) / TAB_SD[i], −5, 5)`, rồi `tab[i] = q(z, TAB_SCALE, TAB_ZP)`.

### 3.3 Lượng tử hóa

`q(z, s, zp) = clamp(rint(z / s) + zp, −128, 127)`, với `rint` làm tròn về số chẵn gần nhất (giống `numpy.round`). **Không** dùng `roundf`, và **không** bật `-ffast-math`.

### 3.4 Đầu ra

Đầu ra là `int8 q_out`, với `logit = (q_out − OUT_ZP) × OUT_SCALE` và `p = sigmoid(logit)`. Xác suất p chỉ để hiển thị. Quyết định cảnh báo so sánh trực tiếp `q_out` với ngưỡng int8.

Thứ tự tensor đầu vào trong file `.tflite` là `tab` trước, `seq` sau. Code mẫu nhận diện theo số chiều (2 chiều là `tab`, 3 chiều là `seq`).

## 4. Baseline

- Lưu `spo2_mean` của 1.500 phút gần nhất. Phút có `spo2_valid < 0.5` hoặc thiếu SpO₂ được ghi `NAN`.
- Baseline tại thời điểm chạy model là **trung vị** các phút không thiếu trong **1.440 phút (24 giờ) kết thúc tại đầu cửa sổ 60'**, tức là **không gồm** 60 phút đầu vào. h = số phút đó / 60.
- **Đóng băng:** khi trạng thái là ALERT hoặc CRITICAL, giữ nguyên giá trị baseline đã chụp lúc vào trạng thái, để đợt tụt không kéo baseline xuống. Hết ALERT/CRITICAL thì tính lại bình thường.
- Mới đeo (< 1 giờ dữ liệu): dùng quy tắc dự phòng ở §3.2, giống lúc train.

## 5. Logic cảnh báo (mỗi phút)

```text
push phút vào cửa sổ 60' và vòng baseline;  t = t + 1

# Sự kiện < T đang diễn ra
low = SP < T (SP thiếu = không thấp);   low_run, low_clean (số phút có mạch) đếm liên tiếp
event_now = low_run >= 3 và low_clean >= 3     → đánh dấu cả đoạn tụt trong cửa sổ là "sự kiện"
ALERT theo luật: event_now; thông báo ở phút thứ 3 của đoạn (theo cooldown của Alert)

# CRITICAL (luật, luôn chạy, kể cả khi UNKNOWN)
crit_run: SP < crit_T và spo2_valid >= crit_sqi liên tiếp;   hr_run: mạch < hr_low hoặc > hr_high liên tiếp
thông báo khi crit_run hoặc hr_run vừa đạt n_min và đã qua crit_cooldown kể từ lần trước
trạng thái CRITICAL khi crit_run >= n_min hoặc hr_run >= n_min

# Chất lượng tín hiệu
poor = t < 60  hoặc  số phút có SP trong 60' < 42  hoặc  trung bình spo2_valid 10' gần nhất < 0.5

# ML: mỗi 5 phút (t % 5 == 0, t >= 60)
nếu không poor và cửa sổ không chứa sự kiện:
    q = model(tab, seq)
    ew = q > WATCH_THR_Q[T];  ea = q > ALERT_THR_Q[T]
    lưu (t, ew, ea) vào lịch sử
    watch_on = ew và (số lần ew trong các lần đánh giá có t0 > t − n*5) >= k        (k, n của Watch)
    alert_on = tương tự với ea                                                       (k, n của Alert)
    thông báo khi *_on và t − lần thông báo trước >= cooldown
    mức ML = ALERT / WATCH / NORMAL, giữ tới lần đánh giá sau
ngược lại: mức ML = NORMAL (không chạy model)

# Trạng thái hiển thị (ưu tiên từ trên xuống)
CRITICAL > ALERT (luật hoặc ML) > WATCH > UNKNOWN (nếu poor) > NORMAL
```

Lịch sử persistence và cờ sự kiện được xóa khi đổi T (`spo2tc_engine_set_T`).

Cấu hình tầng mặc định (chọn trên tập val):

| Tầng | Ngân sách chọn ngưỡng | k / n | Cooldown |
|---|---|---|---|
| Watch | 3 báo giả/ngày (val) | 1 / 1 | 30 phút |
| Alert | 1 báo giả/ngày (val) | 1 / 1 | 60 phút |
| Critical | luật: SpO₂ < 85% hoặc mạch ∉ [40, 140], 3 phút, SQI ≥ 0,5 | | 30 phút |

### 5.1 Bảng T → ngưỡng (trích từ `spo2_tc_params.h`)

| T | 85 | 86 | 87 | 88 | 89 | 90 | 91 | 92 | 93 | 94 | 95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Watch `q >` | 54 | 54 | 63 | 71 | 79 | 87 | 94 | 101 | 106 | 110 | 110 |
| Alert `q >` | 71 | 71 | 79 | 86 | 93 | 101 | 105 | 109 | 114 | 117 | 117 |
| Watch p | 0,044 | 0,044 | 0,064 | 0,091 | 0,122 | 0,161 | 0,207 | 0,261 | 0,305 | 0,353 | 0,353 |
| Alert p | 0,091 | 0,091 | 0,120 | 0,155 | 0,203 | 0,261 | 0,300 | 0,343 | 0,393 | 0,414 | 0,414 |

Ngưỡng tăng theo T vì tỷ lệ sự kiện tăng theo T. Ngưỡng được chọn sao cho báo giả/ngày gần như không đổi giữa các T.

### 5.2 Hiệu năng mong đợi (test, dữ liệu ICU)

| T | Tầng | Báo giả/ngày (TB) | Báo giả/ngày (p90 bệnh nhân) | Precision | Bắt sự kiện (báo trước ≥ 5') | Báo trước trung vị |
|---|---|---|---|---|---|---|
| 88 | Alert | 1,17 | 3,4 | 12% | 23% | 13' |
| 88 | Watch | 3,40 | 9,6 | 9% | 53% | 15' |
| 92 | Alert | 0,90 | 3,6 | 36% | 20% | 11' |
| 92 | Watch | 2,81 | 9,4 | 23% | 35% | 12' |

Các số trên là của ensemble 3 seed trong `model/11_hybrid_tc.ipynb` (mục 9.3). Thiết bị chạy **một seed (seed 3), bản INT8**, với bảng ngưỡng chọn lại trên điểm INT8 của val. AUPRC trung bình trên test ở các T đã train: bản float 0,147, bản INT8 0,144. Hiệu năng vận hành vì vậy dự kiến tương đương nhưng chưa được đo riêng.

Tầng ML chỉ phủ các sự kiện đến sau một đoạn đầu vào sạch. Sự kiện nối tiếp ngay sau một sự kiện khác do ALERT/CRITICAL theo luật đảm nhận.

## 6. Config từ server

Topic `dev/{id}/config`, JSON:

```json
{
  "version": 12,
  "model_version": "hybrid_tc-20261016-s3",
  "T": 90,
  "tiers": {
    "watch": { "k": 1, "n": 1, "cooldown": 30 },
    "alert": { "k": 1, "n": 1, "cooldown": 60 }
  },
  "critical": { "crit_T": 85, "n_min": 3, "sqi_min": 0.5, "hr_low": 40, "hr_high": 140, "cooldown": 30 }
}
```

Quy tắc xử lý:
- `model_version` khác `SPO2TC_MODEL_VERSION`: **từ chối** cả config, vì bảng ngưỡng gắn với model.
- `T` ngoài 85–95: từ chối (web cũng phải chặn).
- Ngưỡng int8 lấy từ `SPO2TC_*_THR_Q[T − 85]` trong firmware, không gửi qua mạng. Muốn hiệu chỉnh ngưỡng theo phản hồi thực tế (đổi bảng), dùng cơ chế OTA của header, kèm `model_version` mới.
- Áp dụng xong thì trả về `{"ack_version": 12}`.

## 7. Kiểm chứng

| Mức | Cách | Tiêu chí |
|---|---|---|
| Thư viện C trên PC | `test/test_host.c` | 552/552 `tab_q`, 8.640/8.640 `seq_q`, 4/4 cấu hình persistence khớp Python, mọi luật đạt |
| Model trên thiết bị | `self_test()` trong `examples/esp32c3/main/main.cc` | 24/24 `q_out` khớp (đầu ra kỳ vọng tính bằng kernel tham chiếu của TFLite, giống TFLite Micro) |
| Hệ thống | đeo thử, so với máy kẹp ngón tay | Bland-Altman cho SpO₂; đếm số cảnh báo/ngày |

Đã kiểm chứng trên PC: mọi kiểm tra ở mức 1 đạt; thư viện C và header biên dịch, link được dưới C99 và C++17. **Chưa kiểm chứng:** build ESP-IDF và chạy trên ESP32-C3 thật (arena thực tế, thời gian chạy, 24/24 trên thiết bị).

## 8. Lưu ý để không lệch so với lúc train

- **`NAN` chứ không phải 0** cho giá trị thiếu. Đây là lỗi gây báo giả nhiều nhất.
- SpO₂ **làm mượt khoảng 8 giây** trước khi gộp phút. SpO₂ thô từng nhịp nhiễu hơn nhiều so với dữ liệu train.
- SQI quá dễ dãi sẽ đưa nhiễu cử động vào `spo2_min` và làm tăng báo giả. SQI quá khắt khe sẽ làm tăng UNKNOWN.
- Không dùng HR từ cảm biến khác thay cho mạch PPG trong `pulse_mean`.
- Khi đổi model: chạy lại `model/export_firmware.py`, chạy `test_host`, build lại, kiểm tra `self-test 24/24`.
