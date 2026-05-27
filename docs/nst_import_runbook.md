# NST Line Stats Manual Import Runbook

## When to use
Daily before NHL pipeline runs. Required until Option A (Playwright CDP)
is built. Takes ~3 minutes.

## Step 1 — Export from NST browser
1. Go to: https://www.naturalstattrick.com/playerteams.php?fromseason=20252026&thruseason=20252026&stype=2&sit=5v5&score=all&rate=n&team=VGK&pos=F&loc=B&toi=0&gpfilt=none&fd=&td=&tgp=410&lines=2&draftteam=ALL
2. Change `team=VGK` to your target team abbrev (or run per-team)
3. Hit Submit — wait for table to load (player rows visible)
4. Open browser console (F12 → Console) and paste:

const dt = $('table').DataTable();
const headers = dt.columns().header().toArray().map(h => h.innerText.trim());
const data = dt.rows().data().toArray();
const csvRows = [headers.join(',')];
data.forEach(row => {
    const div = document.createElement('div');
    const clean = row.map(cell => {
        div.innerHTML = cell;
        return '"' + (div.innerText || '').trim().replace(/"/g,'""') + '"';
    });
    csvRows.push(clean.join(','));
});
const blob = new Blob([csvRows.join('\n')], {type: 'text/csv'});
const a = document.createElement('a');
a.href = URL.createObjectURL(blob);
a.download = 'nst_vgk_5v5_players.csv';
document.body.appendChild(a);
a.click();
document.body.removeChild(a);
console.log('Exported', data.length, 'rows');

5. File saves to H:\halek\ProfileFromC\Downloads\

## Step 2 — Import into cache
```powershell
cd H:\halek\ProfileFromC\Desktop\PropORACLE
Move-Item "H:\halek\ProfileFromC\Downloads\nst_vgk_5v5_players.csv" `
  "Sports\NHL\data\nst_vgk_5v5_import.csv" -Force

py Sports/NHL/scripts/refresh_nst_cache.py `
  --import-csv "Sports\NHL\data\nst_vgk_5v5_import.csv" `
  --sit 5v5 --team VGK --season 20252026 --skip-pp
```

## Step 3 — Verify
```powershell
py -c "
import pandas as pd
df = pd.read_csv('Sports/NHL/data/nst_line_combos_cache.csv')
print('Rows:', len(df))
print(df[['Line','TOI','CF%']].head(5).to_string())
"
```
Target: 15-40 rows, TOI and CF% populated with real values.

## Notes
- Repeat for each team on tonight's slate (change team= param in URL)
- sit=5v5 is standard; also run --sit pp for power play context
- Cache is append+dedupe — safe to re-run
- Non-breaking spaces in NST column names are auto-normalized on import
- Option A (automated Playwright fetch) is the long-term fix — see
  Sports/NHL/scripts/nst_client.py fetch_line_combos() for the stub
