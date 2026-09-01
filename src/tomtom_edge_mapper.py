#!/usr/bin/env python3
"""
Etapa 1 - Calibração Macroscópica
---------------------------------
Mapeia as rotas TomTom completas para as edges da rede SUMO.

O GeoJSON TomTom utilizado possui Features no formato:

    route_id
    lengthInMeters
    travelTimeInSeconds
    geometry = LineString

Exemplo:

    route_id = highway_downtown

O script:
    1. identifica cenário e direção através de route_id;
    2. carrega a rede SUMO correspondente;
    3. converte a geometria TomTom WGS84 para XY da rede SUMO;
    4. amostra a geometria da rota;
    5. encontra as edges SUMO correspondentes;
    6. preserva a ordem das edges;
    7. verifica origem/destino;
    8. calcula velocidade observada;
    9. gera CSV e JSON para a próxima etapa.

Entrada:
    conf/routes.json
    output/od_mapping.json
    roadNetwork/*.net.xml
    arquivo TomTom GeoJSON / GeoJSON.GZ / ZIP

Saída:
    output/tomtom_route_mapping.csv
    output/tomtom_route_mapping.json

Dependências:
    pip install pandas numpy shapely sumolib
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import sumolib
from shapely.geometry import LineString, shape
from shapely.ops import transform


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TOMTOM = PROJECT_ROOT / "traffic_stats_area_full.geojson"
DEFAULT_OD_MAPPING = PROJECT_ROOT / "output" / "od_mapping.json"
DEFAULT_NETWORK_DIR = PROJECT_ROOT / "roadNetwork"

DEFAULT_OUTPUT_CSV = (
    PROJECT_ROOT / "output" / "tomtom_route_mapping.csv"
)

DEFAULT_OUTPUT_JSON = (
    PROJECT_ROOT / "output" / "tomtom_route_mapping.json"
)

DEFAULT_SEARCH_RADIUS_M = 50.0
DEFAULT_SAMPLE_SPACING_M = 20.0

NETWORK_FILES = {
    "highway": "highway.net.xml",
    "urban_road": "urbanRoad.net.xml",
    "intersection": "intersection.net.xml",
}

DIRECTION_NAMES = {
    "downtown": "center_to_neighborhood",
    "neighborhood": "neighborhood_to_center",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def load_geojson(path: Path) -> dict[str, Any]:
    """
    Carrega:
        .geojson
        .json
        .geojson.gz
        .gz
        .zip
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Arquivo TomTom não encontrado: {path}"
        )

    suffixes = "".join(
        path.suffixes
    ).lower()

    if (
        suffixes.endswith(".geojson.gz")
        or path.suffix.lower() == ".gz"
    ):
        with gzip.open(
            path,
            "rt",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(
            path,
            "r",
        ) as z:

            files = [
                name
                for name in z.namelist()
                if name.lower().endswith(
                    (
                        ".geojson",
                        ".json",
                        ".geojson.gz",
                    )
                )
            ]

            if not files:
                raise RuntimeError(
                    "Nenhum GeoJSON encontrado dentro do ZIP."
                )

            name = files[0]

            with z.open(name) as f:
                data = f.read()

            if name.lower().endswith(".gz"):
                data = gzip.decompress(data)

            return json.loads(
                data.decode("utf-8")
            )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def load_od_mapping(
    path: Path,
) -> dict[tuple[str, str | None, str], dict[str, Any]]:
    """
    Indexa o od_mapping.json por:

        scenario
        corridor
        direction_key
    """
    data = load_json(path)

    index = {}

    for route in data.get(
        "routes",
        [],
    ):

        if "error" in route:
            continue

        if not route.get(
            "route_exists",
            False,
        ):
            continue

        key = (
            route.get("scenario"),
            route.get("corridor"),
            route.get("direction_key"),
        )

        index[key] = route

    return index


def parse_route_id(
    route_id: str,
) -> tuple[str, str | None, str]:
    """
    Converte route_id em:

        scenario
        corridor
        direction_key

    Exemplos:

        highway_downtown
        -> highway, None, downtown

        urban_road_neighborhood
        -> urban_road, None, neighborhood

        intersection_main_street_downtown
        -> intersection, main_street, downtown

        intersection_cross_street_neighborhood
        -> intersection, cross_street, neighborhood
    """
    parts = route_id.split("_")

    if len(parts) < 2:
        raise ValueError(
            f"route_id inválido: {route_id}"
        )

    if parts[0] != "intersection":
        scenario = parts[0]
        if len(parts) > 2:
            scenario += '_' + parts[1]
        direction_key = parts[-1]

        if scenario not in NETWORK_FILES:
            raise ValueError(
                f"Cenário desconhecido: {scenario}"
            )

        if direction_key not in DIRECTION_NAMES:
            raise ValueError(
                f"Direção desconhecida: {direction_key}"
            )

        return (
            scenario,
            None,
            direction_key,
        )

    if len(parts) < 3:
        raise ValueError(
            f"route_id de intersection inválido: {route_id}"
        )

    scenario = "intersection"
    direction_key = parts[-1]
    corridor = "_".join(
        parts[1:-1]
    )

    if corridor not in (
        "main_street",
        "cross_street",
    ):
        raise ValueError(
            f"Corredor desconhecido: {corridor}"
        )

    if direction_key not in DIRECTION_NAMES:
        raise ValueError(
            f"Direção desconhecida: {direction_key}"
        )

    return (
        scenario,
        corridor,
        direction_key,
    )


def geometry_to_linestring(
    geometry: dict[str, Any],
) -> LineString:
    geom = shape(geometry)

    if geom.is_empty:
        raise ValueError(
            "Geometria vazia."
        )

    if geom.geom_type == "LineString":
        return geom

    if geom.geom_type == "MultiLineString":
        lines = list(
            geom.geoms
        )

        if not lines:
            raise ValueError(
                "MultiLineString vazio."
            )

        return max(
            lines,
            key=lambda line: line.length,
        )

    raise ValueError(
        f"Geometria não suportada: {geom.geom_type}"
    )


def convert_line_to_sumo(
    net: Any,
    line: LineString,
) -> LineString:
    """
    Converte:

        WGS84 (lon, lat)

    para:

        coordenadas XY da rede SUMO.
    """
    def convert(
        x: float,
        y: float,
        z: Any = None,
    ):
        return net.convertLonLat2XY(
            x,
            y,
        )

    return transform(
        convert,
        line,
    )


def sample_line(
    line: LineString,
    spacing_m: float,
) -> list[tuple[float, float]]:
    """
    Amostra a LineString aproximadamente a cada spacing_m.
    """
    if line.length <= 0:
        return []

    distances = []

    d = 0.0

    while d < line.length:
        distances.append(d)
        d += spacing_m

    distances.append(
        line.length
    )

    points = []

    for distance in distances:
        point = line.interpolate(
            distance
        )

        points.append(
            (
                point.x,
                point.y,
            )
        )

    return points


def edge_is_usable(
    edge: Any,
) -> bool:
    try:
        if edge.getFunction() == "internal":
            return False
    except AttributeError:
        pass

    try:
        if len(edge.getLanes()) == 0:
            return False
    except AttributeError:
        return False

    return True


def find_nearest_edge(
    net: Any,
    x: float,
    y: float,
    radius: float,
) -> tuple[Any, float] | None:
    """
    Procura a edge SUMO mais próxima do ponto.
    """
    candidates = net.getNeighboringEdges(
        x,
        y,
        radius,
    )

    candidates = [
        (edge, float(distance))
        for edge, distance in candidates
        if edge_is_usable(edge)
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[1]
    )

    return candidates[0]


def map_geometry_to_edges(
    net: Any,
    line: LineString,
    search_radius: float,
    sample_spacing: float,
) -> tuple[list[Any], list[float]]:
    """
    Mapeia toda a geometria TomTom para uma sequência ordenada
    de edges SUMO.

    O ponto médio não é utilizado como representação da rota.
    """
    points = sample_line(
        line,
        sample_spacing,
    )

    mapped_edges = []
    distances = []

    for x, y in points:

        result = find_nearest_edge(
            net,
            x,
            y,
            search_radius,
        )

        if result is None:
            continue

        edge, distance = result

        mapped_edges.append(
            edge
        )

        distances.append(
            distance
        )

    if not mapped_edges:
        raise RuntimeError(
            "Nenhuma edge SUMO encontrada "
            "ao longo da geometria TomTom."
        )

    # Remove edges consecutivas repetidas.
    unique_edges = []

    for edge in mapped_edges:

        if (
            not unique_edges
            or edge.getID()
            != unique_edges[-1].getID()
        ):
            unique_edges.append(
                edge
            )

    return (
        unique_edges,
        distances,
    )


def orient_edges(
    edges: list[Any],
) -> list[Any]:
    """
    Tenta garantir que a sequência das edges respeita
    a conectividade topológica.

    A geometria TomTom normalmente já fornece a ordem.
    Aqui apenas removemos inconsistências simples.
    """
    if len(edges) <= 1:
        return edges

    result = [
        edges[0]
    ]

    for edge in edges[1:]:

        previous = result[-1]

        if (
            edge.getFromNode()
            == previous.getToNode()
            or edge.getID()
            == previous.getID()
        ):
            result.append(
                edge
            )
            continue

        # Caso o ponto amostrado tenha saltado uma edge,
        # procuramos entre as edges adjacentes.
        connected = False

        for candidate in previous.getToNode().getOutgoing():

            if candidate == edge:
                result.append(
                    edge
                )
                connected = True
                break

        if not connected:
            result.append(
                edge
            )

    return result


def calculate_speed(
    length_m: Any,
    travel_time_s: Any,
) -> tuple[float | None, float | None]:
    """
    Retorna:
        speed_mps
        speed_kmh
    """
    try:
        length = float(
            length_m
        )

        travel_time = float(
            travel_time_s
        )

        if (
            length <= 0
            or travel_time <= 0
        ):
            return None, None

        speed_mps = (
            length
            / travel_time
        )

        speed_kmh = (
            speed_mps
            * 3.6
        )

        return (
            speed_mps,
            speed_kmh,
        )

    except (
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None, None


def validate_od(
    edges: list[Any],
    od_record: dict[str, Any],
) -> dict[str, Any]:
    """
    Compara as primeiras/últimas edges encontradas com o O/D
    previamente determinado.
    """
    origin_expected = od_record.get(
        "origin",
        {},
    ).get(
        "edge_id"
    )

    destination_expected = od_record.get(
        "destination",
        {},
    ).get(
        "edge_id"
    )

    edge_ids = [
        edge.getID()
        for edge in edges
    ]

    origin_match = (
        origin_expected in edge_ids
        if origin_expected
        else False
    )

    destination_match = (
        destination_expected in edge_ids
        if destination_expected
        else False
    )

    return {
        "expected_origin_edge": origin_expected,
        "expected_destination_edge": destination_expected,
        "mapped_first_edge": (
            edge_ids[0]
            if edge_ids
            else None
        ),
        "mapped_last_edge": (
            edge_ids[-1]
            if edge_ids
            else None
        ),
        "origin_edge_in_route": origin_match,
        "destination_edge_in_route": destination_match,
    }


def process_feature(
    feature: dict[str, Any],
    networks: dict[str, Any],
    od_index: dict[
        tuple[str, str | None, str],
        dict[str, Any],
    ],
    search_radius: float,
    sample_spacing: float,
) -> dict[str, Any]:

    properties = feature.get(
        "properties",
        {},
    )

    route_id = properties.get(
        "route_id"
    )

    if not route_id:
        raise ValueError(
            "Feature sem route_id."
        )

    (
        scenario,
        corridor,
        direction_key,
    ) = parse_route_id(
        str(route_id)
    )

    direction = DIRECTION_NAMES[
        direction_key
    ]

    network = networks[
        scenario
    ]

    od_key = (
        scenario,
        corridor,
        direction_key,
    )

    if od_key not in od_index:
        raise RuntimeError(
            f"O/D não encontrado para {od_key}"
        )

    od_record = od_index[
        od_key
    ]

    line_wgs84 = geometry_to_linestring(
        feature.get(
            "geometry"
        )
    )

    line_sumo = convert_line_to_sumo(
        network,
        line_wgs84,
    )

    edges, distances = map_geometry_to_edges(
        network,
        line_sumo,
        search_radius,
        sample_spacing,
    )

    edges = orient_edges(
        edges
    )

    length_m = properties.get(
        "lengthInMeters"
    )

    travel_time_s = properties.get(
        "travelTimeInSeconds"
    )

    speed_mps, speed_kmh = calculate_speed(
        length_m,
        travel_time_s,
    )

    od_validation = validate_od(
        edges,
        od_record,
    )

    edge_ids = [
        edge.getID()
        for edge in edges
    ]

    return {
        "route_id": route_id,
        "scenario": scenario,
        "corridor": corridor,
        "direction_key": direction_key,
        "direction": direction,

        "origin_edge": od_record[
            "origin"
        ]["edge_id"],

        "destination_edge": od_record[
            "destination"
        ]["edge_id"],

        "mapped_first_edge": (
            edge_ids[0]
            if edge_ids
            else None
        ),

        "mapped_last_edge": (
            edge_ids[-1]
            if edge_ids
            else None
        ),

        "num_sumo_edges": len(
            edge_ids
        ),

        "sumo_edges": "|".join(
            edge_ids
        ),

        "length_m": length_m,

        "travel_time_s": travel_time_s,

        "speed_mps": speed_mps,

        "speed_kmh": speed_kmh,

        "mean_map_distance_m": (
            sum(distances)
            / len(distances)
            if distances
            else None
        ),

        "max_map_distance_m": (
            max(distances)
            if distances
            else None
        ),

        "origin_edge_in_route": od_validation[
            "origin_edge_in_route"
        ],

        "destination_edge_in_route": od_validation[
            "destination_edge_in_route"
        ],

        "route_geometry_length_sumo_m": (
            line_sumo.length
        ),
    }


def save_results(
    records: list[dict[str, Any]],
    csv_path: Path,
    json_path: Path,
) -> None:

    csv_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    df = pd.DataFrame(
        records
    )

    df.to_csv(
        csv_path,
        index=False,
    )

    with json_path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            records,
            f,
            indent=4,
            ensure_ascii=False,
            default=str,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mapeia rotas TomTom completas para edges SUMO."
        )
    )

    parser.add_argument(
        "--tomtom",
        type=Path,
        default=DEFAULT_TOMTOM,
    )

    parser.add_argument(
        "--od-mapping",
        type=Path,
        default=DEFAULT_OD_MAPPING,
    )

    parser.add_argument(
        "--network-dir",
        type=Path,
        default=DEFAULT_NETWORK_DIR,
    )

    parser.add_argument(
        "--output-csv",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
    )

    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
    )

    parser.add_argument(
        "--search-radius",
        type=float,
        default=DEFAULT_SEARCH_RADIUS_M,
        help="Raio máximo para encontrar uma edge SUMO.",
    )

    parser.add_argument(
        "--sample-spacing",
        type=float,
        default=DEFAULT_SAMPLE_SPACING_M,
        help="Espaçamento da amostragem da rota em metros.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 80)
    print("ETAPA 1 - TOMTOM ROUTE → SUMO")
    print("=" * 80)

    try:
        od_index = load_od_mapping(
            args.od_mapping
        )

        print(
            f"[INFO] O/D carregados: "
            f"{len(od_index)}"
        )

        tomtom = load_geojson(
            args.tomtom
        )

        features = tomtom.get(
            "features",
            []
        )

        if not features:
            raise RuntimeError(
                "Nenhuma Feature encontrada no GeoJSON."
            )

        print(
            f"[INFO] Features TomTom: "
            f"{len(features)}"
        )

        networks = {}

        for scenario, filename in NETWORK_FILES.items():

            path = (
                args.network_dir
                / filename
            )

            if not path.exists():
                print(
                    f"[WARNING] Rede não encontrada: "
                    f"{path}"
                )
                continue

            print(
                f"[INFO] Carregando rede: "
                f"{path}"
            )

            networks[scenario] = (
                sumolib.net.readNet(
                    str(path)
                )
            )

        results = []

        for i, feature in enumerate(
            features,
            start=1,
        ):

            route_id = feature.get(
                "properties",
                {}
            ).get(
                "route_id",
                f"feature_{i}"
            )

            print()
            print(
                f"[ROUTE {i}/{len(features)}] "
                f"{route_id}"
            )

            try:

                scenario, corridor, direction_key = parse_route_id(
                    str(route_id)
                )

                if scenario not in networks:
                    raise RuntimeError(
                        f"Rede não carregada para "
                        f"{scenario}"
                    )

                result = process_feature(
                    feature=feature,
                    networks=networks,
                    od_index=od_index,
                    search_radius=args.search_radius,
                    sample_spacing=args.sample_spacing,
                )

                results.append(
                    result
                )

                print(
                    f"      cenário  : "
                    f"{result['scenario']}"
                )

                print(
                    f"      corredor  : "
                    f"{result['corridor']}"
                )

                print(
                    f"      direção   : "
                    f"{result['direction']}"
                )

                print(
                    f"      edges     : "
                    f"{result['num_sumo_edges']}"
                )

                print(
                    f"      distância média: "
                    f"{result['mean_map_distance_m']:.2f} m"
                )

                print(
                    f"      velocidade: "
                    f"{result['speed_kmh']:.2f} km/h"
                )

                print(
                    f"      O/D origem: "
                    f"{result['origin_edge_in_route']}"
                )

                print(
                    f"      O/D destino: "
                    f"{result['destination_edge_in_route']}"
                )

            except Exception as exc:

                print(
                    f"[ERROR] {exc}"
                )

                results.append({
                    "route_id": route_id,
                    "error": str(exc),
                })

        if not results:
            raise RuntimeError(
                "Nenhuma rota foi processada."
            )

        save_results(
            results,
            args.output_csv,
            args.output_json,
        )

        success = sum(
            1
            for result in results
            if "error" not in result
        )

        errors = len(
            results
        ) - success

        print()
        print("=" * 80)
        print("RESUMO")
        print("=" * 80)

        print(
            f"Rotas processadas : "
            f"{len(results)}"
        )

        print(
            f"Sucesso           : "
            f"{success}"
        )

        print(
            f"Erros             : "
            f"{errors}"
        )

        print(
            f"CSV               : "
            f"{args.output_csv}"
        )

        print(
            f"JSON              : "
            f"{args.output_json}"
        )

        return 0 if errors == 0 else 1

    except Exception as exc:

        print(
            f"[ERROR] {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
