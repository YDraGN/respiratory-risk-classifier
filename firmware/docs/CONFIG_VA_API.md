# Config ngưỡng và kết nối server cho vòng đeo tay

> Đọc sau [SPEC.md](SPEC.md) và [HUONG_DAN_CAI_DAT.md](HUONG_DAN_CAI_DAT.md).
> Tài liệu này mô tả phần firmware **phải làm thêm** để: (1) nhận ngưỡng T bác sĩ đặt trên web, (2) áp dụng an toàn vào engine `spo2_tc`, (3) báo lại cho server/app, (4) gửi dữ liệu lên server qua BLE (app làm gateway) hoặc WiFi.
> Đặc tả byte BLE đầy đủ nằm ở repo app: `ITxMech_RespiratoryCare/docs/ble-gatt.md`. Tài liệu này là nguồn chuẩn cho **ý nghĩa** từng trường config; nếu hai file lệch nhau, báo đội App sửa.
>
> Phiên bản: config.v1 · model `hybrid_tc-20261016-s3` · cập nhật 2026-10-16.

---

## 0. Tóm tắt cho người vội

1. Thiết bị có **danh tính riêng** (`device_id` + `device_key`) lưu trong NVS. Mọi request và topic đều gắn `device_id`. Firmware **không biết** bệnh nhân là ai; server gắn dữ liệu vào bệnh nhân theo bảng phân công thiết bị.
2. Bác sĩ đổi T trên web → server tăng `config_version` → config đến vòng theo 1 trong 3 đường: BLE CONFIG (app ghi xuống), HTTPS khi vòng tự lên mạng, hoặc MQTT retained (tùy chọn).
3. Firmware **kiểm tra** config → **lưu NVS** → **áp dụng ở ranh giới phút** (trong cùng task với `spo2tc_engine_push_minute`) → **báo lại** `config_version` đang dùng.
4. Ngưỡng INT8 của model **lấy từ bảng trong firmware** (`SPO2TC_*_THR_Q[T-85]`). Giá trị server gửi kèm chỉ để **đối chiếu**: lệch nghĩa là server và firmware đang dùng model khác nhau → vẫn áp dụng T cho luật, nhưng **tắt ML** và báo lỗi.
5. Chỉ gọi `spo2tc_engine_set_T` khi **T thực sự đổi**. Đổi cooldown, ngưỡng Critical, nhịp tim… thì gán thẳng vào `engine.cfg`, không xóa lịch sử.
6. Khi thiết bị được **giao cho bệnh nhân khác** (`assignment_id` đổi) → khởi tạo lại engine (xóa baseline của người cũ).

---

## 1. Bối cảnh: hiện tại 1 thiết bị, sau này nhiều thiết bị

Hiện đội chỉ có 1 vòng, theo dõi 1 bệnh nhân. Dù vậy firmware phải viết theo **ngữ cảnh API**, không gán cứng gì về bệnh nhân:

| Không làm | Làm |
|---|---|
| Ghi cứng mã bệnh nhân `PAT-000001` trong code | Chỉ biết `device_id`. Server tra `device_assignments` để biết dữ liệu của ai |
| Ghi cứng URL topic `dev/WB-C3-0001/...` | Ghép chuỗi từ `device_id` đọc trong NVS |
| Dùng chung một khóa API cho mọi thiết bị trong code | Mỗi thiết bị một `device_key` nạp lúc provisioning (giai đoạn 1 có thể dùng chung khóa, nhưng **vẫn đọc từ NVS** và **vẫn gửi `x-device-id`**) |
| Giữ baseline khi đổi người đeo | `assignment_id` đổi → `spo2tc_engine_init` lại |

Khi có thêm vòng: nạp `device_id`/`device_key` khác vào NVS, build firmware **giống hệt**. Không cần sửa code.

### 1.1 Provisioning (một lần cho mỗi vòng)

