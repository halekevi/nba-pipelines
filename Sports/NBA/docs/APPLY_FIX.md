# Fix for step2_attach_picktypes.py

The script is missing `import numpy as np` at the top.

## Quick Fix (2 seconds)

Run this in PowerShell in your NbaPropPipelineA directory:

```powershell
cd "C:\Users\halek\OneDrive\Desktop\Vision Board\NbaPropPipelines\NBA-Pipelines\NbaPropPipelineA"

# Replace the file - add numpy import
(Get-Content "step2_attach_picktypes.py") | 
    ForEach-Object { $_ -replace "^(import pandas as pd)", "import numpy as np`nimport pandas as pd" } | 
    Set-Content "step2_attach_picktypes.py" -Encoding UTF8

# Verify it worked
Get-Content "step2_attach_picktypes.py" -TotalCount 10
```

You should see:
```
import numpy as np
import pandas as pd
```

Then re-run step 2:

```powershell
py -3.14 ".\step2_attach_picktypes.py" --input step1_pp_props_today.csv --output step2_with_picktypes.csv
```

Done! 🚀
