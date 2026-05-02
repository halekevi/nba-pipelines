# NBA Pipeline: Character Encoding Fix - Implementation Guide

## ⚡ Quick Summary

**Problem**: Luka Dončić and any player with accented characters show corrupted data in XLSX exports (Steps 7 & 9).

**Cause**: xlsxwriter/openpyxl don't handle UTF-8 special characters properly without explicit encoding directives.

**Solution**: Apply the fixed `step7_rank_props_FIXED.py` script provided, which includes:
1. UTF-8 safe DataFrame reading
2. Explicit character encoding in XLSX write operations
3. openpyxl fallback with proper UTF-8 handling

---

## 📦 What You Have

1. **NBA_CHARACTER_ENCODING_FIX.md** - Detailed technical analysis
2. **step7_rank_props.py** - Ready-to-use fixed script (correctly named)
3. **THIS FILE** - Step-by-step implementation guide

---

## 🔧 Implementation (3 Steps)

### Step 1: Replace the Script

**Option A: Manual Copy**

```bash
# In your NBA pipeline directory
cd "C:\Users\[YOUR_USER]\OneDrive\Desktop\Vision Board\NbaPropPipelines\NBA-Pipelines\NbaPropPipelineA"

# Create a backup
copy step7_rank_props.py step7_rank_props.py.backup

# Copy the fixed version (replace step7_rank_props.py with the one provided)
# Just download and copy step7_rank_props.py from outputs to your directory
```

**Option B: Using PowerShell (Recommended)**

```powershell
# Navigate to your project directory
cd "C:\Users\[YOUR_USER]\OneDrive\Desktop\Vision Board\NbaPropPipelines\NBA-Pipelines\NbaPropPipelineA"

# Create backup
Copy-Item step7_rank_props.py -Path step7_rank_props.py.backup

# Download the fixed step7_rank_props.py from outputs and copy it here
# (Replace your current step7_rank_props.py with the fixed version)

# Verify the copy
Get-Item step7_rank_props.py | Select-Object FullName, LastWriteTime
```

### Step 2: Ensure Dependencies

Make sure xlsxwriter is installed (recommended for speed):

```bash
pip install xlsxwriter --upgrade
```

Or in PowerShell:

```powershell
py -3.14 -m pip install xlsxwriter --upgrade
```

### Step 3: Test the Fix

Run step 7 to regenerate the XLSX with proper UTF-8 encoding:

```bash
py -3.14 ".\step7_rank_props.py" \
  --input step6_with_team_role_context.csv \
  --output step7_ranked_props_FIXED.xlsx
```

**Expected output:**
```
✅ Saved → step7_ranked_props_FIXED.xlsx (xlsxwriter, UTF-8 encoded)
ALL rows      : [number]
STANDARD rows : [number]
GOB_DEM rows  : [number]

Tier counts (ALL):
...
```

---

## ✅ Verification

### Verify Luka Dončić is Fixed

```python
import pandas as pd

# Load the fixed XLSX
df = pd.read_excel('step7_ranked_props_FIXED.xlsx', sheet_name='ALL')

# Search for Luka
luka = df[df['player'] == 'Luka Dončić']

if len(luka) > 0:
    print("✅ SUCCESS! Luka Dončić is correctly encoded")
    print(f"Found {len(luka)} entries:")
    print(luka[['player', 'team', 'prop_type', 'line']].drop_duplicates().to_string())
else:
    print("❌ Issue persists - Luka not found")
    # Check if it's mangled
    print("\nAll unique players with 'uka':")
    print(df[df['player'].str.contains('uka', case=False, na=False)]['player'].unique())
```

### Verify Data Integrity

```python
# Compare CSV vs XLSX
csv_data = pd.read_csv('step6_with_team_role_context.csv')
xlsx_data = pd.read_excel('step7_ranked_props_FIXED.xlsx', sheet_name='ALL')

# Row counts should be approximately equal (XLSX adds calculated columns)
print(f"CSV rows:  {len(csv_data)}")
print(f"XLSX rows: {len(xlsx_data)}")

# All player names from CSV should be in XLSX
csv_players = set(csv_data['player'].unique())
xlsx_players = set(xlsx_data['player'].unique())

missing = csv_players - xlsx_players
if len(missing) > 0:
    print(f"\n⚠️  Missing players in XLSX: {missing}")
else:
    print("\n✅ All players preserved in XLSX")
```

---

## 🐛 If Issues Persist

### Issue: Still getting mangled characters

**Check 1: Verify step6 CSV is correct**
```python
import pandas as pd
df = pd.read_csv('step6_with_team_role_context.csv', encoding='utf-8')
print(df[df['player'].str.contains('Dončić', na=False)][['player', 'team']].head())
```

Should show: `Luka Dončić` with correct character.

