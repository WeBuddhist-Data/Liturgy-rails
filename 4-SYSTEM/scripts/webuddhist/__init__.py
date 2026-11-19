"""Shared core for talking to the WeBuddhist library: environments, API client, ledgers.

Used by `library.py` (the upload tool) and the linters. Every call names its
environment; nothing defaults silently to prod.
"""
