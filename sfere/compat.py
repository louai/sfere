"""Loads GLFW and Dear ImGui so they can share one process on Wayland.

The `glfw` package picks a Wayland-only GLFW build on a Wayland session. The
native module inside `imgui-bundle` links against two X11-only GLFW functions
(glfwGetX11Display / glfwGetX11Window) that this build doesn't export, so
importing it fails. sfere never uses ImGui's native window handles (it renders
ImGui itself with moderngl), so a tiny stub library supplying those two symbols
is enough. It is compiled once with the system C compiler and loaded globally
before imgui-bundle is imported.

Importing imgui-bundle *first* would also work, but then everything uses the
copy of GLFW bundled with imgui-bundle, which supports X11 only.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path

_NATIVE = Path(__file__).parent / "_native"
_SHIM_SRC = _NATIVE / "glfw_x11_shim.c"
_SHIM_LIB = _NATIVE / "libglfw_x11_shim.so"

_loaded = None


def _build_shim() -> Path:
    if _SHIM_LIB.exists() and _SHIM_LIB.stat().st_mtime >= _SHIM_SRC.stat().st_mtime:
        return _SHIM_LIB
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise RuntimeError("A C compiler (cc) is needed once to build sfere's GLFW compatibility stub")
    subprocess.run([cc, "-shared", "-fPIC", "-O2", "-o", str(_SHIM_LIB), str(_SHIM_SRC)], check=True)
    return _SHIM_LIB


def load_gui(force_x11: bool = False):
    """Returns (glfw, imgui). Call before creating any window."""
    global _loaded
    if _loaded:
        return _loaded
    if force_x11:
        os.environ["PYGLFW_LIBRARY_VARIANT"] = "x11"
    import glfw  # noqa: loads libglfw.so.3 (Wayland build on a Wayland session)

    if not force_x11:
        ctypes.CDLL(str(_build_shim()), mode=ctypes.RTLD_GLOBAL)
    from imgui_bundle import imgui  # noqa: binds to the GLFW loaded above

    _loaded = (glfw, imgui)
    return _loaded
