"""
engine_bridge.py

The engine lives in engine/ (a sibling of backend/), not inside the
backend package. This file makes it importable, and finds the demo
scenario file.

NOTE: two environment variables let us override the locations later
when this is packaged for AWS Lambda, where the folder layout is
different. Locally you don't set them - the defaults just work.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

ENGINE_DIR = os.environ.get("REDHOUSE_ENGINE_DIR",
                            os.path.join(_REPO_ROOT, "engine"))
SCENARIO_PATH = os.environ.get("REDHOUSE_SCENARIO",
                               os.path.join(_REPO_ROOT, "scenarios", "demo_house.json"))

# After this line, `from twin import Twin` works anywhere in the backend.
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)