Lưu trong NVS namespace `ident` (nạp bằng `nvs_partition_gen` hoặc lệnh serial lúc sản xuất):

| Khóa NVS | Kiểu | Ví dụ | Ghi chú |
|---|---|---|---|
| `device_id` | string ≤ 32 | `WB-C3-0001` | trùng với id web tạo trong trang Thiết bị |
| `device_key` | string 64 hex | (bí mật) | **không in ra log**, không gửi qua BLE |
| `api_base` | string | `https://<web-domain>` | chung server với website |
| `wifi_ssid`, `wifi_pass` | string | | nạp qua BLE hoặc serial; có thể nhiều bộ |

Namespace `state` (firmware tự ghi): `seq` (u32, tăng mãi, không reset khi reboot), `boot_id` (u32), `cfg` (blob config đang áp dụng, xem §4.3), `acked_seq` (u32).

---

## 2. Luồng khi bác sĩ đổi ngưỡng

```text
Bác sĩ đặt T = 88 cho bệnh nhân PAT-000001 trên web
  └─ server: patient_configs.version 8 → 9; ghép model_releases(model_version) → thr_q {watch 71, alert 86}
      ├─ (a) App đang nối BLE với vòng: app nhận Realtime/FCM → GET config → ghi CONFIG 16 B xuống vòng
      ├─ (b) Vòng đang ở chế độ WiFi: lần POST kế tiếp, response có "config_version_latest": 9
      │       → vòng GET /api/app/v1/device/config → nhận JSON config.v1
      └─ (c) (tùy chọn) MQTT retained dev/{device_id}/config → vòng nhận ngay nếu đang nối broker
  └─ vòng: kiểm tra → lưu NVS → áp dụng ở đầu phút kế tiếp → báo config_version = 9
      ├─ BLE: notify STATUS (byte 12–13) → app gửi config-ack lên server
      └─ WiFi: POST /api/app/v1/ingest/config-ack
  └─ web hiển thị "Đã áp dụng trên thiết bị lúc 08:03"
```

Độ trễ chấp nhận được: BLE vài giây; WiFi ≤ chu kỳ gửi (mặc định 10 phút). Trong lúc chờ, vòng vẫn chạy bằng config cũ, không có khoảng trống.

**Cùng một version đến từ nhiều đường** là bình thường. Firmware chỉ áp dụng khi `version > version đang dùng`; trùng thì bỏ qua nhưng vẫn ack lại (idempotent).

---

## 3. Nội dung config

### 3.1 JSON `config.v1` (HTTPS và MQTT)

```json
{
  "schema": "config.v1",
  "device_id": "WB-C3-0001",
  "assignment_id": 3,
  "version": 9,
  "issued_at": "2026-10-16T08:00:00Z",
  "model_version": "hybrid_tc-20261016-s3",
  "T": 88,
  "ml_enabled": true,
  "thr_q": { "watch": 71, "alert": 86 },
  "tiers": {
    "watch": { "k": 1, "n": 1, "cooldown": 30 },
    "alert": { "k": 1, "n": 1, "cooldown": 60 }
  },
  "critical": { "crit_T": 85, "n_min": 3, "sqi_min": 0.5, "hr_low": 45, "hr_high": 120, "cooldown": 30 },
  "uplink": { "upload_interval_min": 10 }
}
```

### 3.2 Ánh xạ sang `spo2tc_config_t` và kiểm tra hợp lệ

