path = r'H:\halek\ProfileFromC\Desktop\PropORACLE\ui_runner\templates\index.html'
with open(path, encoding='utf-8') as f:
    content = f.read()

old = 'function historySeriesForPick(p, n) {\n  const actual = normalizeSeries(p.actual_series).slice(-n);\n  if (actual.length >= Math.min(3, n)) {\n    const lineRaw = normalizeSeries(p.line_series);\n    const lineSeries = lineRaw.length ? lineRaw.slice(-actual.length) : Array.from({length: actual.length}, () => Number(p.line));\n    return {actual, lineSeries};\n  }\n  return null;\n}'

new = 'function historySeriesForPick(p, n) {\n  let actual = normalizeSeries(p.actual_series).slice(-n);\n  if (!actual.length) {\n    const gcols = ["g1","g2","g3","g4","g5","g6","g7","g8","g9","g10"].slice(0, n);\n    const vals = gcols.map(k => p[k]).filter(v => v !== null && v !== undefined && Number.isFinite(Number(v))).map(Number);\n    if (vals.length >= Math.min(3, n)) actual = vals;\n  }\n  if (actual.length >= Math.min(3, n)) {\n    const lineRaw = normalizeSeries(p.line_series);\n    const lineSeries = lineRaw.length ? lineRaw.slice(-actual.length) : Array.from({length: actual.length}, () => Number(p.line));\n    return {actual, lineSeries};\n  }\n  return null;\n}'

if old in content:
    print('FOUND - replacing')
    content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Done')
else:
    print('NOT FOUND')
