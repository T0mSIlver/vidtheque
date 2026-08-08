"""Test package.

The ``__init__.py`` is load-bearing: without it pytest imports this directory's
``conftest.py`` as the top-level module ``conftest``, which collides with
``worker/tests/conftest.py`` — the worker's tests do ``from conftest import …``
and would get ours. As a package, ours is ``tests.conftest`` and the two cannot
shadow each other.
"""
