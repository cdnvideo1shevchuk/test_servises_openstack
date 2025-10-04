"""Общие утилиты и константы для сценариев OpenStack."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from typing import Callable, Dict, Optional

import openstack
from openstack import exceptions as os_exc

# ===== Константы под конкретную среду =====
# UUID образа, из которого будут создаваться тестовые ВМ.
IMAGE_ID = "47f3d709-a13b-41e0-b930-634e726bcfe8"
# Название сети для подключения тестовых ресурсов.
NETWORK_NAME = "public"
# Имя загруженного SSH-ключа.
KEY_NAME = "ssh_shevchuk"
# UUID security group.
SECURITY_GROUP_ID = "f32b1e84-b8e3-44c9-845a-bd242c7707b5"
# Префикс имени тестовых серверов.
SERVER_NAME_PREFIX = "shevchuk"

# Дополнительные параметры для health-check скриптов.
TEST_FLAVOR_NAME = "amd-1-1"
TEST_VOLUME_TYPE = "SSD"
TEST_VOLUME_SIZE_GB = 10
WAIT_TIMEOUT = 600
WAIT_INTERVAL = 5


@dataclass
class StepResult:
    """Результат выполнения шага health-check."""

    step: str
    status: str
    duration: float
    message: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["duration"] = round(self.duration, 3)
        return payload


def connect() -> openstack.connection.Connection:
    """Возвращает соединение с OpenStack."""

    return openstack.connect(
        cloud=os.getenv("OS_CLOUD", "envvars"),
        compute_api_version="2.74",
    )


def ensure_network(conn: openstack.connection.Connection):
    """Возвращает объект сети по имени из константы."""

    return conn.network.find_network(NETWORK_NAME, ignore_missing=False)


def ensure_security_group(conn: openstack.connection.Connection):
    """Возвращает security group, выбрасывая исключение при отсутствии."""

    sg = conn.network.get_security_group(SECURITY_GROUP_ID)
    if not sg:
        raise os_exc.ResourceNotFound(
            f"Security group {SECURITY_GROUP_ID} не найден"
        )
    return sg


def ensure_flavor(
    conn: openstack.connection.Connection,
    flavor_name: Optional[str] = None,
):
    """Возвращает flavor по имени/ID."""

    name = flavor_name or TEST_FLAVOR_NAME
    return conn.compute.find_flavor(name, ignore_missing=False)


def wait_for_deletion(
    getter: Callable[..., object],
    *getter_args,
    timeout: int = WAIT_TIMEOUT,
    interval: int = WAIT_INTERVAL,
    **getter_kwargs,
) -> None:
    """Ожидает удаления ресурса."""

    deadline = time.monotonic() + timeout
    while True:
        try:
            resource = getter(*getter_args, **getter_kwargs)
        except os_exc.ResourceNotFound:
            return
        if not resource:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Тайм-аут ожидания удаления ресурса")
        time.sleep(interval)
