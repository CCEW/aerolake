import numpy as np
import json
import uuid
from datetime import datetime, timezone

# 1. Generate 2 Msps ci16_le mock noise (simulating GNSS L1)
sample_rate = 2000000
samples = np.random.randint(-2048, 2047, sample_rate * 2, dtype=np.int16)

# Save Channel 0 and Channel 1 binary files
samples.tofile("mock_ch0.sigmf-data")
samples.tofile("mock_ch1.sigmf-data")

# 2. Generate Base Metadata
base_meta = {
    "global": {
        "aerolake:signal_type": "gnss_l1",
        "core:author": "Camila Nino Francia",
        "core:datatype": "ci16_le",
        "core:sample_rate": sample_rate,
        "core:version": "1.0.0"
    },
    "captures": [{
        "core:datetime": datetime.now(timezone.utc).isoformat(),
        "core:frequency": 1575420000,
        "core:sample_start": 0
        
    }],
        "annotations": [{
        "core:sample_start": 0,
        "core:sample_count": sample_rate,   # complex samples = 1 s
        "core:freq_lower_edge": 1575420000 - sample_rate / 2,
        "core:freq_upper_edge": 1575420000 + sample_rate / 2,
        "core:label": "mock_gnss_l1"
    }]

}

# 3. Write individual meta files
with open("mock_ch0.sigmf-meta", "w") as f:
    json.dump(base_meta, f, indent=4)
with open("mock_ch1.sigmf-meta", "w") as f:
    json.dump(base_meta, f, indent=4)