| JSON | Trường C | Hợp lệ | Ghi chú |
|---|---|---|---|
| `device_id` | (không) | = `device_id` trong NVS | khác → từ chối, lỗi `WRONG_DEVICE` |
| `assignment_id` | (lưu riêng) | ≥ 1 | khác giá trị đang lưu → reset engine (§4.2) |
| `version` | (lưu riêng) | > version đang dùng | ≤ → bỏ qua, ack lại version hiện tại |
| `model_version` | — | = `SPO2TC_MODEL_VERSION` | khác → áp dụng phần luật, tắt ML, lỗi `MODEL_MISMATCH` |
| `T` | `T`, `watch_thr_q`, `alert_thr_q` | 85–95 | ngoài khoảng → từ chối cả config, lỗi `T_OUT_OF_RANGE` |
| `ml_enabled` | (cờ firmware) | bool | false → gọi engine với `model = NULL` (chỉ còn luật) |
| `thr_q.watch/alert` | — | = `SPO2TC_WATCH_THR_Q[T-85]` / `SPO2TC_ALERT_THR_Q[T-85]` | **chỉ để đối chiếu**. Lệch → xử lý như `MODEL_MISMATCH` |
| `tiers.watch.k/n` | `watch_k`, `watch_n` | 1 ≤ k ≤ n ≤ 16 | 16 = `SPO2TC_MAX_EVAL` |
| `tiers.watch.cooldown` | `watch_cooldown` | 5–240 | phút |
| `tiers.alert.k/n` | `alert_k`, `alert_n` | 1 ≤ k ≤ n ≤ 16 | |
| `tiers.alert.cooldown` | `alert_cooldown` | 5–240 | |
| `critical.crit_T` | `crit_T` | 70 ≤ crit_T < T | |
| `critical.n_min` | `crit_n_min` | 1–10 | phút liên tiếp |
| `critical.sqi_min` | `crit_sqi` | 0.0–1.0 | |
| `critical.hr_low` | `hr_low` | 0 (tắt) hoặc 30–80 | |
| `critical.hr_high` | `hr_high` | 0 (tắt) hoặc 100–220, > hr_low | |
| `critical.cooldown` | `crit_cooldown` | 5–240 | |
| `uplink.upload_interval_min` | (cờ firmware) | 1–60 | chu kỳ gửi lô khi chạy WiFi |

Trường thiếu → giữ giá trị đang dùng. Trường lạ → bỏ qua (để server thêm trường sau này không làm vỡ firmware cũ).

**Không cấu hình được** (cố định theo lúc train, đổi là lệch model): độ dài sự kiện luật ALERT = 3 phút (`SPO2TC_MIN_EVENT`), cửa sổ 60 phút, chạy model mỗi 5 phút, quy tắc UNKNOWN. Web không được cho bác sĩ sửa "thời gian dưới ngưỡng" của ALERT; chỉ hiển thị "3 phút (cố định)".

Bảng ngưỡng hiện tại để kiểm tra nhanh:

| T | 85 | 86 | 87 | 88 | 89 | 90 | 91 | 92 | 93 | 94 | 95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| watch_thr_q | 54 | 54 | 63 | 71 | 79 | 87 | 94 | 101 | 106 | 110 | 110 |
| alert_thr_q | 71 | 71 | 79 | 86 | 93 | 101 | 105 | 109 | 114 | 117 | 117 |

### 3.3 Gói BLE CONFIG 16 B (app ghi xuống characteristic `a51c0008`)

Little-endian. Đây là bản rút gọn của JSON ở trên.

| Byte | Kiểu | Trường | JSON tương ứng |
|---|---|---|---|
| 0–1 | u16 | config_version | `version` |
| 2 | u8 | T | `T` |
| 3 | u8 | crit_T | `critical.crit_T` |
| 4 | u8 | crit_n_min | `critical.n_min` |
| 5 | u8 | crit_sqi × 100 | `critical.sqi_min` |
| 6 | u8 | hr_low | `critical.hr_low` |
| 7 | u8 | hr_high | `critical.hr_high` |
| 8 | u8 | flags: bit0 ml_enabled | `ml_enabled` |
| 9 | i8 | watch_thr_q (đối chiếu) | `thr_q.watch` |
| 10 | i8 | alert_thr_q (đối chiếu) | `thr_q.alert` |
| 11 | u8 | k (dùng cho cả Watch và Alert) | `tiers.*.k` |
| 12 | u8 | n (dùng cho cả Watch và Alert) | `tiers.*.n` |
| 13 | u8 | alert_cooldown (phút) | `tiers.alert.cooldown` |
| 14 | u8 | upload_interval_min | `uplink.upload_interval_min` |
| 15 | u8 | watch_cooldown (phút) | `tiers.watch.cooldown` |

