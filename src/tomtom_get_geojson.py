#!/usr/bin/env python3
"""
Download completo do TomTom Traffic Stats Area-Full.

Objetivo:
    Baixar os dados Traffic Stats de um mês inteiro para posterior
    divisão temporal:

        Semanas 1-3 -> calibração
        Semana 4    -> validação

O script preserva os arquivos brutos retornados pela API.

Entrada:
    conf/tomTom.json
    conf/moveTomTom.json

Saída:
    trafficStats/
        <job_id>/
            request.json
            create_response.json
            status_*.json
            result_*.geojson.gz
            metadata.json

Uso:
    python src/download_traffic_stats.py \
        --start-date 2026-07-01 \
        --end-date 2026-07-31

Dependências:
    pip install requests
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TOMTOM_CONFIG = (
    PROJECT_ROOT / "conf" / "tomTom.json"
)

DEFAULT_MOVE_CONFIG = (
    PROJECT_ROOT / "conf" / "moveTomTom.json"
)

DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "trafficStats"
)

DEFAULT_POLL_SECONDS = 10

BASE_URL = "https://api.tomtom.com"

# Endpoint do Traffic Stats.
TRAFFIC_STATS_CREATE = (
    "/traffic/trafficstats/areaanalysis/1"
)

TRAFFIC_STATS_STATUS = (
    "/traffic/trafficstats/area/2/{job_id}"
)

TRAFFIC_STATS_RESULT = (
    "/traffic/trafficstats/area/2/{job_id}/result"
)


def print_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


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


def save_json(
    data: Any,
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False,
        )


def normalize_date(
    value: str,
) -> str:
    """
    Aceita:
        YYYY-MM-DD
        YYYY-MM-DDTHH:MM:SS

    Retorna:
        YYYY-MM-DD
    """
    try:
        return datetime.fromisoformat(
            value
        ).strftime(
            "%Y-%m-%d"
        )
    except ValueError:
        raise ValueError(
            f"Data inválida: {value}. "
            "Use YYYY-MM-DD."
        )


def validate_month(
    start_date: str,
    end_date: str,
) -> None:
    start = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    )

    end = datetime.strptime(
        end_date,
        "%Y-%m-%d",
    )

    if end < start:
        raise ValueError(
            "end-date deve ser posterior ou igual "
            "a start-date."
        )

    if start.month != end.month:
        raise ValueError(
            "start-date e end-date devem pertencer "
            "ao mesmo mês."
        )

    if start.year != end.year:
        raise ValueError(
            "start-date e end-date devem pertencer "
            "ao mesmo ano."
        )


def extract_api_key(
    config: dict[str, Any],
) -> str:
    """
    Procura a API key em estruturas comuns.

    Também aceita:
        {
            "apiKey": "..."
        }

        {
            "api_key": "..."
        }

        {
            "key": "..."
        }
    """
    candidates = [
        "apiKey",
        "api_key",
        "apikey",
        "key",
    ]

    for key in candidates:
        value = config.get(key)

        if value:
            return str(value)

    raise ValueError(
        "Não foi possível encontrar a API key."
    )


def extract_area(
    config: dict[str, Any],
) -> Any:
    """
    Procura a definição da área no moveTomTom.json.

    O script preserva a estrutura existente sempre que possível.
    """
    candidates = [
        "area",
        "polygon",
        "geometry",
        "boundingBox",
        "bbox",
    ]

    for key in candidates:
        if key in config:
            return config[key]

    return config


def build_request_body(
    move_config: dict[str, Any],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """
    Monta o corpo do Traffic Stats Area-Full.

    A configuração de área é mantida a partir do arquivo
    moveTomTom.json.
    """
    area = extract_area(
        move_config
    )

    body = {
        "jobName" : "TrafficStats_all",
        "distanceUnit": "KILOMETERS",
        "network": {
            "name": "SUMO_all",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [
                        [
                            [-23.651964, -46.722515],
                            [-23.644716, -46.727439],
                            [-23.555591, -46.662701],
                            [-23.547366, -46.64759],
                            [-23.58886, -46.728426],
                            [-23.584888, -46.719688],
                            [-23.583123, -46.724843],
                            [-23.588796, -46.722879]
                        ]
                    ],
                    [
                        [
                            [-23.644944, -46.725554],
                            [-23.652411, -46.725454],
                            [-23.547502, -46.647512],
                            [-23.555485, -46.662814],
                            [-23.58507, -46.719822],
                            [-23.588747, -46.728523],
                            [-23.588629, -46.723191],
                            [-23.583231, -46.724542]
                        ]
                    ]
                ]
            },
            "timeZoneId": "America/Sao_Paulo",
            "frcs": ["0", "1", "2", "3", "4", "5", "6", "7"],
            "probeSource": "ALL"
        },
        "dateRange": {
            "name": "full_month",
            "from": start_date,
            "to": end_date,
        },
        "timeSets": [
            {
                "name": "full_day",
                "timeSet": {
                    "days": [
                        "MONDAY",
                        "TUESDAY",
                        "WEDNESDAY",
                        "THURSDAY",
                        "FRIDAY",
                        "SATURDAY",
                        "SUNDAY",
                    ],
                    "from": "00:00:00",
                    "to": "23:59:59",
                },
            }
        ]
    }

    return body


def create_job(
    api_key: str,
    body: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """
    Cria o job Traffic Stats.
    """
    url = (
        BASE_URL
        + TRAFFIC_STATS_CREATE
    )

    params = {
        "key": api_key,
    }

    response = requests.post(
        url,
        params=params,
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=120,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            "Erro ao criar Traffic Stats job:\n"
            f"HTTP {response.status_code}\n"
            f"{response.text}"
        )

    data = response.json()

    job_id = (
        data.get("jobId")
        or data.get("jobID")
        or data.get("id")
    )

    if not job_id:
        raise RuntimeError(
            "Resposta da API não contém jobId:\n"
            + json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            )
        )

    return (
        str(job_id),
        data,
    )


def get_status(
    api_key: str,
    job_id: str,
) -> dict[str, Any]:
    url = (
        BASE_URL
        + TRAFFIC_STATS_STATUS.format(
            job_id=job_id
        )
    )

    response = requests.get(
        url,
        params={
            "key": api_key,
        },
        timeout=120,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            "Erro consultando status:\n"
            f"HTTP {response.status_code}\n"
            f"{response.text}"
        )

    return response.json()


def get_status_value(
    data: dict[str, Any],
) -> str:
    """
    Obtém status independentemente de pequenas diferenças
    no formato da resposta.
    """
    candidates = [
        "status",
        "jobStatus",
        "state",
    ]

    for key in candidates:
        value = data.get(key)

        if value is not None:
            return str(value).upper()

    return ""


def is_completed(
    status: str,
) -> bool:
    return status in {
        "COMPLETED",
        "COMPLETE",
        "FINISHED",
        "DONE",
        "SUCCESS",
        "SUCCEEDED",
    }


def is_failed(
    status: str,
) -> bool:
    return status in {
        "FAILED",
        "ERROR",
        "CANCELLED",
        "CANCELED",
    }


def wait_for_job(
    api_key: str,
    job_id: str,
    output_dir: Path,
    poll_seconds: int,
) -> dict[str, Any]:
    """
    Aguarda o processamento do job.
    """
    print()
    print(
        f"[INFO] Aguardando processamento do job {job_id}"
    )

    counter = 0

    while True:

        status_data = get_status(
            api_key,
            job_id,
        )

        counter += 1

        status = get_status_value(
            status_data
        )

        print(
            f"[STATUS {counter}] "
            f"{status or 'UNKNOWN'}"
        )

        save_json(
            status_data,
            output_dir
            / f"status_{counter:03d}.json",
        )

        if is_completed(
            status
        ):
            print(
                "[OK] Job concluído."
            )
            return status_data

        if is_failed(
            status
        ):
            raise RuntimeError(
                "Traffic Stats job falhou:\n"
                + json.dumps(
                    status_data,
                    indent=2,
                    ensure_ascii=False,
                )
            )

        time.sleep(
            poll_seconds
        )


def extract_result_urls(
    data: Any,
) -> list[str]:
    """
    Procura URLs de resultado recursivamente.
    """
    urls = []

    def walk(
        obj: Any,
    ) -> None:

        if isinstance(
            obj,
            dict,
        ):
            for key, value in obj.items():

                if isinstance(
                    value,
                    str,
                ):
                    lower = value.lower()

                    if (
                        lower.startswith(
                            "http"
                        )
                        and (
                            "result"
                            in key.lower()
                            or "url"
                            in key.lower()
                            or "download"
                            in key.lower()
                        )
                    ):
                        urls.append(
                            value
                        )

                walk(
                    value
                )

        elif isinstance(
            obj,
            list,
        ):
            for item in obj:
                walk(
                    item
                )

    walk(data)

    # Remove duplicados preservando ordem.
    unique = []

    for url in urls:
        if url not in unique:
            unique.append(url)

    return unique


def download_url(
    url: str,
    api_key: str,
    output_path: Path,
) -> None:
    """
    Baixa um arquivo de resultado.
    """
    print(
        f"[DOWNLOAD] {url}"
    )

    response = requests.get(
        url,
        params={
            "key": api_key,
        },
        stream=True,
        timeout=300,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            "Erro baixando resultado:\n"
            f"HTTP {response.status_code}\n"
            f"{response.text[:2000]}"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "wb"
    ) as f:

        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):
            if chunk:
                f.write(
                    chunk
                )

    print(
        f"[OK] Salvo: {output_path}"
    )


def download_results(
    api_key: str,
    job_id: str,
    status_data: dict[str, Any],
    output_dir: Path,
) -> list[str]:
    """
    Baixa todos os resultados encontrados na resposta.
    """
    urls = extract_result_urls(
        status_data
    )

    # Caso o status não forneça URLs,
    # tenta o endpoint explícito de resultado.
    if not urls:

        result_url = (
            BASE_URL
            + TRAFFIC_STATS_RESULT.format(
                job_id=job_id
            )
        )

        response = requests.get(
            result_url,
            params={
                "key": api_key,
            },
            timeout=120,
        )

        if response.status_code < 400:

            content_type = (
                response.headers
                .get(
                    "Content-Type",
                    "",
                )
                .lower()
            )

            if (
                "json"
                in content_type
            ):

                data = response.json()

                save_json(
                    data,
                    output_dir
                    / "result_response.json",
                )

                urls = extract_result_urls(
                    data
                )

            else:

                output_path = (
                    output_dir
                    / "result_000"
                )

                output_path.write_bytes(
                    response.content
                )

                return [
                    str(output_path)
                ]

    if not urls:
        print(
            "[WARNING] Nenhuma URL de resultado "
            "encontrada automaticamente."
        )
        return []

    downloaded = []

    for index, url in enumerate(
        urls
    ):

        suffix = ".bin"

        lower = url.lower()

        if ".geojson.gz" in lower:
            suffix = ".geojson.gz"
        elif ".geojson" in lower:
            suffix = ".geojson"
        elif ".json" in lower:
            suffix = ".json"
        elif ".zip" in lower:
            suffix = ".zip"
        elif ".gz" in lower:
            suffix = ".gz"

        output_path = (
            output_dir
            / f"result_{index:03d}{suffix}"
        )

        download_url(
            url,
            api_key,
            output_path,
        )

        downloaded.append(
            str(output_path)
        )

    return downloaded


def create_metadata(
    job_id: str,
    start_date: str,
    end_date: str,
    request_body: dict[str, Any],
    downloaded_files: list[str],
) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "dateRange": {
            "from": start_date,
            "to": end_date,
        },
        "calibration_period": {
            "description": (
                "Primeiras três semanas do período mensal."
            ),
            "weeks": [
                1,
                2,
                3,
            ],
        },
        "validation_period": {
            "description": (
                "Quarta semana do período mensal."
            ),
            "week": 4,
        },
        "request": request_body,
        "downloaded_files": downloaded_files,
        "created_at": datetime.now().isoformat(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Baixa Traffic Stats Area-Full de um mês."
        )
    )

    parser.add_argument(
        "--start-date",
        required=True,
        help="Data inicial: YYYY-MM-DD.",
    )

    parser.add_argument(
        "--end-date",
        required=True,
        help="Data final: YYYY-MM-DD.",
    )

    parser.add_argument(
        "--tomtom-config",
        type=Path,
        default=DEFAULT_TOMTOM_CONFIG,
    )

    parser.add_argument(
        "--move-config",
        type=Path,
        default=DEFAULT_MOVE_CONFIG,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )

    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print_header(
        "TOMTOM TRAFFIC STATS AREA-FULL"
    )

    try:
        start_date = normalize_date(
            args.start_date
        )

        end_date = normalize_date(
            args.end_date
        )

        validate_month(
            start_date,
            end_date,
        )

        print(
            f"[INFO] Período: "
            f"{start_date} → {end_date}"
        )

        tomtom_config = load_json(
            args.tomtom_config
        )

        move_config = load_json(
            args.move_config
        )

        api_key = extract_api_key(
            tomtom_config
        )

        request_body = build_request_body(
            move_config,
            start_date,
            end_date,
        )

        print()
        print(
            "[INFO] Criando Traffic Stats job..."
        )

        job_id, create_response = create_job(
            api_key,
            request_body,
        )

        job_dir = (
            args.output_dir
            / str(job_id)
        )

        job_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        save_json(
            request_body,
            job_dir / "request.json",
        )

        save_json(
            create_response,
            job_dir / "create_response.json",
        )

        print(
            f"[OK] Job criado: {job_id}"
        )

        status_data = wait_for_job(
            api_key=api_key,
            job_id=job_id,
            output_dir=job_dir,
            poll_seconds=args.poll_seconds,
        )

        save_json(
            status_data,
            job_dir / "final_status.json",
        )

        print()
        print(
            "[INFO] Baixando resultados..."
        )

        downloaded_files = download_results(
            api_key=api_key,
            job_id=job_id,
            status_data=status_data,
            output_dir=job_dir,
        )

        metadata = create_metadata(
            job_id=job_id,
            start_date=start_date,
            end_date=end_date,
            request_body=request_body,
            downloaded_files=downloaded_files,
        )

        save_json(
            metadata,
            job_dir / "metadata.json",
        )

        print()
        print("=" * 80)
        print("RESUMO")
        print("=" * 80)

        print(
            f"Job ID       : {job_id}"
        )

        print(
            f"Período      : "
            f"{start_date} → {end_date}"
        )

        print(
            f"Arquivos     : "
            f"{len(downloaded_files)}"
        )

        print(
            f"Diretório    : {job_dir}"
        )

        print()
        print(
            "[OK] Download concluído."
        )

        return 0

    except Exception as exc:

        print(
            f"[ERROR] {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
