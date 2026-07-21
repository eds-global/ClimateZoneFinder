"""Streamlit compatibility shim.

Analysis modules import ``st`` from here instead of importing streamlit
directly, so they can be imported by non-Streamlit consumers (the FastAPI
report/chart service) where streamlit is not installed.

Under Streamlit this re-exports the real module unchanged. Without it, a
stub is provided whose ``cache_data``/``cache_resource`` decorators return
the function unmodified (no caching — several cached functions take
DataFrames, which are unhashable) while exposing ``__wrapped__`` for
callers that already bypass the cache that way (see combined_report.py).
Every other attribute resolves to a no-op that swallows calls, attribute
access, context-manager use, and iteration, since ``render()`` UI code is
never executed outside Streamlit.
"""

from __future__ import annotations

try:
    import streamlit as st  # noqa: F401
except ImportError:
    import functools
    import types

    def _cache_stub(func=None, **_kwargs):
        def _wrap(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            wrapper.__wrapped__ = fn
            wrapper.clear = lambda: None
            return wrapper

        if callable(func):
            return _wrap(func)
        return _wrap

    class _Noop:
        """Callable, iterable, context-manager-capable black hole."""

        def __call__(self, *args, **kwargs):
            return self

        def __getattr__(self, name):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return iter(())

        def __bool__(self):
            return False

    _noop = _Noop()

    class _StubStreamlit(types.SimpleNamespace):
        def __getattr__(self, name):
            return _noop

    st = _StubStreamlit(
        cache_data=_cache_stub,
        cache_resource=_cache_stub,
        fragment=lambda fn=None, **kw: (fn if callable(fn) else (lambda f: f)),
        session_state={},
    )

__all__ = ["st"]
