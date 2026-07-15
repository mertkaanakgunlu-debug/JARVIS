"""Geo-math computation tool for JARVIS (Faz 18).

Handles geophysical and advanced mathematics computations with a
graceful fallback chain for each optional library:

  solve_symbolic  → SymPy (required: pip install sympy)
  wolfram         → wolframalpha (optional: pip install wolframalpha)
  wave_simulate_2d → Devito (optional) → NumPy FDM fallback
  plot_2d         → Matplotlib/Plotly
  plot_contour    → Matplotlib
  plot_3d_surface → Plotly
  plot_volume     → PyVista (optional)

All plot outputs are saved to data/geo_math_outputs/.
Returns path strings so the user can open them.
"""

from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("data/geo_math_outputs")

# ── Library availability checks ────────────────────────────────────────────────

def _has(lib: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(lib) is not None


# ── solve_symbolic ─────────────────────────────────────────────────────────────

def _solve_symbolic(expression: str, variable: str = "", domain: str = "") -> str:
    """Use SymPy to solve/simplify an expression symbolically."""
    if not _has("sympy"):
        return "⚠ SymPy kurulu değil. Kur: pip install sympy"
    try:
        import sympy as sp
        from sympy import symbols, solve, latex, simplify, diff, integrate, Symbol
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations, implicit_multiplication_application
        )

        transformations = standard_transformations + (implicit_multiplication_application,)

        # Try to parse as equation or expression
        expr_str = expression.strip()

        # Declare common physics symbols
        locals_map: dict[str, Any] = {}
        for sym_name in ["x", "y", "z", "t", "v", "c", "k", "w", "omega",
                          "rho", "mu", "lambda_", "alpha", "beta", "f", "A"]:
            locals_map[sym_name] = Symbol(sym_name, positive=True)
        # Override with user-specified variable
        if variable:
            for v in variable.split(","):
                v = v.strip()
                if v:
                    locals_map[v] = Symbol(v)

        if "=" in expr_str:
            # Equation: solve for variable
            lhs_str, rhs_str = expr_str.split("=", 1)
            lhs = parse_expr(lhs_str.strip(), local_dict=locals_map, transformations=transformations)
            rhs = parse_expr(rhs_str.strip(), local_dict=locals_map, transformations=transformations)
            eq = lhs - rhs
            var = locals_map.get(variable.strip()) if variable else None
            if var is not None:
                solutions = solve(eq, var)
            else:
                # Try to find free symbols
                free = list(eq.free_symbols)
                solutions = solve(eq, free[0]) if free else [simplify(eq)]
            sol_latex = ", ".join(f"$${latex(s)}$$" for s in solutions)
            return (
                f"**Denklem:** ${latex(lhs)} = {latex(rhs)}$\n\n"
                f"**Çözüm:** {sol_latex}"
            )
        else:
            # Expression: simplify + display
            expr = parse_expr(expr_str, local_dict=locals_map, transformations=transformations)
            simplified = simplify(expr)
            return (
                f"**İfade:** ${latex(expr)}$\n\n"
                f"**Sadeleştirilmiş:** $${latex(simplified)}$$"
            )
    except Exception as exc:
        return f"⚠ SymPy hatası: {exc}\n\nİpucu: Denklemi şu formda yaz: 'd2u/dt2 - c**2 * d2u/dx2 = 0' veya 'x**2 + 2*x - 3 = 0'"


# ── WolframAlpha ───────────────────────────────────────────────────────────────

