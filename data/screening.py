"""screening.py — chạy: python screening.py"""
import os
import gc
import socket
import random
import time
import wfdb
import pandas as pd
import numpy as np
import requests
from tqdm import tqdm

# ---- 1.0 Config ----
DB_NAME = "mimic3wdb-matched"
DB_VERSION = "1.0"
RECORDS_URL = f"https://physionet.org/files/{DB_NAME}/{DB_VERSION}/RECORDS-numerics"
random.seed(42)

# ---- 1.1 Lấy danh sách record ----
def fetch_records_list(url: str) -> list[str]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text.strip().splitlines()

def group_by_patient(record_paths: list[str]) -> dict[str, list[str]]:
    patients = {}
    for path in record_paths:
        patient_id = path.split("/")[1]
        patients.setdefault(patient_id, []).append(path)
    return patients

# ---- 1.2 screen_candidate ----
def screen_candidate(patient_id, record_path, window_minutes=30, skip_minutes=15):
    pn_dir = f"{DB_NAME}/{DB_VERSION}/" + "/".join(record_path.split("/")[:-1])
    record_name = record_path.split("/")[-1]
    try:
        header = wfdb.rdheader(record_name, pn_dir=pn_dir)
    except Exception:
        return None
    sig_names = [s.upper() for s in header.sig_name]
    if "SPO2" not in sig_names or "PULSE" not in sig_names:
        return None
    total_minutes = header.sig_len / header.fs / 60
    windows = [skip_minutes]
    if total_minutes > (skip_minutes + window_minutes) * 2.5:
        windows.append(max(skip_minutes, total_minutes / 2 - window_minutes / 2))
        windows.append(max(skip_minutes, total_minutes - window_minutes - 5))
    spo2_mins, pulse_means, dropout_ratios = [], [], []
    for start_min in windows:
        sampfrom = int(start_min * 60 * header.fs)
        sampto = min(int((start_min + window_minutes) * 60 * header.fs), header.sig_len)
        if sampto <= sampfrom:
            continue
        try:
            rec = wfdb.rdrecord(record_name, pn_dir=pn_dir, sampfrom=sampfrom, sampto=sampto)
        except Exception:
            continue
        df = pd.DataFrame(rec.p_signal, columns=[s.upper() for s in rec.sig_name])
        spo2_clean = df["SPO2"].replace(0, np.nan)
        pulse_clean = df["PULSE"].replace(0, np.nan)
        if spo2_clean.isna().all():
            continue
        spo2_mins.append(spo2_clean.min())
        pulse_means.append(pulse_clean.mean())
        dropout_ratios.append(spo2_clean.isna().mean())
    if not spo2_mins:
        return None
    return {
        "patient_id": patient_id,
        "record_path": record_path,
        "spo2_min_proxy": min(spo2_mins),
        "spo2_mean_proxy": float(np.mean(spo2_mins)),
        "pulse_mean_proxy": float(np.mean(pulse_means)),
        "dropout_ratio_spo2": float(np.mean(dropout_ratios)),
        "has_resp_signal": "RESP" in sig_names,
        "n_windows_scanned": len(spo2_mins),
    }

# ---- 1.2b Vòng lặp sàng lọc — có resume + dừng êm ----
def main():
    socket.setdefaulttimeout(30)

    CHECKPOINT_PATH = "screening_checkpoint.parquet"
    CHECKPOINT_EVERY = 100
    candidate_pool_size = 3000

    record_paths = fetch_records_list(RECORDS_URL)
    patients_dict = group_by_patient(record_paths)
    candidate_patients = random.sample(list(patients_dict.keys()), candidate_pool_size)

    if os.path.exists(CHECKPOINT_PATH):
        screening_df_old = pd.read_parquet(CHECKPOINT_PATH)
        screening_results = screening_df_old.to_dict("records")
        done_pids = set(screening_df_old["patient_id"])
        print(f"Resume: {len(done_pids)} bệnh nhân đã có kết quả, chạy tiếp phần còn lại.")
    else:
        screening_results = []
        done_pids = set()

    remaining_patients = [pid for pid in candidate_patients if pid not in done_pids]
    skipped_timeout = 0

    pbar = tqdm(remaining_patients, desc="Screening")
    try:
        for pid in pbar:
            first_record = patients_dict[pid][0]
            try:
                result = screen_candidate(pid, first_record)
            except (socket.timeout, TimeoutError):
                skipped_timeout += 1
                result = None
            if result is not None:
                screening_results.append(result)
            pbar.set_postfix(valid=len(screening_results), timeout=skipped_timeout)

            if len(screening_results) % CHECKPOINT_EVERY == 0 and len(screening_results) > 0:
                pd.DataFrame(screening_results).to_parquet(CHECKPOINT_PATH, index=False)
                gc.collect()
    except KeyboardInterrupt:
        print(f"\n[Đã dừng thủ công] Đang có {len(screening_results)} kết quả — lưu rồi dừng.")
    finally:
        pd.DataFrame(screening_results).to_parquet(CHECKPOINT_PATH, index=False)
        print(f"Ứng viên hợp lệ hiện có: {len(screening_results)} / {candidate_pool_size} (timeout: {skipped_timeout})")

if __name__ == "__main__":
    main()