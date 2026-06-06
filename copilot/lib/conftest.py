"""pytest config for the Copilot variant — put lib/ on sys.path so the modules
import as top-level (matching how the hooks invoke them)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
