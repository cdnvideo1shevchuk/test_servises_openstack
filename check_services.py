#!/usr/bin/env python3
"""Набор проверок для базовых сервисов OpenStack.

Скрипт создаёт тестовый том и небольшую виртуальную машину, чтобы убедиться,
что основные сервисы (Cinder, Nova, Neutron) работают корректно. Дополнительно
можно проверить создание floating IP, создание снепшота и получение консольного
вывода через флаги командной строки.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Callable, List

import openstack
from openstack import exceptions as os_exc


# ===== Утилиты работы с CLI =====

def parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""

    parser = argparse.ArgumentParser(
        description="""
        Проверка сервисов OpenStack. Скрипт создаёт тестовый том, виртуальную
        машину и, опционально, дополнительные ресурсы: floating IP, снепшот и
        консольный вывод. Все созданные ресурсы удаляются в конце работы.
        """.strip()
    )

    parser.add_argument(
        "--image",
        required=True,
        help="Имя или ID образа, из которого поднимется тестовая ВМ",
    )
    parser.add_argument(
        "--flavor",
        required=True,
        help="Имя или ID flavor для тестовой ВМ",
    )
    parser.add_argument(
        "--network",
        required=True,
        help="Имя или ID сети, к которой подключится ВМ",
    )
    parser.add_argument(
        "--security-group",
        required=True,
        help="Имя или ID security group для ВМ",
    )
    parser.add_argument(
        "--key-name",
        required=True,
        help="Имя загруженного в OpenStack SSH-ключа",
    )
    parser.add_argument(
        "--volume-size",
        type=int,
        default=5,
        help="Размер тестового тома (ГБ), по умолчанию 5",
    )
    parser.add_argument(
        "--volume-type",
        default=None,
        help="Тип тестового тома Cinder (опционально)",
    )
    parser.add_argument(
        "--server-name",
        default="service-checker",
        help="Имя тестовой ВМ (по умолчанию service-checker)",
    )
    parser.add_argument(
        "--availability-zone",
        default=None,
        help="Желаемая availability zone для тестовой ВМ (опционально)",
    )
    parser.add_argument(
        "--floating-network",
        default=None,
        help="Имя или ID внешней сети для создания floating IP",
    )
    parser.add_argument(
        "--with-floating-ip",
        action="store_true",
        help="Создать floating IP, привязать к ВМ и проверить",  # noqa: E501
    )
    parser.add_argument(
        "--with-snapshot",
        action="store_true",
        help="Создать снепшот тестового тома и дождаться статуса available",
    )
    parser.add_argument(
        "--with-console",
        action="store_true",
        help="Получить консольный вывод созданной ВМ",
    )

    return parser.parse_args()


# ===== Обёртка для финальной очистки =====


@dataclass
class CleanupAction:
    description: str
    func: Callable[[], None]


class CleanupManager:
    """Регистрирует функции очистки и выполняет их в обратном порядке."""

    def __init__(self) -> None:
        self._callbacks: List[CleanupAction] = []

    def add(self, description: str, func: Callable[[], None]) -> None:
        self._callbacks.append(CleanupAction(description, func))

    def run(self) -> None:
        """Выполняет функции очистки, игнорируя ошибки."""

        for action in reversed(self._callbacks):
            try:
                action.func()
            except Exception as exc:  # pragma: no cover - защитный код
                print(
                    f"Cleanup failed ({action.description}): {exc}",
                    file=sys.stderr,
                )


# ===== Соединение и общие помощники =====


def connect() -> openstack.connection.Connection:
    """Создаёт соединение с OpenStack, используя переменные окружения."""

    return openstack.connect(
        cloud=os.getenv("OS_CLOUD", "envvars"),
        compute_api_version="2.74",
    )


def ensure_positive_size(size_gb: int) -> None:
    """Проверяет, что размер тома положительный."""

    if size_gb <= 0:
        raise ValueError("Размер тестового тома должен быть больше нуля")


def find_image(conn: openstack.connection.Connection, name_or_id: str):
    image = conn.compute.find_image(name_or_id, ignore_missing=True)
    if image:
        return image
    return conn.compute.get_image(name_or_id)


def find_flavor(conn: openstack.connection.Connection, name_or_id: str):
    flavor = conn.compute.find_flavor(name_or_id, ignore_missing=True)
    if flavor:
        return flavor
    return conn.compute.get_flavor(name_or_id)


def find_network(conn: openstack.connection.Connection, name_or_id: str):
    network = conn.network.find_network(name_or_id, ignore_missing=True)
    if network:
        return network
    return conn.network.get_network(name_or_id)


def find_security_group(
    conn: openstack.connection.Connection, name_or_id: str
):
    sg = conn.network.find_security_group(name_or_id, ignore_missing=True)
    if sg:
        return sg
    return conn.network.get_security_group(name_or_id)


# ===== Основные шаги =====


def create_test_volume(
    conn: openstack.connection.Connection,
    args: argparse.Namespace,
    cleanup: CleanupManager,
    summary: dict,
):
    """Создаёт тестовый том и ждёт статуса available."""

    volume = conn.block_storage.create_volume(
        name=f"{args.server_name}-test-volume",
        size=args.volume_size,
        volume_type=args.volume_type,
    )
    volume = conn.block_storage.wait_for_status(
        volume,
        status="available",
        failures=["error"],
    )

    summary["volume"] = {
        "id": volume.id,
        "status": volume.status,
        "size": volume.size,
        "type": volume.volume_type,
    }

    cleanup.add(
        "delete test volume",
        lambda vol_id=volume.id: delete_volume(conn, vol_id),
    )
    return volume


def delete_volume(conn: openstack.connection.Connection, volume_id: str) -> None:
    conn.block_storage.delete_volume(volume_id, ignore_missing=True)
    with suppress(os_exc.ResourceNotFound):
        conn.block_storage.wait_for_delete(volume_id)


def create_test_server(
    conn: openstack.connection.Connection,
    args: argparse.Namespace,
    image,
    flavor,
    network,
    security_group,
    cleanup: CleanupManager,
    summary: dict,
):
    """Создаёт тестовую ВМ и ждёт её перехода в статус ACTIVE."""

    server_kwargs = dict(
        name=args.server_name,
        image_id=image.id,
        flavor_id=flavor.id,
        networks=[{"uuid": network.id}],
        security_groups=[{"name": security_group.name}],
        key_name=args.key_name,
    )
    if args.availability_zone:
        server_kwargs["availability_zone"] = args.availability_zone

    server = conn.compute.create_server(**server_kwargs)
    server = conn.compute.wait_for_server(
        server,
        status="ACTIVE",
        failures=["ERROR"],
    )

    summary["server"] = {
        "id": server.id,
        "status": server.status,
        "addresses": server.addresses,
    }

    cleanup.add(
        "delete test server",
        lambda server_id=server.id: delete_server(conn, server_id),
    )
    return server


def delete_server(conn: openstack.connection.Connection, server_id: str) -> None:
    conn.compute.delete_server(server_id, ignore_missing=True)
    with suppress(os_exc.ResourceNotFound):
        conn.compute.wait_for_delete(server_id)


def attach_floating_ip(
    conn: openstack.connection.Connection,
    server,
    args: argparse.Namespace,
    cleanup: CleanupManager,
    summary: dict,
):
    """Создаёт floating IP, назначает на сервер и проверяет привязку."""

    if not args.floating_network:
        raise ValueError(
            "Для проверки floating IP необходимо указать --floating-network"
        )

    floating_net = find_network(conn, args.floating_network)
    floating_ip = conn.network.create_ip(
        floating_network_id=floating_net.id,
        description=f"service-check for {args.server_name}",
    )

    def cleanup_fip(
        server_id: str = server.id,
        address: str = floating_ip.floating_ip_address,
        fip_id: str = floating_ip.id,
    ) -> None:
        with suppress(Exception):
            conn.compute.remove_floating_ip_from_server(server_id, address)
        conn.network.delete_ip(fip_id, ignore_missing=True)

    cleanup.add("delete floating IP", cleanup_fip)

    conn.compute.add_floating_ip_to_server(
        server,
        floating_ip.floating_ip_address,
    )

    floating_ip = conn.network.get_ip(floating_ip.id)
    if not floating_ip.fixed_ip_address or not floating_ip.port_id:
        raise RuntimeError("Floating IP не привязался к серверу")

    summary["floating_ip"] = {
        "id": floating_ip.id,
        "address": floating_ip.floating_ip_address,
        "fixed_address": floating_ip.fixed_ip_address,
        "port_id": floating_ip.port_id,
    }

    return floating_ip


def create_snapshot(
    conn: openstack.connection.Connection,
    volume,
    cleanup: CleanupManager,
    summary: dict,
):
    """Создаёт снепшот тестового тома и ждёт статуса available."""

    snapshot = conn.block_storage.create_snapshot(
        volume_id=volume.id,
        name=f"snapshot-{volume.name}",
        force=True,
    )
    snapshot = conn.block_storage.wait_for_status(
        snapshot,
        status="available",
        failures=["error"],
    )

    summary["snapshot"] = {
        "id": snapshot.id,
        "status": snapshot.status,
        "size": snapshot.size,
    }

    cleanup.add(
        "delete snapshot",
        lambda snap_id=snapshot.id: conn.block_storage.delete_snapshot(
            snap_id, ignore_missing=True
        ),
    )
    return snapshot


def record_console_output(
    conn: openstack.connection.Connection,
    server,
    summary: dict,
) -> None:
    """Запрашивает консольный вывод и сохраняет его в сводку."""

    output = conn.compute.get_server_console_output(server) or ""
    summary["console_output"] = {
        "length": len(output),
        "preview": output[-2000:] if len(output) > 2000 else output,
    }


# ===== Точка входа =====


def main() -> int:
    args = parse_args()
    ensure_positive_size(args.volume_size)

    conn = connect()
    summary: dict = {"status": "ok"}
    cleanup = CleanupManager()
    exit_code = 0

    try:
        image = find_image(conn, args.image)
        flavor = find_flavor(conn, args.flavor)
        network = find_network(conn, args.network)
        security_group = find_security_group(conn, args.security_group)

        volume = create_test_volume(conn, args, cleanup, summary)
        server = create_test_server(
            conn, args, image, flavor, network, security_group, cleanup, summary
        )

        if args.with_floating_ip:
            attach_floating_ip(conn, server, args, cleanup, summary)

        if args.with_snapshot:
            create_snapshot(conn, volume, cleanup, summary)

        if args.with_console:
            record_console_output(conn, server, summary)

    except os_exc.ResourceNotFound as exc:
        summary["status"] = "error"
        summary["error"] = f"Ресурс не найден: {exc}"
        exit_code = 1
    except os_exc.HttpException as exc:
        summary["status"] = "error"
        summary["error"] = f"HTTP error: {exc}"
        exit_code = 1
    except Exception as exc:  # pragma: no cover - для непредвиденных ошибок
        summary["status"] = "error"
        summary["error"] = str(exc)
        exit_code = 1
    finally:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        cleanup.run()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
