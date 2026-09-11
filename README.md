# Respiratory Risk-Level Classifier (Model 1)

> Part of an AIoT respiratory monitoring system (Team IT x MECH — VEDC 2026).
> This repository covers **Model 1**: a rule-based (NEWS2) risk-level classifier
> — Normal / Watch / Alert / Critical — derived from SpO2 and pulse-rate signals
> collected via a wearable MAX30102 PPG sensor.

![Python](https://img.shields.io/badge/Python-3.10+-1a1a2e?style=flat-square&logo=python&logoColor=3776AB&labelColor=1a1a2e&color=3776AB)
![Jupyter](https://img.shields.io/badge/Jupyter-Notebook-1a1a2e?style=flat-square&logo=jupyter&logoColor=F37626&labelColor=1a1a2e&color=F37626)
![scikit--learn](https://img.shields.io/badge/scikit--learn-ML-1a1a2e?style=flat-square&logo=scikit-learn&logoColor=F7931E&labelColor=1a1a2e&color=F7931E)
![License](https://img.shields.io/badge/license-Private%20%2F%20All%20Rights%20Reserved-1a1a2e?style=flat-square&labelColor=1a1a2e&color=6c757d)
---

## Overview

Model 1 is a **clinical decision-support** component for patients already
diagnosed with chronic respiratory conditions (COPD, asthma, etc.), designed
to monitor disease progression at home — not to perform initial diagnosis or
screening, and not to replace a physician.

It runs independently of, but stays timestamp-synchronized with, **Model 2**
(a digital stethoscope classifying lung sounds — out of scope for this repo)
so that a physician's dashboard can overlay both data streams.

## Scope

- **In scope:** SpO2 + pulse-rate data pipeline, feature engineering, NEWS2
  rule-based labeling, Phase 1 risk-level classifier.
- **Out of scope:** acoustic/lung-sound analysis (Model 2), initial diagnosis
  or screening of undiagnosed patients.

## Data Source

- **Dataset:** [MIMIC-III Waveform Database Matched Subset](https://physionet.org/content/mimic3wdb-matched/1.0/) (PhysioNet) — open access, no credentialed access required.
- Only **numerics records** (`*n.dat` / `*n.hea`) are used; waveform records are out of scope for v0.
- Cohort: ~20–50 patients, randomly selected with quality control, split **train/val/test at the patient level** to avoid data leakage.
- See [LICENSE.md](./LICENSE.md) for full dataset attribution and citation requirements.

## Input Features

Matched to what the physical MAX30102 sensor can measure:

| Kept | Reason |
|---|---|
| `SpO2` | Direct pulse-oximetry measurement |
| `PULSE`| `PULSE` is PPG/oximetry-derived, matching MAX30102 |

Dropped: `RESP`, `ABPSys/Dias/Mean` — outside the sensor's measurement capability.

Engineered features (from SpO2 + PULSE only):
- Rolling statistics (mean/std/min/max) over 5/15/30-minute windows
- Trend/slope over time
- Desaturation index (count of SpO2 drops below threshold per window)

> True HRV is not feasible at v0 (numerics are per-minute values, not
> beat-to-beat); reserved for a future extension using raw PLETH waveform data.

## Labeling — NEWS2 (rule-based, not learned)

Risk levels are generated using the standard **NEWS2** scoring table (Royal
College of Physicians) applied to SpO2 + Pulse, not learned from data.

- SpO2 Scale 1 (default, general population) or Scale 2 (COPD /
  hypercapnic-risk patients, target range 88–92%) is a **runtime flag set by
  the physician**, not something the model infers from training data.
- The ML component focuses on **learning temporal trend/pattern** (early
  prediction of an upcoming risk-level transition) — not on re-learning the
  NEWS2 formula itself.

## Output

- **Patient app:** traffic-light risk indicator, short description, simple
  daily/weekly trend chart, push alerts on Alert/Critical with guidance.
- **Physician dashboard:** SpO2/Pulse time-series overlaid by risk-level,
  event log of risk-level transitions, trend indicator.

## Disclaimer

This is a research prototype intended as **clinical decision support only**.
It is not a certified medical device and must not be used as the sole basis
for a diagnostic or treatment decision.

## License

- Project code: see [LICENSE.md](./LICENSE.md)
- Third-party data (MIMIC-III Waveform Database Matched Subset): see the
  **Third-Party Data & Licenses** section in [LICENSE.md](./LICENSE.md) —
  required attribution/citation applies.