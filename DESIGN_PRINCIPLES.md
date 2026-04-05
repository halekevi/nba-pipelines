# PropORACLE — design principles (income engine, not consumer app)

## Product intent

PropORACLE is a **model-driven, edge-focused betting engine**. It exists to convert **calibrated probabilities**, **market prices**, and **explicit risk rules** into **measurable profit**—not to maximize engagement or entertainment.

## Anti–PrizePicks rules

1. **No optimization for** time-on-app, streaks, leaderboards, XP, missions, social flex, or “hot hand” narratives.
2. **Primary KPIs are only:** ROI (PnL / risk), CLV, calibration (Brier / log loss / ECE), hit rate **by EV or probability bucket** (sanity check), max and rolling drawdown, and optionally Sharpe-style return per unit risk.
3. **“Correct legs” and streaks** are **diagnostic only**, not success criteria. A system can hit legs and still lose to vig, correlation, and bad staking.
4. **No hand-wavy “confidence sliders”** as a primary driver. Position sizing uses **calibrated `p_fair`**, **posted odds**, and **documented** `edge_quality` / EV—not feelings.
5. **Every new feature** must justify itself by improving at least one of: ↑ CLV, ↑ risk-adjusted ROI, ↓ calibration error, ↓ drawdown / tail risk, ↑ measurement fidelity (logging, CLV capture, settlement).
6. **Display tiers (A/B/C, etc.)** must be **derived from** EV and uncertainty (or similar), never the **input** that defines model or stake decisions.

## Code norms (betting path)

- Code that places or recommends bets must log **`run_id`, `slate_id`, `model_version`, `pricing_version`** (structured logging).
- Writes must populate **`bet_recommendation`** and **`bet_result`** with **stake, PnL, open/close odds, CLV** when available.
- **Caps and stops** (per game/player/sport, daily loss, drawdown) are **mandatory** checks before final stake—not optional UI hints.

## Out of scope

Social feeds, pick copying, influencer rails, gamified rewards, leaderboard-by-hit-rate, and any UX whose success metric is engagement rather than **CLV/ROI/calibration/drawdown**.
