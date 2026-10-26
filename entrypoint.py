"""PyInstaller entry point (the package itself is importable as `imefuse`)."""
import sys

from imefuse.cli import main

if __name__ == "__main__":
    sys.exit(main())
