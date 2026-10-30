"""Shared fixtures and import path for the test tree.

Its presence puts `tests/` on `sys.path`, so a suite in any subdirectory can import the shared
helpers next to it (today `hashing_embedder`) instead of keeping its own copy. There are no
fixtures here: the object fakes stay in the module of the capability that owns them.
"""