def _wolfram(query: str, settings: "Settings") -> str:
    """Query WolframAlpha for step-by-step solutions."""
    if not _has("wolframalpha"):
        return "⚠ wolframalpha kurulu değil. Kur: pip install wolframalpha"
    app_id = getattr(settings, "wolfram_app_id", "")
    if not app_id:
        return "⚠ WOLFRAM_APP_ID .env'de ayarlı değil. https://developer.wolframalpha.com adresinden al."
    try:
        import wolframalpha
        client = wolframalpha.Client(app_id)
        res = client.query(query)
        # Collect pods
        lines = [f"**WolframAlpha:** {query}", ""]
        for pod in res.pods:
            title = getattr(pod, "title", "")
            for sub in pod.subpods:
                text = getattr(sub, "plaintext", "") or ""
                if text.strip():
                    lines.append(f"**{title}:**")
                    lines.append(f"  {text.strip()}")
                    lines.append("")
        return "\n".join(lines) if len(lines) > 2 else f"Sonuç bulunamadı: {query}"
    except Exception as exc:
        return f"⚠ WolframAlpha hatası: {exc}"


# ── 2D wave simulation ─────────────────────────────────────────────────────────

def _wave_simulate_2d(
    velocity_grid: list | None,
    source_pos: list,
    receivers: list | None = None,
    duration: float = 0.5,
    nz: int = 100,
    nx: int = 100,
    dz: float = 10.0,
    dx: float = 10.0,
    dt: float = 0.001,
) -> str:
    """Run 2D acoustic FDM wave simulation.

    Tries Devito first; falls back to pure NumPy FDM.
    Returns path to snapshot PNG.
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if _has("devito"):
        return _wave_simulate_2d_devito(
            velocity_grid, source_pos, receivers, duration, nz, nx, dz, dx, dt
        )
    else:
        return _wave_simulate_2d_numpy(
            velocity_grid, source_pos, nz, nx, dz, dx, dt, duration
        )


def _wave_simulate_2d_devito(velocity_grid, source_pos, receivers, duration, nz, nx, dz, dx, dt) -> str:
    try:
        import numpy as np
        from devito import Grid, TimeFunction, Function, Eq, Operator, solve as dev_solve

        grid = Grid(shape=(nz, nx), extent=(nz * dz, nx * dx))
        v_arr = np.array(velocity_grid) if velocity_grid else np.full((nz, nx), 2000.0)
        v_arr = v_arr.reshape(nz, nx)

        nt = int(duration / dt)
        u = TimeFunction(name="u", grid=grid, time_order=2, space_order=4)
        v_func = Function(name="v", grid=grid)
        v_func.data[:] = v_arr

        pde = u.dt2 - v_func**2 * u.laplace
        stencil = Eq(u.forward, dev_solve(pde, u.forward))

        # Source (Ricker wavelet)
        from devito import SparseTimeFunction
        src_pos = np.array([[source_pos[0] * dz, source_pos[1] * dx]])
        src = SparseTimeFunction(name="src", grid=grid, npoint=1, nt=nt)
        src.coordinates.data[:] = src_pos
        t_vec = np.arange(nt) * dt
        f0 = 25.0
        t0 = 0.05
        ricker = (1 - 2 * (np.pi * f0 * (t_vec - t0))**2) * np.exp(-(np.pi * f0 * (t_vec - t0))**2)
        src.data[:, 0] = ricker * 1e6

        src_term = src.inject(field=u.forward, expr=src * dt**2 / v_func**2)
        op = Operator([stencil, src_term])
        op(time_M=nt - 2, dt=dt)

        snapshot = u.data[0].copy()
        out_path = _OUTPUT_DIR / "wave2d_devito.png"
        _save_wave_snapshot(snapshot, out_path, title="2D Acoustic Wave (Devito)")
        return f"✅ Devito simülasyonu tamamlandı → {out_path}"

    except Exception as exc:
        logger.debug("Devito simulation failed: %s — falling back to NumPy FDM", exc)
        return _wave_simulate_2d_numpy(velocity_grid, [source_pos[0], source_pos[1]],
                                        nz, nx, dz, dx, dt, duration)


def _wave_simulate_2d_numpy(velocity_grid, source_pos, nz, nx, dz, dx, dt, duration) -> str:
    try:
        import numpy as np

        v_arr = np.array(velocity_grid) if velocity_grid else np.full((nz, nx), 2000.0)
        v_arr = v_arr.reshape(nz, nx)
        nt = int(duration / dt)
        courant = v_arr.max() * dt / min(dx, dz)
        if courant > 0.5:
            dt = 0.5 * min(dx, dz) / v_arr.max()
            nt = int(duration / dt)

        u_prev = np.zeros((nz, nx))
        u_curr = np.zeros((nz, nx))
        u_next = np.zeros((nz, nx))
        iz, ix = int(source_pos[0]), int(source_pos[1])

        t_vec = np.arange(nt) * dt
        f0 = 25.0
        t0 = 0.05
        ricker = (1 - 2 * (np.pi * f0 * (t_vec - t0))**2) * np.exp(-(np.pi * f0 * (t_vec - t0))**2)

        snapshot = None
        for it in range(nt):
            # Laplacian with 2nd-order FD
            lap = (
                (np.roll(u_curr, -1, axis=0) - 2 * u_curr + np.roll(u_curr, 1, axis=0)) / dz**2
                + (np.roll(u_curr, -1, axis=1) - 2 * u_curr + np.roll(u_curr, 1, axis=1)) / dx**2
            )
            u_next = 2 * u_curr - u_prev + v_arr**2 * dt**2 * lap
            u_next[iz, ix] += ricker[it] * dt**2
            # Absorbing boundary (simple taper)
            taper_width = 10
            for i in range(taper_width):
                damp = (i / taper_width) ** 2
                u_next[i, :] *= damp
                u_next[-(i+1), :] *= damp
                u_next[:, i] *= damp
                u_next[:, -(i+1)] *= damp
            u_prev, u_curr = u_curr, u_next.copy()
            if it == nt // 2:
                snapshot = u_curr.copy()

        if snapshot is None:
            snapshot = u_curr

        out_path = _OUTPUT_DIR / "wave2d_numpy.png"
        _save_wave_snapshot(snapshot, out_path, title="2D Acoustic Wave (NumPy FDM)")
        return f"✅ NumPy FDM simülasyonu tamamlandı → {out_path}"

    except Exception as exc:
        return f"⚠ Dalga simülasyonu hatası: {exc}"


def _save_wave_snapshot(snapshot, out_path: Path, title: str = "Wave") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(8, 6), facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")
    vmax = np.percentile(np.abs(snapshot), 98) or 1.0
    ax.imshow(snapshot, cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_title(title, color="#e0e0e0")
    ax.set_xlabel("x (grid)", color="#aaa")
    ax.set_ylabel("z (depth)", color="#aaa")
    ax.tick_params(colors="#aaa")
    plt.tight_layout()
    fig.savefig(str(out_path), dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)


# ── 2D plot ────────────────────────────────────────────────────────────────────

def _plot_2d(
    x_data: list,
    y_data: list,
    plot_type: str = "line",
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
) -> str:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        x = np.array(x_data)
        y = np.array(y_data)
        fig, ax = plt.subplots(figsize=(8, 5), facecolor="#1a1a2e")
        ax.set_facecolor("#16213e")
        if plot_type == "scatter":
            ax.scatter(x, y, c="#22d3ee", s=10, alpha=0.8)
        else:
            ax.plot(x, y, color="#22d3ee", linewidth=1.5)
        ax.set_title(title or "2D Plot", color="#e0e0e0")
        ax.set_xlabel(xlabel or "x", color="#aaa")
        ax.set_ylabel(ylabel or "y", color="#aaa")
        ax.tick_params(colors="#aaa")
        ax.grid(True, alpha=0.2, color="#555")
        plt.tight_layout()
        safe_title = (title or "plot_2d").replace(" ", "_")[:30]
        out_path = _OUTPUT_DIR / f"{safe_title}.png"
        fig.savefig(str(out_path), dpi=120, facecolor=fig.get_facecolor())
        plt.close(fig)
        return f"✅ 2D grafik → {out_path}"
    except Exception as exc:
        return f"⚠ 2D plot hatası: {exc}"


# ── Contour plot ───────────────────────────────────────────────────────────────

def _plot_contour(
    grid_data: list,
    levels: int = 20,
    cmap: str = "RdBu_r",
    title: str = "",
    xlabel: str = "x",
    ylabel: str = "z (depth)",
) -> str:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        data = np.array(grid_data)
        if data.ndim == 1:
            n = int(len(data)**0.5)
            data = data.reshape(n, n)

        fig, ax = plt.subplots(figsize=(9, 7), facecolor="#1a1a2e")
        ax.set_facecolor("#16213e")
        # Geophysics convention: depth increases downward
        cs = ax.contourf(data, levels=levels, cmap=cmap)
        cl = ax.contour(data, levels=levels // 2, colors="white", alpha=0.3, linewidths=0.5)
        fig.colorbar(cs, ax=ax, label="Amplitude")
        ax.set_title(title or "Contour Map", color="#e0e0e0")
        ax.set_xlabel(xlabel, color="#aaa")
        ax.set_ylabel(ylabel, color="#aaa")
        ax.invert_yaxis()  # depth downward
        ax.tick_params(colors="#aaa")
        plt.tight_layout()
        safe_title = (title or "contour").replace(" ", "_")[:30]
        out_path = _OUTPUT_DIR / f"{safe_title}.png"
        fig.savefig(str(out_path), dpi=120, facecolor=fig.get_facecolor())
        plt.close(fig)
        return f"✅ Kontur haritası → {out_path}"
    except Exception as exc:
        return f"⚠ Kontur plot hatası: {exc}"


# ── 3D surface plot (Plotly) ───────────────────────────────────────────────────

def _plot_3d_surface(grid_data: list, title: str = "") -> str:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not _has("plotly"):
        return "⚠ plotly kurulu değil. Kur: pip install plotly"
    try:
        import numpy as np
        import plotly.graph_objects as go

        data = np.array(grid_data)
        if data.ndim == 1:
            n = int(len(data)**0.5)
            data = data.reshape(n, n)

        fig = go.Figure(
            data=[go.Surface(z=data.tolist(), colorscale="RdBu", reversescale=True)],
            layout=go.Layout(
                title=title or "3D Surface",
                scene=dict(
                    xaxis_title="x",
                    yaxis_title="y",
                    zaxis_title="z",
                    bgcolor="#16213e",
                ),
                paper_bgcolor="#1a1a2e",
                font=dict(color="#e0e0e0"),
            ),
        )
        out_path = _OUTPUT_DIR / f"{(title or '3d_surface').replace(' ','_')[:30]}.html"
        fig.write_html(str(out_path))
        return f"✅ 3D yüzey grafiği → {out_path}"
    except Exception as exc:
        return f"⚠ 3D surface hatası: {exc}"


# ── Volume render (PyVista) ────────────────────────────────────────────────────

def _plot_volume(volume_3d: list, isosurface_values: list | None = None, title: str = "") -> str:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not _has("pyvista"):
        # Fallback: render a 2D midplane slice with matplotlib
        return _plot_volume_fallback(volume_3d, title)
    try:
        import numpy as np
        import pyvista as pv

        vol = np.array(volume_3d)
        if vol.ndim != 3:
            return f"⚠ 3D veri gerekli (shape={vol.shape})"

        grid = pv.ImageData()
        grid.dimensions = np.array(vol.shape) + 1
        grid.cell_data["values"] = vol.flatten(order="F")

        iso_vals = isosurface_values or [float(np.percentile(vol, 75))]

        pl = pv.Plotter(off_screen=True, window_size=(800, 600))
        pl.background_color = "#1a1a2e"
        for iv in iso_vals:
            iso = grid.contour([iv])
            if iso.n_points > 0:
                pl.add_mesh(iso, opacity=0.7, cmap="RdBu", show_scalar_bar=True)
        pl.add_title(title or "Volume Render", color="white")
        out_path = _OUTPUT_DIR / f"{(title or 'volume').replace(' ','_')[:30]}.png"
        pl.screenshot(str(out_path))
        pl.close()
        return f"✅ Volume render → {out_path}"
    except Exception as exc:
        return f"⚠ PyVista hatası: {exc}"


def _plot_volume_fallback(volume_3d: list, title: str = "") -> str:
    """2D midplane slice fallback when PyVista not installed."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        vol = np.array(volume_3d)
        if vol.ndim < 2:
            return "⚠ Geçersiz veri boyutu"
        if vol.ndim == 3:
            mid = vol.shape[0] // 2
            slice_2d = vol[mid]
        else:
            slice_2d = vol

        fig, ax = plt.subplots(facecolor="#1a1a2e")
        ax.set_facecolor("#16213e")
        ax.imshow(slice_2d, cmap="RdBu_r", aspect="auto")
        ax.set_title(f"{title} (orta dilim — PyVista kuruluysa 3D göster)", color="#e0e0e0")
        plt.tight_layout()
        out_path = _OUTPUT_DIR / f"{(title or 'volume_slice').replace(' ','_')[:30]}.png"
        fig.savefig(str(out_path), dpi=120, facecolor=fig.get_facecolor())
        plt.close(fig)
        return (
            f"✅ 2D dilim kaydedildi → {out_path}\n"
            f"(Tam 3D görselleştirme için: pip install pyvista)"
        )
    except Exception as exc:
        return f"⚠ Volume fallback hatası: {exc}"


