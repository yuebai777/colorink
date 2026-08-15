"""Colorink core package (sync backends, updater, config, hotkeys).

Explicit package marker: previously this relied on PEP 420 implicit
namespace packages, which is fragile (any other ``core/`` directory earlier
on sys.path would shadow this one) and complicates PyInstaller collection.
"""
