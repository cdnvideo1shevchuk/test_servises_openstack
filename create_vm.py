#!/usr/bin/env python3
"""Утилита для создания виртуальной машины в OpenStack.

Скрипт создаёт корневой том из заданного образа, поднимает сервер с этим
томом, дожидается активного состояния и печатает основные параметры созданной
ВМ. Конфигурация (образ, сеть, ключ и security group) хранится в константах
ниже — подставь свои значения при необходимости.
"""

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
    connect,
)

# ===== Константы под твою среду =====
# Значения констант импортируются из openstack_utils.py

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


def ensure_positive_size(size_gb: int) -> None:
    """Небольшая валидация размера тома."""

    if size_gb <= 0:
        raise ValueError("Размер тома должен быть больше нуля")


def build_host_fqdn(host: str | None) -> str | None:
    """Дополняет короткое имя hypervisor до FQDN.

    OpenStack ожидает полное имя compute-ноды при явном указании AZ, поэтому
    автоматом подставляем домен, если пользователь указал только o2nX.
    """

    if not host:
        return None
    return host if "." in host else f"{host}.001.gpucloud.ru"


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
        # Подтягиваем все зависимые ресурсы.
        net = conn.network.find_network(NETWORK_NAME, ignore_missing=False)
        flv = conn.compute.find_flavor(args.flavor, ignore_missing=False)
        sg = conn.network.get_security_group(SECURITY_GROUP_ID)
        if not sg:
            die(f"Security group {SECURITY_GROUP_ID} не найден")

        # Создаём корневой том с нужным типом и образом.
        volume = conn.block_storage.create_volume(
            name=f"{name}-root",
            size=args.size_gb,
            image_id=IMAGE_ID,
            volume_type=args.volume_type,
        )
        volume = conn.block_storage.wait_for_status(
            volume,
            status="available",
            failures=["error"],
        )

        # Если при создании сервера что-то пойдёт не так — удаляем том вручную.
        server = None
        try:
            server_kwargs = dict(
                name=name,
                flavor_id=flv.id,
                block_device_mapping_v2=[
                    {
                        "boot_index": 0,
                        "uuid": volume.id,
                        "source_type": "volume",
                        "destination_type": "volume",
                        "delete_on_termination": True,
                    }
                ],
                networks=[{"uuid": net.id}],
                security_groups=[{"name": sg.name}],
                key_name=KEY_NAME,
            )
            if host_fqdn:
                server_kwargs["availability_zone"] = f"nova:{host_fqdn}"

            # Создаём ВМ и ждём активного состояния.
            server = conn.compute.create_server(**server_kwargs)
            server = conn.compute.wait_for_server(
                server,
                status="ACTIVE",
                failures=["ERROR"],
            )
        except Exception:
            # Если Nova не смогла создать сервер, удаляем том, чтобы не осталось
            # висящих ресурсов.
            with suppress(Exception):
                conn.block_storage.delete_volume(volume, ignore_missing=True)
            raise

        # Дополнительно запрашиваем сервер, чтобы получить hypervisor_hostname.
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
