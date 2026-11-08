"""
main.py - Connection between all .py files, where the modelling 
            of the agents behaviour and visualization of the simulation takes place.
"""

import time
from typing import Optional
import solara
import mesa
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from model import BikeSharingModel

from space import (
    ZONE_CENTRAL,
    ZONE_UNIVERSITY,
    ZONE_RESIDENTIAL,
    ZONE_COLORS,
    COLOR_ROAD,
)

@solara.component
def MapView(model, tick: int = 0):
    """Renders the Map."""
    grid = model.grid
    fig = Figure(figsize=(7.8, 6.5), dpi=100)
    ax = fig.subplots()

    # Vectorized grid rendering
    ax.imshow(
        grid.rgb_matrix,
        origin="lower",
        extent=(-0.5, grid.width - 0.5, -0.5, grid.height - 0.5),
        interpolation="nearest",
        zorder=1,
    )

    # Road intersections
    ix_coords = [pos[0] for pos in grid.intersection_cells]
    iy_coords = [pos[1] for pos in grid.intersection_cells]
    ax.scatter(ix_coords, iy_coords, s=14, c="#757575", marker="o", zorder=3)
    # Depot
    ax.scatter([16], [16], s=90, c="#d9534f", marker="s", edgecolor="black", linewidth=1.2, zorder=5)

    ax.set_xlim(-0.5, grid.width - 0.5)
    ax.set_ylim(-0.5, grid.height - 0.5)
    ax.set_aspect("equal")
    ax.set_xticks(range(0, grid.width, 4))
    ax.set_yticks(range(0, grid.height, 4))
    ax.tick_params(axis="both", which="major", labelsize=8)
    ax.grid(color="#999999", linestyle=":", linewidth=0.5, alpha=0.35)

    legend_elements = [
        Patch(facecolor=ZONE_COLORS[ZONE_CENTRAL], edgecolor="grey", label="Central"),
        Patch(facecolor=ZONE_COLORS[ZONE_UNIVERSITY], edgecolor="grey", label="University"),
        Patch(facecolor=ZONE_COLORS[ZONE_RESIDENTIAL], edgecolor="grey", label="Residential"),
        Patch(facecolor=COLOR_ROAD, edgecolor="grey", label="Roads"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#757575", markersize=4, label=f"Intersections"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#d9534f", markeredgecolor="black", markersize=6, label="Depot"),
    ]
    ax.legend(
        handles=legend_elements,
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        frameon=True,
        fontsize=7.5,
        borderaxespad=0.2,
        labelspacing=0.25,
        handletextpad=0.3,
        handlelength=1.0,
    )
    fig.tight_layout(pad=0.3)

    with solara.Card(margin=0, elevation=1):
        solara.FigureMatplotlib(fig)


@solara.component
def SolaraViz(
    model: mesa.Model,
    components: list,
    model_params: Optional[dict] = None,
    name: str = "Porto Bike Sharing Dashboard",
    play_interval: int = 20,
):
    """Solara dashboard to visualize the simulation."""
    model_ref = solara.use_reactive(model)
    step_tick = solara.use_reactive(0)
    playing = solara.use_reactive(False)
    interval = solara.use_reactive(play_interval)
    seed_val = solara.use_reactive(42)

    def simulation_loop():
        while playing.value and model_ref.value.running:
            iv = interval.value
            if iv < 25:
                n_steps = max(1, int(30 / max(1, iv)))
                for _ in range(n_steps):
                    if model_ref.value.running:
                        model_ref.value.step()
                time.sleep(0.025)
            else:
                time.sleep(iv / 1000.0)
                if model_ref.value.running:
                    model_ref.value.step()

            step_tick.value = model_ref.value.current_tick

    solara.lab.use_task(simulation_loop, dependencies=[playing.value])

    def do_step():
        if model_ref.value.running:
            model_ref.value.step()
            step_tick.value = model_ref.value.current_tick

    def do_reset():
        playing.value = False
        new_model = model_ref.value.__class__(seed=seed_val.value)
        model_ref.set(new_model)
        step_tick.value = 0

    def toggle_play():
        playing.value = not playing.value

    with solara.AppBar():
        solara.AppBarTitle(name)
        solara.lab.ThemeToggle()

    with solara.Sidebar():
        with solara.Card("Controls", margin=1):
            solara.SliderInt(label="Play Interval (ms)", value=interval, min=1, max=250, step=1)
            with solara.Row(justify="space-between"):
                solara.Button(label="Reset", color="primary", on_click=do_reset)
                solara.Button(
                    label="▶" if not playing.value else "❚❚",
                    color="primary",
                    on_click=toggle_play,
                    disabled=not model_ref.value.running,
                )
                solara.Button(
                    label="Step",
                    color="primary",
                    on_click=do_step,
                    disabled=playing.value or not model_ref.value.running,
                )

        with solara.Card("Model Parameters", margin=1):
            solara.SliderInt(
                label="Random Seed",
                value=seed_val,
                min=1,
                max=100,
                step=1,
                on_value=lambda v: seed_val.set(v),
            )

        with solara.Card("Information", margin=1):
            cur_tick = step_tick.value
            hour = 7 + cur_tick // 60
            minute = cur_tick % 60
            solara.Markdown(f"**Step / Minute:** {cur_tick} / {model_ref.value.shift_ticks}")
            solara.Markdown(f"**Simulated Clock:** {hour:02d}:{minute:02d}")
            solara.Markdown(f"**Status:** {'Running' if model_ref.value.running else 'Shift Finished (19:00)'}")

    with solara.Column(style="padding: 6px 16px; width: 100%; align-items: center; justify-content: flex-start;"):
        for comp in components:
            comp_func = comp[0] if isinstance(comp, tuple) else comp
            comp_func(model_ref.value, tick=step_tick.value)


model_params = {
    "seed": {
        "type": "SliderInt",
        "value": 42,
        "label": "Random Seed",
        "min": 1,
        "max": 100,
        "step": 1,
    }
}

model = BikeSharingModel()

page = SolaraViz(
    model=model,
    components=[MapView],
    model_params=model_params,
    name="Porto Bike Sharing Dashboard",
)
