"""Infinite OCR -- one continuous, structure-aware OCR output for a whole PDF."""

from dotenv import load_dotenv

# Load .env at package import, not just in the CLI, so scripts and notebooks
# that use the library directly pick up BODHAN_API_KEY too. Existing
# environment variables win, so an explicitly exported key is never clobbered.
load_dotenv(override=False)

__version__ = "0.1.0"
