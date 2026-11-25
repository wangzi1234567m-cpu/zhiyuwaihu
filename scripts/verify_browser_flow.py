"""Compatibility entry point for the current workbench browser acceptance.
Resets only an explicitly designated _test database, then runs the shared verifier.
"""
import os
from pathlib import Path
import subprocess
import sys
from verify_workbench import main

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(root/'scripts/reset_test_db.py'),'--workbench'],cwd=root,env=os.environ.copy(),check=True)
    main()
