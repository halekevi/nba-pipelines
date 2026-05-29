from pathlib import Path

lines = Path("scripts/build_retrain_dataset.py").read_text(encoding="utf-8").splitlines()

wnba_start = next(i for i, l in enumerate(lines) if 'sk_u == "WNBA"' in l and i > 1000)
print(f"WNBA block starts at line {wnba_start + 1}")

for i, l in enumerate(lines[wnba_start : wnba_start + 120], wnba_start + 1):
    print(f"{i}: {l}")
