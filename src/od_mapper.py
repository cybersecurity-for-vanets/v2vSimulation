#!/usr/bin/env python3
"""
Etapa 1 - Calibração Macroscópica
---------------------------------
Mapeia os pares O/D definidos em conf/routes.json para edges da rede SUMO
(.net.xml), preservando a direção de cada fluxo.

Direções:
    downtown     -> center_to_neighborhood
    neighborhood -> neighborhood_to_center

Para cada O/D são determinadas:
    - coordenadas geográficas;
    - coordenadas SUMO;
    - origin_edge;
    - destination_edge;
    - distância entre coordenada e edge;
    - existência de rota válida O/D.

Entrada:
    conf/routes.json
    roadNetwork/*.net.xml

Saída:
    output/od_mapping.json
    output/od_mapping.csv

Dependência:
    sumolib
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import sumolib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROUTES = PROJECT_ROOT / "conf" / "routes.json"
DEFAULT_NETWORK_DIR = PROJECT_ROOT / "roadNetwork"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "output" / "od_mapping.json"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "output" / "od_mapping.csv"
DEFAULT_SEARCH_RADIUS = 100.0
DEFAULT_MAX_DISTANCE = 100.0

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
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_coordinates(value: Any, context: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(
            f"{context}: esperado [latitude, longitude], recebido {value}"
        )
    lat, lon = float(value[0]), float(value[1])
    if not -90 <= lat <= 90:
        raise ValueError(f"{context}: latitude inválida: {lat}")
    if not -180 <= lon <= 180:
        raise ValueError(f"{context}: longitude inválida: {lon}")
    return lat, lon


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def flatten_routes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Converte routes.json em registros O/D explícitos.

    highway / urban_road:
        scenario / downtown
        scenario / neighborhood

    intersection:
        scenario / main_street / downtown
        scenario / main_street / neighborhood
        scenario / cross_street / downtown
        scenario / cross_street / neighborhood
    """
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict):
        raise ValueError("routes.json não contém 'scenarios' válido.")

    records = []

    for scenario, scenario_data in scenarios.items():
        if scenario not in NETWORK_FILES:
            print(f"[WARNING] Cenário desconhecido ignorado: {scenario}")
            continue

        if not isinstance(scenario_data, dict):
            raise ValueError(f"Cenário inválido: {scenario}")

        for key, value in scenario_data.items():
            if not isinstance(value, dict):
                continue

            # highway / urban_road
            if "start_coordinates" in value and "end_coordinates" in value:
                records.append({
                    "scenario": scenario,
                    "corridor": None,
                    "direction_key": key,
                    "direction": DIRECTION_NAMES.get(key, key),
                    "start_coordinates": value["start_coordinates"],
                    "end_coordinates": value["end_coordinates"],
                })
                continue

            # intersection
            corridor = key
            for direction_key, route in value.items():
                if not isinstance(route, dict):
                    continue
                if "start_coordinates" not in route or "end_coordinates" not in route:
                    print(
                        f"[WARNING] O/D incompleto: "
                        f"{scenario}/{corridor}/{direction_key}"
                    )
                    continue

                records.append({
                    "scenario": scenario,
                    "corridor": corridor,
                    "direction_key": direction_key,
                    "direction": DIRECTION_NAMES.get(direction_key, direction_key),
                    "start_coordinates": route["start_coordinates"],
                    "end_coordinates": route["end_coordinates"],
                })

    return records


def edge_is_usable(edge: Any) -> bool:
    try:
        if edge.getFunction() == "internal":
            return False
    except AttributeError:
        pass

    try:
        return len(edge.getLanes()) > 0
    except AttributeError:
        return False


def find_candidate_edges(
    net: Any,
    x: float,
    y: float,
    radius: float,
    max_candidates: int = 10,
) -> list[tuple[Any, float]]:
    """
    Retorna as edges utilizáveis mais próximas do ponto.
    """
    candidates = net.getNeighboringEdges(x, y, radius)
    candidates = [
        (edge, float(distance))
        for edge, distance in candidates
        if edge_is_usable(edge)
    ]
    candidates.sort(key=lambda item: item[1])
    return candidates[:max_candidates]


def convert_coordinates(
    net: Any,
    lat: float,
    lon: float,
) -> tuple[float, float]:
    """
    routes.json usa [latitude, longitude].
    SUMO espera longitude, latitude na conversão.
    """
    x, y = net.convertLonLat2XY(lon, lat)
    return float(x), float(y)


