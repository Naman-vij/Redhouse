"""
conftest.py

NOTE: pytest runs from inside tests/, so it can't see the engine
files one folder up. This file adds engine/ to Python's search path
so `from twin import Twin` works inside the tests. pytest loads
conftest.py automatically - you never import it yourself.
"""
import os
import sys

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)