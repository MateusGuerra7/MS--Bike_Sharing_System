# Proposta para a task de separar o projeto em ficheiros:
    # /docs: markdowns com as guidelines
    # space.py: define a grid, estradas e zonas, e também as funções de movimento tipo pathfinding entre origin e destination
    # agents.py: definir as várias entities
    # main.py: interface entre o space.py e o agents.py que depois chama a dashboard final como output

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from matplotlib.animation import FuncAnimation
import mesa  # Mesa 3.x

def demand_factor(hour):
    """Multiplicador da taxa de chegada ao longo do dia."""
    if 7 <= hour <= 9 or 17 <= hour <= 19:
        return 1.0      # horas de ponta
    if 10 <= hour <= 16:
        return 0.5
    if 20 <= hour <= 22:
        return 0.4
    return 0.1          # noite

def build_city(n_blocks_x=5, n_blocks_y=4, block_size=6, road_width=2):
    """Devolve a matriz ROAD/BLOCK e a lista de quarteirões (i, j, x0, y0)."""
    width = n_blocks_x * (block_size + road_width) + road_width
    height = n_blocks_y * (block_size + road_width) + road_width
    city = np.full((height, width), ROAD, dtype=int)

    blocks = []
    for i in range(n_blocks_x):
        x0 = road_width + i * (block_size + road_width)
        for j in range(n_blocks_y):
            y0 = road_width + j * (block_size + road_width)
            city[y0:y0 + block_size, x0:x0 + block_size] = BLOCK
            blocks.append((i, j, x0, y0))
    return city, blocks
