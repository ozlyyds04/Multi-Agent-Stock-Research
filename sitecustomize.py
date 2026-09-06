import os

# Prevent any accidental breakpoint() hooks from causing debugger/break exceptions
os.environ.setdefault("PYTHONBREAKPOINT", "0")

# Force matplotlib to a non-GUI backend (prevents Windows backend crashes)
os.environ.setdefault("MPLBACKEND", "Agg")
