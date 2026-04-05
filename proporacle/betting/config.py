"""Staking / risk parameters — load from YAML in ops; defaults match income-engine placeholders."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class StakingConfig:
    bankroll_units: float = 200.0
    kelly_fraction: float = 0.25
    max_stake_frac_per_bet: float = 0.02
    max_exposure_frac_per_game: float = 0.08
    max_exposure_frac_per_player: float = 0.05
    max_exposure_frac_per_sport: float = 0.30
    daily_loss_stop_frac: float = 0.05
    drawdown_warn_frac: float = 0.15
    drawdown_hard_stop_frac: float = 0.25
    drawdown_hard_stop_days: int = 3
    #: If rolling 60d ROI>0, mean CLV>0, max_dd<Y → multiply stakes by this (Phase 3).
    bankroll_scale_up_mult: float = 1.0
    bankroll_scale_up_min_roi: float = 0.0
    bankroll_scale_up_window_days: int = 60
    bankroll_scale_up_max_dd: float = 0.20

    @classmethod
    def from_dict(cls, d: dict) -> StakingConfig:
        kw = {f.name: d[f.name] for f in fields(cls) if f.name in d}
        return cls(**kw)

    @classmethod
    def from_yaml(cls, path: str | Path) -> StakingConfig:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore
        except ImportError as e:
            raise ImportError("Install PyYAML to use from_yaml: pip install pyyaml") from e
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("YAML root must be a mapping")
        return cls.from_dict(data)


def default_income_engine_config() -> StakingConfig:
    """Documented defaults for X=200 units, Y≈25% hard DD, 5% daily loss stop."""
    return StakingConfig()