Gói BLE không mang `model_version`, `assignment_id`, `crit_cooldown` (giữ giá trị đang có; mặc định 30). App chỉ ghi CONFIG khi `model_version` của server trùng với `model` đọc từ DEVICE_INFO; nếu khác, app không ghi và báo server.

Ví dụ T = 88, version 9, Critical 85% / 3 phút / SQI 0,5, nhịp tim 45–120, ML bật, cooldown Alert 60, gửi 10 phút, cooldown Watch 30:

```text
09 00 58 55 03 32 2D 78 01 47 56 01 01 3C 0A 1E
```

Phản hồi ghi (write with response):
- Hợp lệ → trả thành công, áp dụng, notify STATUS với version mới.
- Không hợp lệ → trả lỗi ATT `0x80` (application error) và **không** đổi gì. App đọc lại STATUS để biết version đang dùng.
- Đọc CONFIG (read) → trả 16 B của config đang áp dụng.

---

## 4. Áp dụng config trong firmware

### 4.1 Quy tắc luồng (thread)

Config đến từ callback BLE, task HTTP hoặc task MQTT, nhưng **engine chỉ được sửa trong task chạy `spo2tc_engine_push_minute`**. Dùng một hàng đợi 1 phần tử (`xQueueOverwrite`) chứa config đã kiểm tra; task phút đọc hàng đợi ngay trước khi push phút mới.

```c
// Callback (BLE/HTTP/MQTT): parse + validate, KHÔNG đụng engine
static QueueHandle_t s_cfg_q;   // xQueueCreate(1, sizeof(device_cfg_t))

typedef struct {
  uint16_t version;
  uint32_t assignment_id;        // 0 = không đổi (gói BLE không mang)
  bool     ml_enabled;
  bool     model_ok;             // model_version và thr_q khớp firmware
  uint8_t  upload_interval_min;
  spo2tc_config_t eng;           // đã điền T, thr_q từ bảng local, tầng, critical
} device_cfg_t;
```

### 4.2 Trình tự áp dụng

```c
// Trong task phút, đầu mỗi vòng lặp
device_cfg_t nc;
if (xQueueReceive(s_cfg_q, &nc, 0) == pdTRUE && nc.version > g_cfg.version) {
  bool reassign = nc.assignment_id && nc.assignment_id != g_cfg.assignment_id;
  bool t_changed = nc.eng.T != engine.cfg.T;

  cfg_save_nvs(&nc);                         // 1) lưu trước, mất điện giữa chừng vẫn còn bản cũ hoặc bản mới đầy đủ

  if (reassign) {
    spo2tc_engine_init(&engine, &nc.eng);    // 2a) người đeo mới: xóa cả baseline 24 h
  } else {
    int keep_T = engine.cfg.T;
    engine.cfg = nc.eng;                     // 2b) gán tầng, critical, nhịp tim
    engine.cfg.T = keep_T;
    if (t_changed) spo2tc_engine_set_T(&engine, nc.eng.T);   // chỉ khi T đổi: điền thr_q và xóa lịch sử sự kiện
  }
  g_cfg = nc;                                // 3) ml_enabled, model_ok, upload_interval, version
  report_config_applied(g_cfg.version, g_cfg.model_ok ? "applied" : "applied_rules_only");   // 4) §5
}

spo2tc_model_fn fn = (g_cfg.ml_enabled && g_cfg.model_ok && g_model_ready) ? model_runner_invoke : NULL;
spo2tc_result_t r = spo2tc_engine_push_minute(&engine, m, fn, NULL);
```