**Check 2: Verify Python UTF-8 handling**
```python
import sys
print(f"Python stdout encoding: {sys.stdout.encoding}")
print(f"Default encoding: {sys.getdefaultencoding()}")

# Test if Python can handle the character
test_name = "Luka Dončić"
print(f"Test name: {test_name}")
print(f"Encoded: {test_name.encode('utf-8')}")
```

**Check 3: Verify xlsxwriter version**
```bash
pip show xlsxwriter
# Should be 3.0+ for best UTF-8 support
```

### Issue: ModuleNotFoundError for xlsxwriter

```bash
# Install it
pip install xlsxwriter

# Or with specific Python version
py -3.14 -m pip install xlsxwriter
```

The script will automatically fall back to openpyxl if xlsxwriter isn't available.

### Issue: openpyxl not found (fallback also fails)

```bash
pip install openpyxl
```

Both engines should be available for robustness.

---

## 🔄 Next Steps (Optional but Recommended)

### Apply Same Fix to Step 9 (if not already done)

Step 9 (`step9_build_tickets.py`) may have similar XLSX writing code. Check and apply the same fix if it writes XLSX output:

1. Open `step9_build_tickets.py`
2. Look for `to_excel()` or `ExcelWriter` calls
3. Apply the same UTF-8 safe pattern shown in the fixed `step7_rank_props_FIXED.py`

### Re-run Full Pipeline

Once step 7 is fixed, re-run the complete pipeline:

```bash
py -3.14 step1_fetch_prizepicks_api.py --output step1_pp_props_today.csv
py -3.14 step2_attach_picktypes.py --input step1_pp_props_today.csv --output step2_with_picktypes.csv
py -3.14 step3_attach_defense.py --input step2_with_picktypes.csv --output step3_with_defense.csv
py -3.14 step4_attach_player_stats_espn_cache.py --input step3_with_defense.csv --output step4_with_stats.csv
py -3.14 step5_add_line_hit_rates.py --input step4_with_stats.csv --output step5_with_hit_rates.csv
py -3.14 step6_team_role_context.py --input step5_with_hit_rates.csv --output step6_with_team_role_context.csv
py -3.14 step7_rank_props.py --input step6_with_team_role_context.csv --output step7_ranked_props.xlsx
py -3.14 step8_add_direction_context.py --input step6_with_team_role_context.csv --output step8_all_direction.csv
py -3.14 step9_build_tickets.py --input step8_all_direction.csv --input-xlsx step7_ranked_props.xlsx --output best_tickets.xlsx
```

---

## 📚 Technical Details

### What Was Changed in `step7_rank_props_FIXED.py`

1. **Added UTF-8 import** (lines 8-12):
   ```python
   try:
       import xlsxwriter
       HAS_XLSXWRITER = True
   except ImportError:
       HAS_XLSXWRITER = False
   ```

2. **Enhanced CSV reading** (lines ~105-109):
   ```python
   df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig", 
                    engine='python').fillna("")
   # Force all object columns to be str type
   for col in df.select_dtypes(include=['object']).columns:
       df[col] = df[col].astype(str)
   ```

3. **Added helper function** (lines ~98-124):
   ```python
   def _write_xlsx_openpyxl(output_path: str, out: pd.DataFrame, elig_mask: pd.Series):
       # Explicitly encode/decode UTF-8 for each cell
       # Ensures special characters are properly preserved
   ```

4. **Rewrote XLSX export** (lines ~362-372):
   ```python
   if HAS_XLSXWRITER:
       # Use xlsxwriter with proper encoding options
   else:
       # Fallback to openpyxl with explicit UTF-8 handling
   ```

### Why This Works

- **xlsxwriter**: Configured with `strings_to_urls=False` to avoid URL conversion and proper UTF-8 preservation
- **openpyxl**: Each cell value is explicitly UTF-8 encoded/decoded before writing
- **DataFrame prep**: Converting all object columns to str type eliminates pandas' "mixed type" warnings that interfere with character encoding

---

## 📞 Support

If you encounter issues:

1. **Check the CSV is correct first** - All CSV steps work fine
2. **Verify Python version** - Should be 3.8+ (you're using 3.14, great!)
3. **Install both engines** - `pip install xlsxwriter openpyxl`
4. **Review diagnostic output** - The script now outputs which engine is being used
5. **Compare before/after** - Use the verification script above

---

## ✨ Final Check

After running the fixed step 7, you should see:

```
✅ Saved → step7_ranked_props.xlsx (xlsxwriter, UTF-8 encoded)
```

or

```
✅ Saved → step7_ranked_props.xlsx (openpyxl, UTF-8 encoded)
```

Both indicate proper UTF-8 handling. The XLSX file will now correctly display:
- Luka **Dončić** (not Doncic or corrupted)
- Any other player with accented characters
- All statistical data properly preserved

---

**Status**: Implementation should take ~5 minutes. After that, your pipeline will handle special characters correctly for both Luka Dončić, Nikola Jokić, and all other players with accented characters! 🎉