class BikeSharingModel(mesa.Model):
    def __init__(self, n_blocks_x=7, n_blocks_y=6, block_size=5,
                 road_width=2, n_stations=7, min_dist=10,
                 capacity_range=(10, 20), initial_fill=0.5,
                 centro_radius=1.2, centro_jitter=0.7,arrival_rate=1.5, max_walk=10,  seed=None):
        super().__init__(seed=seed)
        self.n_blocks_x, self.n_blocks_y = n_blocks_x, n_blocks_y
        self.block_size = block_size
        self.city, self.blocks = build_city(n_blocks_x, n_blocks_y,
                                            block_size, road_width)
        self.height, self.width = self.city.shape
        self.min_dist = min_dist
        self.capacity_range = capacity_range
        self.initial_fill = initial_fill
        self.centro_radius = centro_radius
        self.centro_jitter = centro_jitter
        self.zone_map = self.assign_zones()
        self.stations = []
        self.zone_map = self.assign_zones()

        # parâmetros dos utilizadores
        self.arrival_rate = arrival_rate      # viagens/min em hora de ponta
        self.max_walk = max_walk              # células (10 ≈ 500 m)
        self.walk_speed = 1.7                 # células/min (~5 km/h)
        self.bike_speed = 5.0                 # células/min (~15 km/h)
        self.rng = np.random.default_rng(self.random.randint(0, 2**32 - 1))

        # células de cada zona (para sortear origens e destinos)
        self.zone_cells = {}
        for idx, name in enumerate(ZONE_NAMES):
            ys, xs = np.where(self.zone_map == idx)
            self.zone_cells[name] = list(zip(xs.tolist(), ys.tolist()))

        # contadores
        self.served = self.unmet = self.redirects = 0
        self.failed_returns = 0
        self.walk_minutes = 0
        self.requests = {z: 0 for z in ZONE_NAMES}
        self.unmet_by_zone = {z: 0 for z in ZONE_NAMES}
        self.flashes = []   # [x, y, cor, minutos_restantes]

        self.place_stations(n_stations)

        self.datacollector = mesa.DataCollector(model_reporters={
            "hora": lambda m: (m.steps // 60) % 24,
            "servidos": "served",
            "nao_satisfeitos": "unmet",
            "estacoes_vazias": lambda m: sum(s.bikes == 0 for s in m.stations),
            "estacoes_cheias": lambda m: sum(s.free_docks == 0 for s in m.stations),
            "bikes_no_sistema": lambda m: sum(s.bikes for s in m.stations),
            " flashes": lambda m: len(m.flashes),
        })

    # ---------- zonas ----------
    def assign_zones(self):
        """Centro irregular: distância ao meio + ruído aleatório."""
        cx = (self.n_blocks_x - 1) / 2
        cy = (self.n_blocks_y - 1) / 2

        zone_of = {}
        for (i, j, _, _) in self.blocks:
            noise = self.random.uniform(-self.centro_jitter, self.centro_jitter)
            dist = np.hypot(i - cx, j - cy) + noise
            if dist <= self.centro_radius:
                zone_of[(i, j)] = ZONE_NAMES.index("centro")
            else:
                zone_of[(i, j)] = ZONE_NAMES.index("residencial")

        zone_map = np.full(self.city.shape, ROAD_ZONE, dtype=int)
        for (i, j, x0, y0) in self.blocks:
            zone_map[y0:y0 + self.block_size,
                     x0:x0 + self.block_size] = zone_of[(i, j)]
        return zone_map

    def zone_at(self, x, y):
        """Zona de uma célula de estrada = zona do quarteirão vizinho."""
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (0 <= nx < self.width and 0 <= ny < self.height
                    and self.zone_map[ny, nx] != ROAD_ZONE):
                return ZONE_NAMES[self.zone_map[ny, nx]]
        return None

    # ---------- colocação das estações ----------
    def candidate_cells(self):
        """Células de estrada que tocam (4-vizinhança) num quarteirão."""
        cells = []
        for y in range(self.height):
            for x in range(self.width):
                if self.city[y, x] != ROAD:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if (0 <= nx < self.width and 0 <= ny < self.height
                            and self.city[ny, nx] == BLOCK):
                        cells.append((x, y))
                        break
        return cells

    def place_stations(self, n_stations):
        candidates = self.candidate_cells()
        self.random.shuffle(candidates)

        chosen = []
        for (x, y) in candidates:
            if len(chosen) == n_stations:
                break
            if all(abs(x - cx) + abs(y - cy) >= self.min_dist
                   for cx, cy in chosen):
                chosen.append((x, y))

        for (x, y) in chosen:
            capacity = self.random.randint(*self.capacity_range)
            bikes = round(capacity * self.initial_fill)
            self.stations.append(
                BikeStation(self, (x, y), capacity, bikes, self.zone_at(x, y)))
        # ---------- utilitários ----------
    def walk_time(self, a, b):
        return int(np.ceil(manhattan(a, b) / self.walk_speed))

    def ride_time(self, a, b):
        return max(1, int(np.ceil(manhattan(a, b) / self.bike_speed)))

    def nearest_station(self, xy, condition=lambda s: True):
        cands = [s for s in self.stations if condition(s)]
        return min(cands, key=lambda s: manhattan(xy, s.xy)) if cands else None

    def pick_cell(self, key):
        """Sorteia uma célula; a zona pesa pelo peso 'key' x nº de células."""
        weights = [ZONES[z][key] * len(self.zone_cells[z]) for z in ZONE_NAMES]
        zone = self.random.choices(ZONE_NAMES, weights=weights)[0]
        return self.random.choice(self.zone_cells[zone]), zone

    # ---------- procura ----------
    def add_flash(self, xy, color, ttl=5):
        """Ponto que fica visível 'ttl' minutos (vermelho = falhou, verde = concluído)."""
        self.flashes.append([xy[0], xy[1], color, ttl])

    def tick_flashes(self):
        for f in self.flashes:
            f[3] -= 1
        self.flashes = [f for f in self.flashes if f[3] > 0]
        
    def step(self):
        self.tick_flashes()
        self.spawn_users()
        self.agents_by_type[Person].do("step") if Person in self.agents_by_type else None
        self.datacollector.collect(self)
    # ---------- desenho ----------
    def draw(self, ax=None):
        if ax is None:
            _, ax = plt.subplots(figsize=(9, 6))

        # 0 = estrada, 1..n = zonas
        colors = ["#d9d9d9"] + [ZONES[z]["color"] for z in ZONE_NAMES]
        ax.imshow(self.zone_map + 1, cmap=ListedColormap(colors),
                  vmin=0, vmax=len(ZONE_NAMES), origin="lower")

        users = [u for u in self.agents if isinstance(u, Person)]
        if users:
            pos = [u.xy for u in users]
            ax.scatter([p[0] for p in pos], [p[1] for p in pos], s=22,
                       c=[u.color for u in users],
                       edgecolor="white", linewidth=0.5, zorder=5)
        if self.flashes:
            ax.scatter([f[0] for f in self.flashes],
                       [f[1] for f in self.flashes], s=45,
                       c=[f[2] for f in self.flashes],
                       edgecolor="black", linewidth=0.6, zorder=6)
            
        for st in self.stations:
            x, y = st.xy
            ax.scatter(x, y, s=60 + 25 * st.capacity, marker="o",
                       c=[st.fill_ratio], cmap="RdYlGn", vmin=0, vmax=1,
                       edgecolor="black", zorder=3)
            ax.text(x, y, f"{st.bikes}/{st.capacity}", ha="center",
                    va="center", fontsize=7, zorder=4)

        legend = [Patch(facecolor=ZONES[z]["color"], edgecolor="gray", label=z)
                  for z in ZONE_NAMES]
        legend += [Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                          markeredgecolor="black", markersize=7, label=l)
                   for c, l in ((GREY, "a caminhar"), (GREEN, "de bicicleta / concluído"),
                                (RED, "não satisfeito"))]
        ax.legend(handles=legend, loc="upper left",
                  bbox_to_anchor=(1.01, 1), title="Legenda")
        ax.set_title(f"Mapa da cidade ({len(self.agents)} estações)")
        ax.set_xticks([]); ax.set_yticks([])
        return ax


def animate(model, minutes_per_frame=1, interval_ms=100):
    fig, ax = plt.subplots(figsize=(11, 6))

    def update(frame):
        for _ in range(minutes_per_frame):
            model.step()
        ax.clear()
        model.draw(ax)
        h, m = (model.steps // 60) % 24, model.steps % 60
        ax.set_title(f"{h:02d}:{m:02d} | servidos: {model.served} | "
                     f"não satisfeitos: {model.unmet}")

    anim = FuncAnimation(fig, update, frames=24 * 60 // minutes_per_frame,
                         interval=interval_ms, repeat=False)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    model = BikeSharingModel(seed=178)
    animate(model, minutes_per_frame=1, interval_ms=100)