Ghi chú:
- `spo2tc_engine_set_T` **không** xóa baseline, chỉ xóa đếm sự kiện và lịch sử persistence (để nhãn sự kiện tính lại theo T mới). Đây là hành vi đúng.
- `model = NULL` vẫn chạy luật CRITICAL và ALERT theo luật (SpO₂ < T ≥ 3 phút). ASSESSMENT gửi `out_q = -128`.
- `g_model_ready = false` khi `self-test` lúc boot không đạt 24/24.

### 4.3 Lưu NVS

- Một blob `cfg` trong namespace `state`: `{magic 'CFG1', device_cfg_t, crc32}`. `nvs_set_blob` + `nvs_commit` là nguyên tử ở mức khóa.
- Lúc boot: đọc blob, kiểm tra magic + CRC + T hợp lệ. Hỏng hoặc chưa có → dùng **config xuất xưởng**.

### 4.4 Config xuất xưởng (version 0)

`spo2tc_config_default()` (T = 88, Watch 1/1/30, Alert 1/1/60, Critical 85%/3 phút/SQI 0,5, nhịp tim 40–140), `ml_enabled = true`, `upload_interval_min = 10`, `version = 0`, `assignment_id = 0`.

Version 0 nghĩa là **chưa có config của bác sĩ**. App và web hiển thị cảnh báo "Thiết bị đang dùng ngưỡng mặc định". Vòng vẫn theo dõi bình thường.

---

## 5. Báo lại config đang dùng

| Kênh | Chỗ báo |
|---|---|
| BLE | STATUS byte 12–13 (notify ngay sau khi áp dụng), ASSESSMENT byte 22–23, DEVICE_INFO khóa `cfg`. App thấy version mới → gửi ack lên server thay vòng |
| WiFi | `POST /api/app/v1/ingest/config-ack` (§6.4) ngay sau khi áp dụng; mọi `assessments` và `status` đều mang `config_version` |

Server coi config "đã áp dụng" khi nhận ack **hoặc** thấy `config_version` đó trong assessment/status.

---

## 6. API cho thiết bị (chế độ WiFi)

Chung server Next.js với website. Mọi request:

```http
x-device-id: WB-C3-0001
x-device-key: <device_key>
content-type: application/json
```

JSON dùng `snake_case`. Thời gian `ts` là epoch giây UTC (số nguyên). Lỗi có dạng `{"error": {"code": "...", "message": "..."}}`.

| Method | Đường dẫn | Khi nào gọi |
|---|---|---|
| GET | `/api/app/v1/device/config` | lúc boot khi có WiFi; khi response khác có `config_version_latest` > version đang dùng |
| POST | `/api/app/v1/ingest/telemetry` | mỗi `upload_interval_min` phút (lô bản ghi phút) |
| POST | `/api/app/v1/ingest/assessments` | mỗi lô; **ngay lập tức** khi có `notify_alert` hoặc `notify_critical` |
| POST | `/api/app/v1/ingest/status` | mỗi lô và khi pin/đeo thay đổi |
| POST | `/api/app/v1/ingest/config-ack` | ngay sau khi áp dụng hoặc từ chối config |

Ở chế độ BLE, vòng **không** gọi các API này: app nhận dữ liệu qua BLE rồi gọi **cùng** các endpoint `/ingest/*` bằng token của người dùng (`Authorization: Bearer`), với `"transport": "ble_relay"`. Server xử lý hai đường bằng một hàm, bỏ trùng theo `(device_id, seq)`.

### 6.1 `GET /api/app/v1/device/config`

`200` → JSON `config.v1` (§3.1). `304` nếu gửi header `if-none-match: "9"` và version không đổi. `404 NO_ASSIGNMENT` nếu thiết bị chưa được giao cho bệnh nhân nào → giữ config hiện tại, thử lại sau 15 phút.

