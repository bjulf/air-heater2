"""Make the airheater package importable under pytest without installing it.

pytest puts the test file's own directory on sys.path, not the project root,
so `import airheater` would fail from tests/. This file sits at the root, and
pytest imports it before collecting, which is the usual place to fix that.

The test scripts also insert this path themselves, so they still run with
plain `python tests/test_airheater.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
