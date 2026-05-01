import pandas as pd
import numpy as np

df = pd.read_csv('ticket_performance.csv')
df['legs'] = pd.to_numeric(df['legs'], errors='coerce')
df['tickets'] = pd.to_numeric(df['tickets'], errors='coerce')
df['staked'] = pd.to_numeric(df['staked'], errors='coerce')
df['payout'] = pd.to_numeric(df['payout'], errors='coerce')
df['profit'] = pd.to_numeric(df['profit'], errors='coerce')
df['roi'] = pd.to_numeric(df['roi'], errors='coerce')
df['win_rate'] = pd.to_numeric(df['win_rate'], errors='coerce')
df['cash_rate'] = pd.to_numeric(df['cash_rate'], errors='coerce')

df = df.dropna(subset=['legs','staked','payout'])

grouped = df.groupby(['mode','legs']).apply(lambda x: pd.Series({
    'total_tickets':  x['tickets'].sum(),
    'total_staked':   x['staked'].sum(),
    'total_payout':   x['payout'].sum(),
    'total_profit':   x['profit'].sum(),
    'weighted_roi':   (x['payout'].sum() - x['staked'].sum()) / x['staked'].sum() if x['staked'].sum() > 0 else None,
    'avg_win_rate':   x['win_rate'].mean(),
    'avg_cash_rate':  x['cash_rate'].mean(),
    'days_sampled':   x['date'].nunique(),
    'days_profitable': (x.groupby('date')['profit'].sum() > 0).sum(),
})).reset_index()

grouped = grouped.sort_values(['mode','weighted_roi'], ascending=[True, False])

print('=== WEIGHTED ROI BY MODE + LEGS ===')
print(grouped.to_string(index=False))

print()
print('=== VERDICT ===')
for _, r in grouped.iterrows():
    roi = r['weighted_roi']
    if roi is None:
        continue
    if roi > 0.05:
        verdict = 'KEEP -- profitable'
    elif roi > -0.10:
        verdict = 'MARGINAL -- monitor'
    elif roi < -0.30:
        verdict = 'DROP -- heavy loser'
    else:
        verdict = 'WEAK -- consider dropping'
    print(f"  {r['mode']:5s} {int(r['legs'])}-leg: ROI={roi:+.1%}  win={r['avg_win_rate']:.1%}  cash={r['avg_cash_rate']:.1%}  days={int(r['days_sampled'])}  profitable_days={int(r['days_profitable'])}  --> {verdict}")
