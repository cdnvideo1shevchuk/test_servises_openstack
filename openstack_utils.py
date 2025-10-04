"""Вспомогательные утилиты для взаимодействия с OpenStack."""

from __future__ import annotations

import os
from typing import Iterable

import openstack


DEFAULT_VOLUME_FAILURE_STATES = ("error",)
DEFAULT_SERVER_FAILURE_STATES = ("ERROR",)


def ensure_positive_size(size_gb: int) -> None:
    """Проверяет, что размер тома положительный."""

    if size_gb <= 0:
        raise ValueError("Размер тома должен быть больше нуля")


def build_host_fqdn(host: str | None) -> str | None:
    """Возвращает FQDN compute-ноды, если указан короткий hostname."""

    if not host:
        return None
    return host if "." in host else f"{host}.001.gpucloud.ru"


def connect() -> openstack.connection.Connection:
    """Создаёт соединение с OpenStack используя переменные окружения OS_*."""

    return openstack.connect(
        cloud=os.getenv("OS_CLOUD", "envvars"),
        compute_api_version="2.74",
    )


def wait_for_volume(
    conn: openstack.connection.Connection,
    volume,
    *,
    status: str = "available",
    failures: Iterable[str] = DEFAULT_VOLUME_FAILURE_STATES,
):
    """Ожидает, пока том перейдёт в нужное состояние."""

    return conn.block_storage.wait_for_status(
        volume,
        status=status,
        failures=list(failures),
    )


def wait_for_server(
    conn: openstack.connection.Connection,
    server,
    *,
    status: str = "ACTIVE",
    failures: Iterable[str] = DEFAULT_SERVER_FAILURE_STATES,
):
    """Ожидает, пока сервер перейдёт в нужное состояние."""

    return conn.compute.wait_for_server(
        server,
        status=status,
        failures=list(failures),
    )


__all__ = [
    "build_host_fqdn",
    "connect",
    "ensure_positive_size",
    "wait_for_server",
    "wait_for_volume",
]
