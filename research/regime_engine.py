"""
Regime Engine

Responsibility:
    Deterministic regime classification engine (HMM-style forward filter
    with fixed Gaussian emissions — a robust alternative to a fitted HMM).
    Consumes OHLCV data and outputs a probability distribution over regimes:
        Crash / Bear / Neutral / Bull / Euphoria
    for every instrument on the watchlist.
    Fully tested; never invents data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class RegimeEngine:
    """Classifies market regimes using a sticky 5-state Gaussian HMM filter."""

    REGIMES = ("Crash", "Bear", "Neutral", "Bull", "Euphoria")

    # Emission means in (window log-return, window realized vol) space.
    # Window is 24 bars of H1 ≈ 1 trading day of FX / 1 day of crypto.
    MEANS = {
        "Crash": np.array([-0.070, 0.090]),
        "Bear": np.array([-0.013, 0.032]),
        "Neutral": np.array([0.000, 0.010]),
        "Bull": np.array([0.011, 0.020]),
        "Euphoria": np.array([0.050, 0.065]),
    }
    # Diagonal covariance — wide enough to overlap, tight enough to separate
    VAR = np.array([0.028**2, 0.022**2])

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        data_cfg = self.config.get("data", {}) if isinstance(self.config, dict) else {}
        self.window = int(data_cfg.get("regime_window", 24))
        self.step = int(data_cfg.get("regime_step", 12))
        self.recent_windows = int(data_cfg.get("regime_recent_windows", 8))
        self._means = np.stack([self.MEANS[r] for r in self.REGIMES])
        self._log_trans = np.log(self._transition_matrix())
        self._log_start = np.log(np.array([0.08, 0.18, 0.40, 0.24, 0.10]))
        logger.info("RegimeEngine initialized window=%s step=%s", self.window, self.step)

    def classify(self, ohlcv: pd.DataFrame) -> Dict[str, float]:
        """
        Return probability distribution over the five regimes.

        Returns:
            {"Crash": 0.05, "Bear": 0.15, "Neutral": 0.40, "Bull": 0.30, "Euphoria": 0.10}
        """
        detail = self.classify_detail(ohlcv)
        return {regime: detail["probabilities"][regime] for regime in self.REGIMES}

    def classify_detail(self, ohlcv: pd.DataFrame) -> Dict[str, Any]:
        """Full classification: probabilities, label, confidence, last features."""
        features = self._feature_sequence(ohlcv)
        if features.size == 0:
            uniform = 1.0 / len(self.REGIMES)
            probs = {r: uniform for r in self.REGIMES}
            return {
                "probabilities": probs,
                "label": "Neutral",
                "confidence": uniform,
                "features": None,
                "n_windows": 0,
            }
        recent = features[-self.recent_windows :]
        robust = np.median(recent, axis=0)
        # Blend last-window emission posterior with a short forward filter so
        # a single noisy bar cannot flip the regime, and 90-day history cannot
        # trap the filter in an early state.
        emit_log = self._emission_logp(robust)
        emit_post = np.exp(emit_log - self._logsumexp(emit_log))
        log_alpha = self._forward(recent)
        filt_log = log_alpha[-1]
        filt_post = np.exp(filt_log - self._logsumexp(filt_log))
        posterior = 0.65 * emit_post + 0.35 * filt_post
        posterior = posterior / posterior.sum()
        probs = {regime: float(posterior[i]) for i, regime in enumerate(self.REGIMES)}
        label_idx = int(np.argmax(posterior))
        return {
            "probabilities": probs,
            "label": self.REGIMES[label_idx],
            "confidence": float(posterior[label_idx]),
            "features": {"trend": float(robust[0]), "vol": float(robust[1])},
            "n_windows": int(features.shape[0]),
        }

    def classify_watchlist(self, data: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, float]]:
        """Classify every symbol in the provided data dict."""
        out: Dict[str, Dict[str, float]] = {}
        for symbol, frame in data.items():
            out[symbol] = self.classify(frame)
        return out

    def classify_watchlist_detail(
        self, data: Mapping[str, pd.DataFrame]
    ) -> Dict[str, Dict[str, Any]]:
        """Classify every symbol, returning labels and probabilities."""
        return {symbol: self.classify_detail(frame) for symbol, frame in data.items()}

    # ------------------------------------------------------------------
    # HMM internals
    # ------------------------------------------------------------------
    def _transition_matrix(self) -> np.ndarray:
        """Sticky adjacent-regime transitions. Rows sum to 1."""
        n = len(self.REGIMES)
        a = np.full((n, n), 0.02)
        for i in range(n):
            a[i, i] = 0.78
            if i > 0:
                a[i, i - 1] = 0.10
            if i < n - 1:
                a[i, i + 1] = 0.10
        a /= a.sum(axis=1, keepdims=True)
        return a

    def _emission_logp(self, x: np.ndarray) -> np.ndarray:
        """Log p(x | state) for each of the 5 states. x shape (2,)."""
        diff = x - self._means
        quad = np.sum((diff**2) / self.VAR, axis=1)
        log_det = np.sum(np.log(2.0 * np.pi * self.VAR))
        return -0.5 * (quad + log_det)

    def _forward(self, features: np.ndarray) -> np.ndarray:
        """Forward algorithm. Returns log-alpha of shape (T, 5)."""
        t_steps = features.shape[0]
        n = len(self.REGIMES)
        log_alpha = np.zeros((t_steps, n))
        log_alpha[0] = self._log_start + self._emission_logp(features[0])
        for t in range(1, t_steps):
            emit = self._emission_logp(features[t])
            for j in range(n):
                log_alpha[t, j] = emit[j] + self._logsumexp(log_alpha[t - 1] + self._log_trans[:, j])
        return log_alpha

    def _feature_sequence(self, ohlcv: pd.DataFrame) -> np.ndarray:
        if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
            return np.zeros((0, 2))
        close = pd.to_numeric(ohlcv["close"], errors="coerce").dropna().to_numpy(dtype=float)
        if close.size < self.window + 2:
            # Fall back to whatever we have
            if close.size < 8:
                return np.zeros((0, 2))
            feat = self._window_features(close)
            return feat.reshape(1, 2) if feat is not None else np.zeros((0, 2))

        feats: list[np.ndarray] = []
        start = self.window
        for end in range(start, close.size, self.step):
            window = close[end - self.window : end + 1]
            feat = self._window_features(window)
            if feat is not None:
                feats.append(feat)
        if not feats:
            feat = self._window_features(close)
            if feat is None:
                return np.zeros((0, 2))
            return feat.reshape(1, 2)
        return np.vstack(feats)

    @staticmethod
    def _window_features(close: np.ndarray) -> Optional[np.ndarray]:
        if close.size < 6:
            return None
        # Guard against non-positive prices
        close = np.clip(close, 1e-12, None)
        log_r = np.diff(np.log(close))
        if log_r.size < 4:
            return None
        trend = float(np.sum(log_r))
        vol = float(np.std(log_r, ddof=1) * np.sqrt(log_r.size))
        return np.array([trend, vol], dtype=float)

    @staticmethod
    def _logsumexp(values: Sequence[float] | np.ndarray) -> float:
        arr = np.asarray(values, dtype=float)
        m = float(np.max(arr))
        return m + float(np.log(np.sum(np.exp(arr - m))))
