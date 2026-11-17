"""Test-run defaults. Importing api.main builds the app, which refuses to start with no login configured."""

import os

os.environ.setdefault("ALLOW_UNAUTHENTICATED", "true")