### 6.2 `POST /api/app/v1/ingest/telemetry`

```json
{
  "schema": "telemetry.v1",
  "device_id": "WB-C3-0001",
  "boot_id": 42,
  "samples": [
    { "seq": 10231, "ts": 1792137600, "spo2_mean": 95.4, "spo2_min": 94.0, "spo2_valid": 0.97, "pulse_mean": 78, "sqi": 0.91, "worn": true },
    { "seq": 10232, "ts": 1792137660, "spo2_mean": null, "spo2_min": null, "spo2_valid": 0.0, "pulse_mean": null, "sqi": 0.0, "worn": false }
  ]
}
```

Giá trị thiếu gửi `null` (trong C là `NAN`; **không gửi 0**). Tối đa 120 bản ghi mỗi request.

Response `200`:

```json
{ "acked_seq": 10232, "config_version_latest": 9, "server_time": 1792137725 }
```

- Xóa khỏi flash các bản ghi có `seq ≤ acked_seq`.
- `config_version_latest` > version đang dùng → gọi §6.1.
- `server_time` lệch đồng hồ > 5 giây → chỉnh đồng hồ (thay cho TIME_SYNC khi không có app).

### 6.3 `POST /api/app/v1/ingest/assessments`

```json
{
  "schema": "assessment.v1",
  "device_id": "WB-C3-0001",
  "items": [
    {
      "seq": 10232, "ts": 1792137660,
      "level": "ALERT",
      "notify": { "watch": false, "alert": true, "critical": false },
      "trigger": "ml_risk",
      "critical_reason": null,
      "alert_by_rule": false,
      "ran_model": true, "out_q": 92, "prob": 0.41,
      "T": 88, "baseline": 94.6, "base_hours": 23.5,
      "spo2_now": 89.8, "pulse_now": 96,
      "config_version": 9, "model_version": "hybrid_tc-20261016-s3",
      "local_notified": true
    }
  ]
}
```

| Trường | Lấy từ `spo2tc_result_t` |
|---|---|
| `level` | `level`: `UNKNOWN` / `NORMAL` / `WATCH` / `ALERT` / `CRITICAL` |
| `notify.*` | `notify_watch`, `notify_alert`, `notify_critical` |
| `trigger` | `critical_reason==1` → `rule_critical`; `==2` → `hr_out_of_range`; `alert_by_rule` → `rule_alert`; `notify_alert`/`notify_watch` do ML → `ml_risk`; `UNKNOWN` → `signal_lost`; còn lại `none` |
| `critical_reason` | 1 → `"spo2"`, 2 → `"hr"`, 0 → `null` |
| `out_q`, `prob` | chỉ khi `ran_model`; không chạy → `null` |
| `baseline`, `base_hours` | `baseline`, `base_hours` |
| `seq` | seq của bản ghi phút vừa push (khóa idempotent: `device_id` + `seq`) |

Gửi 1 item **mỗi 5 phút** (khi `t % 5 == 0`) và **mỗi khi** `level` đổi hoặc có `notify_*`. Response `200`: `{ "accepted": 1 }`. Server tự gửi thông báo đẩy (FCM) tới app khi có `notify.alert` hoặc `notify.critical`.

### 6.4 `POST /api/app/v1/ingest/config-ack`

```json
{ "device_id": "WB-C3-0001", "config_version": 9, "status": "applied", "reason": null, "model_version": "hybrid_tc-20261016-s3", "applied_at": 1792137720 }
```

`status`: `applied` · `applied_rules_only` (kèm `reason: "MODEL_MISMATCH"`) · `rejected` (kèm `reason`: `T_OUT_OF_RANGE`, `INVALID_FIELD:<tên>`, `WRONG_DEVICE`).

### 6.5 `POST /api/app/v1/ingest/status`