# ── Main control function ─────────────────────────────────────────────────────

def geo_math_control(
    action: str,
    *,
    expression: str = "",
    variable: str = "",
    domain: str = "",
    query: str = "",
    x_data: str = "[]",
    y_data: str = "[]",
    grid_data: str = "[]",
    volume_data: str = "[]",
    isosurface_values: str = "[]",
    velocity_grid: str = "[]",
    source_pos: str = "[5, 5]",
    receivers: str = "[]",
    duration: float = 0.5,
    nz: int = 100,
    nx: int = 100,
    plot_type: str = "line",
    levels: int = 20,
    cmap: str = "RdBu_r",
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
    settings: "Settings | None" = None,
) -> str:
    """Dispatch geo-math action to the appropriate computation function."""
    action = action.strip().lower()

    def _parse(s: str) -> list:
        try:
            return json.loads(s) if s.strip() else []
        except Exception:
            return []

    if action == "solve_symbolic":
        return _solve_symbolic(expression, variable, domain)

    if action == "wolfram":
        if settings is None:
            return "⚠ settings gerekli."
        return _wolfram(query or expression, settings)

    if action == "wave_simulate_2d":
        vg = _parse(velocity_grid)
        sp_ = _parse(source_pos)
        rec = _parse(receivers)
        return _wave_simulate_2d(vg or None, sp_ or [5, 5], rec or None, duration, nz, nx)

    if action == "plot_2d":
        return _plot_2d(_parse(x_data), _parse(y_data), plot_type, title, xlabel, ylabel)

    if action == "plot_contour":
        return _plot_contour(_parse(grid_data), levels, cmap, title, xlabel, ylabel)

    if action == "plot_3d_surface":
        return _plot_3d_surface(_parse(grid_data), title)

    if action == "plot_volume":
        return _plot_volume(_parse(volume_data), _parse(isosurface_values) or None, title)

    return (
        f"⚠ Bilinmeyen action: '{action}'. "
        "Geçerli: solve_symbolic, wolfram, wave_simulate_2d, "
        "plot_2d, plot_contour, plot_3d_surface, plot_volume"
    )
