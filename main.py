import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from matplotlib.animation import FuncAnimation
import mesa  # Mesa 3.x

ROAD, BLOCK = 0, 1

GREY, GREEN, RED = "#888888", "#2ca02c", "#d62728"

# Zonas: cor no mapa e pesos de procura (placeholders, afinamos depois)
# generation = quão frequentemente se começam viagens aqui
# attraction = quão frequentemente as viagens acabam aqui
ZONES = {
    "centro":      {"color": "#f4c76b", "generation": 1.0, "attraction": 3.0},
    "residencial": {"color": "#a8d5a2", "generation": 2.0, "attraction": 0.8},
}

def demand_factor(hour):
    """Multiplicador da taxa de chegada ao longo do dia."""
    if 7 <= hour <= 9 or 17 <= hour <= 19:
        return 1.0      # horas de ponta
    if 10 <= hour <= 16:
        return 0.5
    if 20 <= hour <= 22:
        return 0.4
    return 0.1          # noite


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

ZONE_NAMES = list(ZONES)          # índice da zona = posição nesta lista
ROAD_ZONE = -1                    # valor de zone_map nas estradas


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


class BikeStation(mesa.Agent):
    """Estação de bicicletas fixa numa célula de estrada."""

    def __init__(self, model, xy, capacity, bikes, zone):
        super().__init__(model)
        self.xy = xy
        self.zone = zone           # nome da zona onde está a estação
        self.capacity = capacity   # nº total de docas
        self.bikes = bikes         # bicicletas atualmente na estação

    @property
    def free_docks(self):
        return self.capacity - self.bikes

    @property
    def fill_ratio(self):
        return self.bikes / self.capacity

    def take_bike(self):
        """True se havia bicicleta (senão: procura não satisfeita)."""
        if self.bikes > 0:
            self.bikes -= 1
            return True
        return False

    def return_bike(self):
        """True se havia doca livre (senão: estação cheia)."""
        if self.free_docks > 0:
            self.bikes += 1
            return True
        return False

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
    def spawn_users(self):
        hour = (self.steps // 60) % 24
        n = self.rng.poisson(self.arrival_rate * demand_factor(hour))

        if 7 <= hour <= 9:
            o_key, d_key = "generation", "attraction"   # casa -> centro
        elif 17 <= hour <= 19:
            o_key, d_key = "attraction", "generation"   # centro -> casa
        else:
            o_key, d_key = "generation", "attraction"

        for _ in range(n):
            origin, o_zone = self.pick_cell(o_key)
            dest, _ = self.pick_cell(d_key)
            self.requests[o_zone] += 1

            o_st = self.nearest_station(
                origin, lambda s: s.bikes > 0
                and manhattan(origin, s.xy) <= self.max_walk)
            d_st = self.nearest_station(dest)
            if o_st is None:               # longe de estações ou sem bicicletas
                self.unmet += 1
                self.unmet_by_zone[o_zone] += 1
                self.add_flash(origin, RED)
                continue
            if o_st is d_st:               # origem e destino na mesma estação
                continue

            self.walk_minutes += self.walk_time(origin, o_st.xy)
            User(self, origin, dest, o_st, d_st, o_zone)


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
        self.agents_by_type[User].do("step") if User in self.agents_by_type else None
        self.datacollector.collect(self)
    # ---------- desenho ----------
    def draw(self, ax=None):
        if ax is None:
            _, ax = plt.subplots(figsize=(9, 6))

        # 0 = estrada, 1..n = zonas
        colors = ["#d9d9d9"] + [ZONES[z]["color"] for z in ZONE_NAMES]
        ax.imshow(self.zone_map + 1, cmap=ListedColormap(colors),
                  vmin=0, vmax=len(ZONE_NAMES), origin="lower")

        users = [u for u in self.agents if isinstance(u, User)]
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

class User(mesa.Agent):
    """Uma viagem: caminha -> pedala -> caminha."""

    def __init__(self, model, origin, dest, o_station, d_station, o_zone):
        super().__init__(model)
        self.dest = dest
        self.o_station = o_station
        self.d_station = d_station
        self.o_zone = o_zone
        self.state = "walking_to_start"
        self.start_leg(origin, o_station.xy, model.walk_time(origin, o_station.xy))

    def start_leg(self, a, b, minutes):
        """Começa um troço de a para b que demora 'minutes'."""
        self.leg_from, self.leg_to = a, b
        self.timer = self.leg_total = max(1, minutes)

    @property
    def xy(self):
        """Posição interpolada ao longo do troço atual."""
        f = min(max(1 - self.timer / self.leg_total, 0), 1)
        return (self.leg_from[0] + (self.leg_to[0] - self.leg_from[0]) * f,
                self.leg_from[1] + (self.leg_to[1] - self.leg_from[1]) * f)

    @property
    def color(self):
        return GREEN if self.state == "riding" else GREY

    def step(self):
        self.timer -= 1
        if self.timer > 0:
            return
        m = self.model

        if self.state == "walking_to_start":
            if self.o_station.take_bike():
                self.state = "riding"
                self.start_leg(self.o_station.xy, self.d_station.xy,
                               m.ride_time(self.o_station.xy, self.d_station.xy))
            else:                      # alguém levou a última bicicleta
                m.unmet += 1
                m.unmet_by_zone[self.o_zone] += 1
                m.add_flash(self.o_station.xy, RED)
                self.remove()

        elif self.state == "riding":
            if self.d_station.return_bike():
                self.finish(self.d_station)
            else:                      # estação cheia: procura outra
                m.redirects += 1
                alt = m.nearest_station(self.d_station.xy,
                                        lambda s: s.free_docks > 0)
                if alt is None:
                    m.failed_returns += 1
                    m.add_flash(self.d_station.xy, RED)
                    self.remove()
                else:
                    self.start_leg(self.d_station.xy, alt.xy,
                                   m.ride_time(self.d_station.xy, alt.xy))
                    self.d_station = alt

        elif self.state == "walking_to_dest":
            m.served += 1
            m.add_flash(self.dest, GREEN)
            self.remove()

    def finish(self, station):
        self.state = "walking_to_dest"
        self.start_leg(station.xy, self.dest,
                       self.model.walk_time(station.xy, self.dest))
        self.model.walk_minutes += self.leg_total

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