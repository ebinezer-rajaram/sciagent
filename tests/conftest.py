"""Puts ``tests/`` on ``sys.path`` so ``slice_tables`` is importable from
anywhere in the suite. Deliberately holds nothing else: a shared table is built
by the module that asks for it, not by collection.
"""
