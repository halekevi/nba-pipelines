# PrizePicks Payout Engine Progress

Date: 2026-02-22

## Terminology Update

-   Reversion renamed to FLEX (site naming convention)
-   Modes now:
    -   STANDARD
    -   FLEX

------------------------------------------------------------------------

# Confirmed Payout Tables (From Screenshots)

## 3-Leg STANDARD (Power Play)

  Correct Picks   Multiplier
  --------------- ------------
  3/3             6.0x
  2/3             0.75x
  1/3             0
  0/3             0

------------------------------------------------------------------------

## 3-Leg FLEX

  Correct Picks   Multiplier
  --------------- ------------
  3/3             2.8x
  2/3             0
  1/3             0
  0/3             0

------------------------------------------------------------------------

# Demon Tier Example (3-Leg Power Play Observed)

  Correct Picks   Multiplier
  --------------- ------------
  3/3             15x
  2/3             1.75x

⚠ Indicates tier-dependent scaling.

------------------------------------------------------------------------

# Current Engine Structure

payout_mode = STANDARD \| FLEX

Future Structure: mode → legs → tier_combo → correct → multiplier

Example logic skeleton:

def calculate_payout(mode, legs, tier, correct, stake): multiplier =
payout_tables\[mode\]\[legs\]\[tier\].get(correct, 0) return stake \*
multiplier

------------------------------------------------------------------------

Next Steps: - Map full 2-leg Power Play tables - Map full 3-leg tier
combinations - Formalize JSON payout structure - Build EV calculator
layer