```json
{ "device_id": "WB-C3-0001", "boot_id": 42, "ts": 1792137720, "battery": 76, "worn": true, "charging": false,
  "backlog": 0, "acked_seq": 10232, "last_seq": 10232, "config_version": 9,
  "fw": "1.0.0", "model_version": "hybrid_tc-20261016-s3", "model_ready": true, "rssi": -61 }
```

Response giống §6.2 (có `config_version_latest`, `server_time`).

### 6.6 Xử lý lỗi mạng

| Mã | Firmware làm gì |
|---|---|
| `200` | xử lý như trên |
| `400` | lỗi định dạng do firmware: log, **bỏ** lô đó (không thử lại mãi), tăng biến đếm lỗi |
| `401` / `403` | sai khóa: dừng gửi 30 phút, giữ dữ liệu trong flash |
| `404 NO_ASSIGNMENT` | thiết bị chưa giao bệnh nhân: giữ dữ liệu, thử lại sau 15 phút |
| `413` | chia đôi lô rồi gửi lại |
| `429`, `5xx`, timeout | thử lại với thời gian chờ 30 s, 1, 2, 4… tối đa 15 phút; dữ liệu vẫn nằm trong flash |

Gửi lại cùng `seq` là an toàn: server bỏ qua bản trùng.

### 6.7 MQTT (tùy chọn, chỉ chiều xuống)

Giai đoạn 1 **không bắt buộc**: §6.2 đã báo `config_version_latest`, đủ cho độ trễ ≤ 10 phút và tiết kiệm pin hơn giữ kết nối MQTT. Nếu đội firmware muốn nhận config ngay:

| Topic | QoS | Retained | Nội dung |
|---|---|---|---|
| `dev/{device_id}/config` | 1 | có | JSON `config.v1` (§3.1) |
| `dev/{device_id}/cmd` | 1 | không | `{"id":"c-81","cmd":"backfill","from_seq":10000}` · `{"cmd":"time_sync","ts":...}` · `{"cmd":"reboot"}` |

Server publish qua MQTT Gateway sẵn có của web (lệnh `wearable-config`). Vòng **không publish** dữ liệu qua MQTT; mọi dữ liệu đi lên bằng HTTPS ở §6.2–6.5.

---

## 7. Chuyển chế độ BLE ↔ WiFi

| Sự kiện | Hành động |
|---|---|
| App ghi CONTROL `SET_GATEWAY(1)` | tắt WiFi, gửi dữ liệu qua BLE, nhận config qua BLE CONFIG |
| Mất BLE > 60 s hoặc `SET_GATEWAY(0)` | bật WiFi theo đợt: mỗi `upload_interval_min` thức dậy, nối WiFi, `POST telemetry → assessments → status`, đọc `config_version_latest`, tắt WiFi |
| Có `notify_alert`/`notify_critical` lúc đang ở chế độ WiFi | nối WiFi ngay, gửi assessments trước, telemetry sau |
| Vừa nối lại BLE | app gửi `BACKFILL`; bản ghi đã được server ACK qua WiFi thì app đã có trên server, app tự bỏ qua bản trùng |

`acked_seq` là chung cho cả hai đường: bản ghi được xóa khi **app** ACK (BLE) hoặc **server** ACK (WiFi), lấy giá trị lớn hơn.

---

## 8. Kiểm thử

