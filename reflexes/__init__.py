"""Reflexes: small Python functions an agent runs without thinking.

    from reflexes import run, resume, Local
    outcome = run(source, {"folder": "~/Downloads"}, Local(), dry=True)
"""

from .engine import Outcome, Refused, describe, resume, run
from .host import Local

__all__ = ["run", "resume", "describe", "Outcome", "Refused", "Local"]
__version__ = "0.2.0.dev0"