def find_valid_od_pair(
    net: Any,
    origin_candidates: list[tuple[Any, float]],
    destination_candidates: list[tuple[Any, float]],
) -> tuple[Any, float, Any, float, bool]:
    """
    Procura uma combinação O/D para a qual exista uma rota válida.

    A prioridade é minimizar:
        distância_origem + distância_destino

    A verificação de conectividade é feita através da topologia da rede.
    """
    best = None

    for origin_edge, origin_dist in origin_candidates:
        for destination_edge, destination_dist in destination_candidates:
            if origin_edge == destination_edge:
                route_exists = True
            else:
                route_exists = check_route_exists(
                    origin_edge,
                    destination_edge,
                )

            if not route_exists:
                continue

            score = origin_dist + destination_dist

            if best is None or score < best[0]:
                best = (
                    score,
                    origin_edge,
                    origin_dist,
                    destination_edge,
                    destination_dist,
                )

    if best is None:
        raise RuntimeError(
            "Não foi encontrada combinação de origin/destination "
            "edges com conectividade válida."
        )

    _, origin_edge, origin_dist, destination_edge, destination_dist = best

    return (
        origin_edge,
        origin_dist,
        destination_edge,
        destination_dist,
        True,
    )


def check_route_exists(
    origin_edge: Any,
    destination_edge: Any,
    max_depth: int = 10000,
) -> bool:
    """
    Verifica conectividade direcional entre duas edges.

    A busca segue:
        origin_edge.to_node -> destination_edge.from_node

    Isso respeita o sentido das edges SUMO.
    """
    start = origin_edge.getToNode()
    target = destination_edge.getFromNode()

    if start == target:
        return True

    visited = {start}
    queue = [start]

    depth = 0
    while queue and depth < max_depth:
        node = queue.pop(0)

        for edge in node.getOutgoing():
            next_node = edge.getToNode()

            if next_node == target:
                return True

            if next_node not in visited:
                visited.add(next_node)
                queue.append(next_node)

        depth += 1

    return False


def process_record(
    net: Any,
    record: dict[str, Any],
    search_radius: float,
    max_distance: float,
) -> dict[str, Any]:
    context = (
        f"{record['scenario']}/"
        f"{record['corridor'] + '/' if record['corridor'] else ''}"
        f"{record['direction_key']}"
    )

    start_lat, start_lon = validate_coordinates(
        record["start_coordinates"],
        f"{context}/start_coordinates",
    )
    end_lat, end_lon = validate_coordinates(
        record["end_coordinates"],
        f"{context}/end_coordinates",
    )

    start_x, start_y = convert_coordinates(
        net,
        start_lat,
        start_lon,
    )
    end_x, end_y = convert_coordinates(
        net,
        end_lat,
        end_lon,
    )

    origin_candidates = find_candidate_edges(
        net,
        start_x,
        start_y,
        search_radius,
    )
    destination_candidates = find_candidate_edges(
        net,
        end_x,
        end_y,
        search_radius,
    )

    if not origin_candidates:
        raise RuntimeError(
            f"Nenhuma edge utilizável encontrada para origem "
            f"dentro de {search_radius:.1f} m."
        )

    if not destination_candidates:
        raise RuntimeError(
            f"Nenhuma edge utilizável encontrada para destino "
            f"dentro de {search_radius:.1f} m."
        )

    (
        origin_edge,
        origin_distance,
        destination_edge,
        destination_distance,
        route_exists,
    ) = find_valid_od_pair(
        net,
        origin_candidates,
        destination_candidates,
    )

    warnings = []

    if origin_distance > max_distance:
        warnings.append("origin_distance_exceeds_threshold")

    if destination_distance > max_distance:
        warnings.append("destination_distance_exceeds_threshold")

    return {
        "scenario": record["scenario"],
        "corridor": record["corridor"],
        "direction_key": record["direction_key"],
        "direction": record["direction"],

        "origin": {
            "latitude": start_lat,
            "longitude": start_lon,
            "x": round(start_x, 3),
            "y": round(start_y, 3),
            "edge_id": origin_edge.getID(),
            "distance_m": round(origin_distance, 3),
        },

        "destination": {
            "latitude": end_lat,
            "longitude": end_lon,
            "x": round(end_x, 3),
            "y": round(end_y, 3),
            "edge_id": destination_edge.getID(),
            "distance_m": round(destination_distance, 3),
        },

        "route_exists": route_exists,
        "warnings": warnings,
    }