| # | Kiểm tra | Đạt khi |
|---|---|---|
| C1 | Ghi BLE CONFIG mẫu ở §3.3 | STATUS báo version 9; log in `T=88 watch=71 alert=86` |
| C2 | Ghi lại đúng gói đó | không reset lịch sử, vẫn trả thành công (idempotent) |
| C3 | Ghi gói T = 84, rồi T = 96 | lỗi ATT 0x80, version giữ nguyên |
| C4 | Ghi gói version 10 có `watch_thr_q = 70` (lệch bảng) | áp dụng T, ASSESSMENT `out_q = -128`, ack `applied_rules_only` |
| C5 | Đổi chỉ `alert_cooldown`, giữ T | engine không bị reset (`low_run`, lịch sử ML còn nguyên) |
| C6 | Đổi T 88 → 92 khi đang có sự kiện SpO₂ = 90 | sau khi đổi, sự kiện được đếm lại theo T = 92 (luật ALERT phát ở phút thứ 3) |
| C7 | Rút điện ngay sau khi ghi CONFIG, cắm lại | boot lên với version mới hoặc cũ, không bao giờ hỏng |
| C8 | Config JSON có `assignment_id` khác | baseline về 0 giờ, `base_hours` trong assessment = 0 |
| C9 | Chế độ WiFi, server trả `config_version_latest` lớn hơn | vòng GET config và ack trong cùng đợt |
| C10 | Tắt mạng 2 giờ rồi bật | dữ liệu đủ trên server, không trùng, flash được giải phóng |
| C11 | Dùng `device_key` sai | 401, dừng 30 phút, không mất dữ liệu |

Test trên PC (không cần board): viết hàm `cfg_validate_json()` và `cfg_decode_ble()` bằng C thuần, test cùng `test_host` với các ca C1–C5.

---

## 9. Mẫu prompt để nhờ AI hỗ trợ

Dán kèm 4 file: `include/spo2_tc.h`, `include/spo2_tc_params.h`, `docs/SPEC.md`, tài liệu này.

```text
Bối cảnh: ESP32-C3 SuperMini, ESP-IDF 5.x, NimBLE, cảm biến MAX30102. Thư viện C spo2_tc (header đính kèm)
đã chạy được: self-test 24/24, mỗi phút gọi spo2tc_engine_push_minute.

Việc cần làm: viết module device_config (device_config.h/.c) theo CONFIG_VA_API.md:
1. cfg_decode_ble(const uint8_t buf[16], device_cfg_t *out) và cfg_parse_json(const char *json, device_cfg_t *out)
   dùng cJSON; cả hai trả mã lỗi theo §3.2; điền thr_q từ bảng SPO2TC_*_THR_Q, đối chiếu giá trị gửi kèm.
2. cfg_save_nvs / cfg_load_nvs (blob + magic + crc32, §4.3), config xuất xưởng §4.4.
3. Hàng đợi 1 phần tử và đoạn áp dụng trong task phút đúng như §4.2 (không sửa engine trong callback BLE).
4. Test trên PC cho các ca C1–C5 ở §8 (không phụ thuộc ESP-IDF).
Ràng buộc: không gán cứng device_id hay mã bệnh nhân; không in device_key ra log; dùng NAN cho giá trị thiếu;
không bật -ffast-math. Giải thích ngắn từng hàm, không đổi API của spo2_tc.
```

Prompt cho phần gửi dữ liệu WiFi:

```text
Viết module uplink (esp_http_client + esp-tls, chứng chỉ gốc từ esp_crt_bundle) theo CONFIG_VA_API.md §6–7:
POST telemetry/assessments/status/config-ack với header x-device-id và x-device-key đọc từ NVS,
xử lý response acked_seq và config_version_latest, chính sách thử lại §6.6, gửi ngay khi có ALERT/CRITICAL.
Dữ liệu lấy từ ring buffer flash (seq tăng dần, xóa khi seq <= acked_seq). Không dùng MQTT để gửi dữ liệu lên.
```

---

## 10. Việc cần đội firmware xác nhận lại

1. Có dùng MQTT chiều xuống (§6.7) ở giai đoạn 1 không, hay chỉ dùng `config_version_latest` trong response.
2. Giai đoạn 1 dùng `device_key` chung hay riêng từng vòng (API hỗ trợ cả hai; firmware luôn gửi cả `x-device-id`).
3. Thư viện BLE: NimBLE (đề xuất) hay Bluedroid.
4. Có cần thêm opcode CONTROL để nạp WiFi qua app không (đề xuất `0x05 SET_WIFI` ở bản sau).
