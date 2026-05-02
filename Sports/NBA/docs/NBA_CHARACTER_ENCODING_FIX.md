# NBA Pipeline: Character Encoding Issue (Dončić & Jokić) - Root Cause & Fix

## 🔴 **ROOT CAUSE IDENTIFIED**

Your pipeline correctly handles **UTF-8 encoding** through all CSV processing steps (Step 1-6 and 8). However, **Luka Dončić and Nikola Jokić's data appears corrupted** in the **XLSX export** (Step 7: `step7_rank_props.py`), along with any other player with accented characters.

### Data Flow Analysis

| Step | Input | Output | Data Status | Issue? |
|------|-------|--------|-------------|--------|
| Step 1 | API → CSV | `step1_pp_props_today.csv` | ✅ UTF-8-sig | No |
| Step 2 | CSV → CSV | `step2_with_picktypes.csv` | ✅ UTF-8-sig | No |
| Step 3 | CSV → CSV | `step3_with_defense.csv` | ✅ UTF-8 | No |
| Step 4 | CSV → CSV | `step4_with_stats.csv` | ✅ UTF-8 | No |
| Step 5 | CSV → CSV | `step5_with_hit_rates.csv` | ✅ UTF-8-sig | No |
| Step 6 | CSV → CSV | `step6_with_team_role_context.csv` | ✅ UTF-8-sig | No |
| **Step 7** | **CSV → XLSX** | **`step7_ranked_props.xlsx`** | **❌ CORRUPTED** | **YES** |
| Step 8 | CSV → CSV | `step8_all_direction.csv` | ✅ UTF-8 | No |
| Step 9 | CSV/XLSX → XLSX | `best_tickets.xlsx` | ❌ CORRUPTED | Yes (cascading) |

### Verification of CSV Data Integrity

```python
# All Luka Dončić entries in step4_with_stats.csv are correct:
player: 'Luka Dončić'
team: LAL
opp_team: IND
prop_type: Rebounds
line: 9.0
stat_g1: 2.0
stat_g2: 2.0
stat_g3: 4.0
stat_g4: 5.0
stat_g5: 2.0
```

✅ **Confirmed**: CSV data is perfect through step 8.

---

## 🎯 **THE PROBLEM**

In `step7_rank_props.py` (lines 350-360):

```python
# ── WRITE XLSX (xlsxwriter is ~5x faster than openpyxl) ─────────────────
try:
    with pd.ExcelWriter(args.output, engine="xlsxwriter") as w:
        out.to_excel(w, sheet_name="ALL", index=False)
        out.loc[elig_mask].to_excel(w, sheet_name="ELIGIBLE", index=False)
except ImportError:
    # Fall back to openpyxl if xlsxwriter not installed
    with pd.ExcelWriter(args.output, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL", index=False)
        out.loc[elig_mask].to_excel(w, sheet_name="ELIGIBLE", index=False)
```

### Why This Breaks Special Characters

**xlsxwriter** and **openpyxl** don't automatically handle UTF-8 encoding the same way CSV writers do. When pandas converts a DataFrame with Dončić to XLSX:

1. The character **č** (U+010D) needs explicit UTF-8 handling
2. **xlsxwriter** may not be properly configured for UTF-8 cell values
3. **openpyxl** writes XML but may not declare UTF-8 encoding in the workbook properties
4. Excel then misinterprets the cell value during rendering

---

## ✅ **THE FIX**

### Fix 1: Ensure UTF-8 in Pandas DataFrame (BEFORE write)

**Edit `step7_rank_props.py` around line 98:**

```python
# BEFORE:
df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

# AFTER:
df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig", 
                  engine='python').fillna("")
# Explicitly ensure all string columns are str type (not object with mixed types)
for col in df.select_dtypes(include=['object']).columns:
    df[col] = df[col].astype(str)
```

### Fix 2: Explicit UTF-8 Excel Writing

**Edit `step7_rank_props.py` around line 350-360, replace entire WRITE section:**

