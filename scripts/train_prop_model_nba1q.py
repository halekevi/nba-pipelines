#!/usr/bin/env python3
"""
Train NBA 1Q prop model (models/prop_model_nba1q.*) from graded_nba1q_*.xlsx.

Thin wrapper around train_prop_model_nba.py --segment nba1q.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    sys.argv = [sys.argv[0], "--segment", "nba1q", *sys.argv[1:]]
    import train_prop_model_nba as _t

    _t.main()
