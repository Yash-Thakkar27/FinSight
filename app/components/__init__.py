"""Shared pieces of the FinSight Streamlit app.

Importing this package puts the project root on sys.path, so pages can import
`src` and `config` however the app is launched.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