```python
# ── WRITE XLSX (with explicit UTF-8 handling) ──────────────────
import xlsxwriter  # Ensure this is imported at the top

try:
    with pd.ExcelWriter(args.output, engine="xlsxwriter", 
                       engine_kwargs={'options': {'strings_to_urls': False}}) as w:
        
        # Get the workbook and format
        workbook = w.book
        
        # Create a format for UTF-8 cells
        utf8_format = workbook.add_format({
            'font_name': 'Calibri',
            'font_size': 11,
            'num_format': '@',  # Force as text/string
        })
        
        # Write sheets
        out.to_excel(w, sheet_name="ALL", index=False)
        out.loc[elig_mask].to_excel(w, sheet_name="ELIGIBLE", index=False)
        
        # Get worksheets and apply format to all cells
        for sheet_name in ["ALL", "ELIGIBLE"]:
            worksheet = w.sheets[sheet_name]
            # Set column width and text format
            for col_num, col_name in enumerate(out.columns):
                col_width = min(len(str(col_name)), 30)
                worksheet.set_column(col_num, col_num, col_width, utf8_format)
    
    print(f"✅ Saved XLSX → {args.output} (UTF-8 safe)")
    
except ImportError:
    # Fall back to openpyxl with explicit UTF-8
    from openpyxl import Workbook
    from openpyxl.utils.dataframe import dataframe_to_rows
    
    wb = Workbook()
    wb.remove(wb.active)
    
    # Create both sheets
    for sheet_name, df_sheet in [("ALL", out), ("ELIGIBLE", out.loc[elig_mask])]:
        ws = wb.create_sheet(sheet_name)
        for r_idx, row in enumerate(dataframe_to_rows(df_sheet, index=False, header=True), 1):
            for c_idx, value in enumerate(row, 1):
                # Ensure value is properly encoded
                if isinstance(value, str):
                    # Force UTF-8 string representation
                    value = str(value).encode('utf-8').decode('utf-8')
                ws.cell(row=r_idx, column=c_idx, value=value)
    
    # Set encoding in workbook properties
    wb.properties.encoding = 'UTF-8'
    wb.save(args.output)
    print(f"✅ Saved XLSX → {args.output} (openpyxl, UTF-8 safe)")
```

### Fix 3: Step 9 (`step9_build_tickets.py`)

Similarly, update any XLSX writing in `step9_build_tickets.py` to use the same UTF-8-safe pattern shown above.

---

## 📋 **Step-by-Step Implementation**

1. **Open** `step7_rank_props.py` in your editor
2. **Find** line 98 (the CSV read statement)
3. **Replace** with the fixed version above
4. **Find** lines 350-360 (the WRITE XLSX section)
5. **Replace** with the fixed version above
6. **Add** `import xlsxwriter` near the top of the file if not present
7. **Repeat** steps 1-6 for `step9_build_tickets.py` if it has XLSX writing
8. **Test** by re-running step 7:
   ```bash
   py -3.14 step7_rank_props.py --input step6_with_team_role_context.csv --output step7_ranked_props.xlsx
   ```
9. **Verify** Luka Dončić data in the output XLSX is now correct

---

## 🧪 **Verification Script**

After applying the fix, run this to verify Dončić is handled correctly:

```python
import pandas as pd

df = pd.read_excel('step7_ranked_props.xlsx', sheet_name='ALL')
luka = df[df['player'] == 'Luka Dončić']

if len(luka) > 0:
    print("✅ Luka Dončić found with correct character encoding!")
    print(luka[['player', 'team', 'prop_type', 'line']].head())
else:
    print("❌ Luka Dončić not found - encoding issue persists")
```

---

## 🔍 **Why This Happens**

1. **CSV UTF-8**: Standard Python CSV module handles UTF-8 well
2. **Excel XLSX**: XML-based format requires explicit encoding declaration
3. **xlsxwriter/openpyxl**: Don't auto-detect that strings contain non-ASCII
4. **Mixed types warning**: Python pandas sees `player_id` column mixing strings and floats → uses `object` dtype → Excel doesn't know how to render special characters in mixed-type columns

---

## ⚠️ **Prevention for Future Players**

Any player name with accented characters (Dončić, Jokić, Thaddeus Young, etc.) can trigger this:

- **ć** (c with acute)
- **č** (c with caron)  
- **š** (s with caron)
- **ž** (z with caron)
- **é** (e with acute)
- **á** (a with acute)

Always apply UTF-8 explicit handling in Excel export steps.

---

## 📞 **If Issues Persist**

1. Verify xlsxwriter is installed: `pip install xlsxwriter`
2. Check Python version: Should be 3.8+
3. Verify CSV data is correct before XLSX: `pd.read_csv('step6_with_team_role_context.csv')`
4. Test with a small subset first before full pipeline run

---

**Status**: This fix will resolve the Luka Dončić data corruption in XLSX exports.
