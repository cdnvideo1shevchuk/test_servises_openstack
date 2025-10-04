#!/usr/bin/env python3
"""Утилита для создания виртуальной машины в OpenStack."""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress

from openstack import exceptions as os_exc

from openstack_utils import (
    IMAGE_ID,
    KEY_NAME,
    NETWORK_NAME,
    SECURITY_GROUP_ID,
    SERVER_NAME_PREFIX,
    build_host_fqdn,
    connect,
    create_server_from_volume,
    create_volume,
    delete_volume,
    ensure_positive_size,
    resolve_base_resources,
)


def parse_args() -> argparse.Namespace:
    """Разбор аргументов командной строки."""

    parser = argparse.ArgumentParser(
        description=(
            "Создать ВМ: FLAVOR VOLUME_TYPE SIZE_GB [HOST]\n"
            "Пример: create_vm.py amd-1-1 SSD 30 o2n40"
        )
    )
    parser.add_argument("flavor", help="Имя/ID flavor, напр. amd-1-1")
    parser.add_argument(
        "volume_type",
        help="Тип тома Cinder: SSD | NVME | HDD | SSD-MA",
    )
    parser.add_argument(
        "size_gb",
        type=int,
        help="Размер корневого тома в ГБ",
    )
    parser.add_argument(
        "host",
        nargs="?",
        default=None,
        help="Опционально: o2nX или FQDN, напр. o2n40 или o2n40.001.gpucloud.ru",
    )
    parser.add_argument("--name", default=None, help="Имя ВМ (опционально)")
    return parser.parse_args()


def die(msg: str, code: int = 1) -> None:
    """Печатает сообщение об ошибке и завершает процесс."""

    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    args = parse_args()
    ensure_positive_size(args.size_gb)

    name = args.name or f"{SERVER_NAME_PREFIX}_auto"
    host_fqdn = build_host_fqdn(args.host)

    conn = connect()

    try:
        resources = resolve_base_resources(
            conn,
            network_name=NETWORK_NAME,
            flavor_name=args.flavor,
            security_group_id=SECURITY_GROUP_ID,
        )

        volume = create_volume(
            conn,
            name=f"{name}-root",
            size_gb=args.size_gb,
            volume_type=args.volume_type,
            image_id=IMAGE_ID,
        )

        server = None
        try:
            server = create_server_from_volume(
                conn,
                name=name,
                flavor_id=resources.flavor_id,
                volume_id=volume.id,
                network_id=resources.network_id,
                security_group_name=resources.security_group_name,
                key_name=KEY_NAME,
                host_fqdn=host_fqdn,
            )
        except Exception:
            with suppress(Exception):
                delete_volume(conn, volume.id)
            raise

        server = conn.compute.get_server(server.id)
        hyper = getattr(server, "hypervisor_hostname", None)

        print("OK")
        print(f"id={server.id}")
        print(f"name={server.name}")
        print(f"hypervisor=" + (hyper or "(unknown)"))
        print(f"addresses={server.addresses or {}}")
        print(f"volume_id={volume.id}")

    except os_exc.ResourceNotFound as exc:
        die(f"Ресурс не найден: {exc}")
    except os_exc.HttpException as exc:
        die(f"HTTP error: {exc}")
    except Exception as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