def save_json(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def save_csv(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = []

    for item in data["routes"]:
        origin = item.get("origin", {})
        destination = item.get("destination", {})

        rows.append({
            "scenario": item.get("scenario"),
            "corridor": item.get("corridor"),
            "direction": item.get("direction"),
            "direction_key": item.get("direction_key"),

            "origin_latitude": origin.get("latitude"),
            "origin_longitude": origin.get("longitude"),
            "origin_edge": origin.get("edge_id"),
            "origin_distance_m": origin.get("distance_m"),

            "destination_latitude": destination.get("latitude"),
            "destination_longitude": destination.get("longitude"),
            "destination_edge": destination.get("edge_id"),
            "destination_distance_m": destination.get("distance_m"),

            "route_exists": item.get("route_exists"),
            "warnings": ";".join(item.get("warnings", [])),
            "error": item.get("error"),
        })

    fields = [
        "scenario",
        "corridor",
        "direction",
        "direction_key",
        "origin_latitude",
        "origin_longitude",
        "origin_edge",
        "origin_distance_m",
        "destination_latitude",
        "destination_longitude",
        "destination_edge",
        "destination_distance_m",
        "route_exists",
        "warnings",
        "error",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mapeia O/D direcionais para edges SUMO."
    )

    parser.add_argument(
        "--routes",
        type=Path,
        default=DEFAULT_ROUTES,
    )

    parser.add_argument(
        "--network-dir",
        type=Path,
        default=DEFAULT_NETWORK_DIR,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
    )

    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
    )

    parser.add_argument(
        "--search-radius",
        type=float,
        default=DEFAULT_SEARCH_RADIUS,
        help="Raio de busca das edges em metros.",
    )

    parser.add_argument(
        "--max-distance",
        type=float,
        default=DEFAULT_MAX_DISTANCE,
        help="Distância máxima desejável O/D-edge em metros.",
    )

    parser.add_argument(
        "--no-csv",
        action="store_true",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 80)
    print("ETAPA 1 - MAPEAMENTO O/D DIRECIONAL")
    print("=" * 80)

    try:
        data = load_json(args.routes)
        records = flatten_routes(data)

        if not records:
            raise RuntimeError("Nenhum O/D encontrado em routes.json.")

        networks = {}
        results = []

        print(f"[INFO] O/D encontrados: {len(records)}")

        for record in records:
            scenario = record["scenario"]
            network_file = NETWORK_FILES[scenario]
            network_path = args.network_dir / network_file

            if not network_path.exists():
                raise FileNotFoundError(
                    f"Rede não encontrada: {network_path}"
                )

            if scenario not in networks:
                print(f"[INFO] Carregando {network_path}")
                networks[scenario] = sumolib.net.readNet(
                    str(network_path)
                )

            net = networks[scenario]

            context = (
                f"{scenario}/"
                f"{record['corridor'] + '/' if record['corridor'] else ''}"
                f"{record['direction']}"
            )

            print()
            print(f"[MAP] {context}")

            try:
                result = process_record(
                    net,
                    record,
                    args.search_radius,
                    args.max_distance,
                )

                results.append(result)

                print(
                    f"      O: {result['origin']['edge_id']} "
                    f"({result['origin']['distance_m']:.2f} m)"
                )
                print(
                    f"      D: {result['destination']['edge_id']} "
                    f"({result['destination']['distance_m']:.2f} m)"
                )
                print(
                    f"      rota válida: "
                    f"{result['route_exists']}"
                )

                if result["warnings"]:
                    print(
                        "      WARNING: "
                        + ", ".join(result["warnings"])
                    )
                else:
                    print("      status: OK")

            except Exception as exc:
                print(f"      ERROR: {exc}")

                results.append({
                    "scenario": record["scenario"],
                    "corridor": record["corridor"],
                    "direction_key": record["direction_key"],
                    "direction": record["direction"],
                    "start_coordinates": record["start_coordinates"],
                    "end_coordinates": record["end_coordinates"],
                    "error": str(exc),
                })

        output = {
            "metadata": {
                "routes_file": str(args.routes),
                "network_directory": str(args.network_dir),
                "search_radius_m": args.search_radius,
                "max_distance_m": args.max_distance,
                "num_routes": len(records),
                "num_success": sum(
                    1 for r in results if "error" not in r
                ),
                "num_errors": sum(
                    1 for r in results if "error" in r
                ),
            },
            "routes": results,
        }

        save_json(output, args.output)

        if not args.no_csv:
            save_csv(output, args.csv)

        print()
        print("=" * 80)
        print("RESUMO")
        print("=" * 80)
        print(f"O/D processados : {len(records)}")
        print(
            f"Sucesso         : "
            f"{output['metadata']['num_success']}"
        )
        print(
            f"Erros           : "
            f"{output['metadata']['num_errors']}"
        )
        print(f"JSON            : {args.output}")

        if not args.no_csv:
            print(f"CSV             : {args.csv}")

        return 0 if output["metadata"]["num_errors"] == 0 else 1

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
