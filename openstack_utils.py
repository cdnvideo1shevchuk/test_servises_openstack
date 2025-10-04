"""Общие утилиты для скриптов, работающих с OpenStack."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from typing import Sequence

import openstack
from openstack import connection, exceptions as os_exc

# ===== Константы под твою среду =====
# UUID образа, из которого будет собираться корневой том. Сейчас это Ubuntu 24.04.
IMAGE_ID = "47f3d709-a13b-41e0-b930-634e726bcfe8"
# Название сети, в которую будет подключаться создаваемый порт.
NETWORK_NAME = "public"
# Имя уже загруженного в OpenStack SSH-ключа.
KEY_NAME = "ssh_shevchuk"
# UUID security group, который необходимо применить.
SECURITY_GROUP_ID = "f32b1e84-b8e3-44c9-845a-bd242c7707b5"
# Префикс имени сервера, если пользователь явно не задал имя.
SERVER_NAME_PREFIX = "shevchuk"
# Домен для автоматического дополнения коротких имен compute-нод.
DEFAULT_HOST_DOMAIN = "001.gpucloud.ru"
# Версия API Nova, которая поддерживает live migration и расширенные поля.
COMPUTE_API_VERSION = "2.74"

# Тайм-ауты ожиданий (секунды)
DEFAULT_VOLUME_WAIT_TIMEOUT = 600
DEFAULT_SERVER_WAIT_TIMEOUT = 900
DEFAULT_WAIT_INTERVAL = 5


@dataclass(frozen=True)
class ResolvedResources:
    """Набор заранее найденных ресурсов OpenStack."""

    network_id: str
    flavor_id: str
    security_group_name: str


def connect() -> connection.Connection:
    """Возвращает соединение с OpenStack."""

    return openstack.connect(
        cloud=os.getenv("OS_CLOUD", "envvars"),
        compute_api_version=COMPUTE_API_VERSION,
    )


def ensure_positive_size(size_gb: int, *, what: str = "Размер тома") -> None:
    """Проверяет, что указанный размер положительный."""

    if size_gb <= 0:
        raise ValueError(f"{what} должен быть больше нуля")


def build_host_fqdn(host: str | None, *, domain: str = DEFAULT_HOST_DOMAIN) -> str | None:
    """Дополняет короткое имя compute-ноды до FQDN."""

    if not host:
        return None
    return host if "." in host else f"{host}.{domain}"


def resolve_base_resources(
    conn: connection.Connection,
    *,
    network_name: str,
    flavor_name: str,
    security_group_id: str,
) -> ResolvedResources:
    """Находит базовые ресурсы, требуемые для создания сервера."""

    network = conn.network.find_network(network_name, ignore_missing=False)
    flavor = conn.compute.find_flavor(flavor_name, ignore_missing=False)
    security_group = conn.network.get_security_group(security_group_id)
    if not security_group:
        raise os_exc.ResourceNotFound(f"Security group {security_group_id} не найден")
    return ResolvedResources(
        network_id=network.id,
        flavor_id=flavor.id,
        security_group_name=security_group.name,
    )


def wait_for_volume(
    conn: connection.Connection,
    volume,
    *,
    status: str = "available",
    failures: Sequence[str] | None = None,
    interval: int = DEFAULT_WAIT_INTERVAL,
    wait: int = DEFAULT_VOLUME_WAIT_TIMEOUT,
):
    """Дожидается нужного статуса тома."""

    failures = tuple(failures or ("error", "error_deleting"))
    return conn.block_storage.wait_for_status(
        volume,
        status=status,
        failures=list(failures),
        interval=interval,
        wait=wait,
    )


def create_volume(
    conn: connection.Connection,
    *,
    name: str,
    size_gb: int,
    volume_type: str,
    image_id: str | None = None,
    wait: bool = True,
):
    """Создаёт том и при необходимости дожидается готовности."""

    ensure_positive_size(size_gb)
    volume = conn.block_storage.create_volume(
        name=name,
        size=size_gb,
        volume_type=volume_type,
        image_id=image_id,
    )
    if wait:
        volume = wait_for_volume(conn, volume)
    return volume


def delete_volume(
    conn: connection.Connection,
    volume_id: str | None,
    *,
    wait: bool = True,
    interval: int = DEFAULT_WAIT_INTERVAL,
    timeout: int = DEFAULT_VOLUME_WAIT_TIMEOUT,
) -> None:
    """Удаляет том и, при необходимости, ждёт завершения операции."""

    if not volume_id:
        return
    with suppress(os_exc.ResourceNotFound):
        volume = conn.block_storage.get_volume(volume_id)
        if not volume:
            return
        conn.block_storage.delete_volume(volume, ignore_missing=True)
        if wait:
            conn.block_storage.wait_for_delete(volume, interval=interval, wait=timeout)


def wait_for_server(
    conn: connection.Connection,
    server,
    *,
    status: str = "ACTIVE",
    failures: Sequence[str] | None = None,
    interval: int = DEFAULT_WAIT_INTERVAL,
    wait: int = DEFAULT_SERVER_WAIT_TIMEOUT,
):
    """Дожидается нужного статуса сервера."""

    failures = tuple(failures or ("ERROR", "VERIFY_RESIZE"))
    return conn.compute.wait_for_server(
        server,
        status=status,
        failures=list(failures),
        interval=interval,
        wait=wait,
    )


def create_server_from_volume(
    conn: connection.Connection,
    *,
    name: str,
    flavor_id: str,
    volume_id: str,
    network_id: str,
    security_group_name: str,
    key_name: str,
    host_fqdn: str | None = None,
    wait: bool = True,
):
    """Создаёт сервер, бутящийся с указанного тома."""

    server_kwargs = dict(
        name=name,
        flavor_id=flavor_id,
        block_device_mapping_v2=[
            {
                "boot_index": 0,
                "uuid": volume_id,
                "source_type": "volume",
                "destination_type": "volume",
                "delete_on_termination": True,
            }
        ],
        networks=[{"uuid": network_id}],
        security_groups=[{"name": security_group_name}],
        key_name=key_name,
    )
    if host_fqdn:
        server_kwargs["availability_zone"] = f"nova:{host_fqdn}"

    server = conn.compute.create_server(**server_kwargs)
    if wait:
        server = wait_for_server(conn, server)
    return server


def delete_server(conn: connection.Connection, server_id: str | None) -> None:
    """Удаляет сервер и ждёт завершения операции."""

    if not server_id:
        return
    with suppress(os_exc.ResourceNotFound):
        server = conn.compute.get_server(server_id)
        if not server:
            return
        conn.compute.delete_server(server, ignore_missing=True)
        conn.compute.wait_for_delete(server)


def detach_volume(
    conn: connection.Connection,
    *,
    server_id: str,
    volume_id: str,
    wait: bool = True,
) -> None:
    """Отсоединяет том от сервера."""

    server = conn.compute.get_server(server_id)
    if not server:
        return
    attachments = getattr(server, "attachments", []) or []
    attachment_id = None
    for attachment in attachments:
        if attachment.get("volume_id") == volume_id:
            attachment_id = attachment.get("id")
            break
    if attachment_id:
        conn.compute.delete_volume_attachment(attachment_id, server)
    else:
        # Fallback: попросим Nova отсоединить по ID тома.
        conn.compute.detach_volume(server, volume_id)
    if wait:
        volume = conn.block_storage.get_volume(volume_id)
        if volume is not None:
            wait_for_volume(conn, volume)


__all__ = [
    "COMPUTE_API_VERSION",
    "DEFAULT_HOST_DOMAIN",
    "IMAGE_ID",
    "KEY_NAME",
    "NETWORK_NAME",
    "SECURITY_GROUP_ID",
    "SERVER_NAME_PREFIX",
    "ResolvedResources",
    "build_host_fqdn",
    "connect",
    "create_server_from_volume",
    "create_volume",
    "delete_server",
    "delete_volume",
    "detach_volume",
    "ensure_positive_size",
    "resolve_base_resources",
    "wait_for_server",
    "wait_for_volume",
]
