"""Typed bindings for the Win32 functions the toolkit uses.

This package is the only code that touches ctypes. ctypes itself is untyped, so
every function here has an exact signature, and every value is converted to a
concrete type before it leaves. Nothing untyped crosses into the rest of the
toolkit. `shapes` is plain Python; every other module needs Windows.
"""
