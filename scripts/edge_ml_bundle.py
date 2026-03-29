"""Pickle-stable wrapper for XGBoost + Platt calibration (used by train_edge_model and step7b_edge_score)."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier


class EdgeCalibratedModel:
    """Booster + Platt scaling (logistic on raw positive-class probability)."""

    def __init__(self, booster: XGBClassifier, platt_lr: LogisticRegression):
        self.booster = booster
        self.platt_lr = platt_lr

    def predict_proba(self, X) -> np.ndarray:
        p = self.booster.predict_proba(X)[:, 1].reshape(-1, 1)
        return self.platt_lr.predict_proba(p)
