#!/usr/bin/env python3
"""Health-check сервисов OpenStack."""

from __future__ import annotations

import json
import sys
import time
from contextlib import suppress
from typing import Dict, List

from openstack_utils import (
    IMAGE_ID,
    KEY_NAME,
    SERVER_NAME_PREFIX,
    StepResult,
    TEST_FLAVOR_NAME,
    TEST_VOLUME_SIZE_GB,
    TEST_VOLUME_TYPE,
    WAIT_INTERVAL,
    WAIT_TIMEOUT,
    connect,
    ensure_flavor,
    ensure_network,
    ensure_security_group,
    wait_for_deletion,
)


def _format_error(prefix: str, exc: Exception) -> str:
    return f"{prefix}: {exc}" if prefix else str(exc)


def check_keystone(conn, context: Dict[str, object]) -> StepResult:
    step_name = "check_keystone"
    start = time.monotonic()
    status = "success"
    message = "Токен Keystone валиден"

    try:
        conn.authorize()
    except Exception as exc:  # pylint: disable=broad-except
        status = "failed"
        message = _format_error("Не удалось авторизоваться", exc)

    duration = time.monotonic() - start
    return StepResult(step=step_name, status=status, duration=duration, message=message)


def create_test_volume(conn, context: Dict[str, object]) -> StepResult:
    step_name = "create_test_volume"
    start = time.monotonic()
    status = "success"
    message = "Том создан"

    try:
        volume = conn.block_storage.create_volume(
            name=f"{SERVER_NAME_PREFIX}-hc-volume",
            size=TEST_VOLUME_SIZE_GB,
            volume_type=TEST_VOLUME_TYPE,
        )
        volume = conn.block_storage.wait_for_status(
            volume,
            status="available",
            failures=["error"],
            wait=WAIT_TIMEOUT,
            interval=WAIT_INTERVAL,
        )
        context["volume"] = volume
        message = f"Том {volume.id} в состоянии available"
    except Exception as exc:  # pylint: disable=broad-except
        status = "failed"
        message = _format_error("Ошибка создания тестового тома", exc)

    duration = time.monotonic() - start
    return StepResult(step=step_name, status=status, duration=duration, message=message)


def create_test_server(conn, context: Dict[str, object]) -> StepResult:
    step_name = "create_test_server"
    start = time.monotonic()
    status = "success"
    message = "Сервер создан"

    server = None
    try:
        flavor = ensure_flavor(conn, TEST_FLAVOR_NAME)
        network = ensure_network(conn)
        security_group = ensure_security_group(conn)

        server = conn.compute.create_server(
            name=f"{SERVER_NAME_PREFIX}-hc-server",
            image_id=IMAGE_ID,
            flavor_id=flavor.id,
            networks=[{"uuid": network.id}],
            key_name=KEY_NAME,
            security_groups=[{"name": security_group.name}],
        )
        server = conn.compute.wait_for_server(
            server,
            status="ACTIVE",
            failures=["ERROR"],
            wait=WAIT_TIMEOUT,
            interval=WAIT_INTERVAL,
        )
        context["server"] = server
        message = f"Сервер {server.id} в состоянии ACTIVE"
    except Exception as exc:  # pylint: disable=broad-except
        status = "failed"
        message = _format_error("Ошибка создания тестового сервера", exc)
        if server is not None:
            with suppress(Exception):
                conn.compute.delete_server(server, ignore_missing=True)
    duration = time.monotonic() - start
    return StepResult(step=step_name, status=status, duration=duration, message=message)


def attach_volume(conn, context: Dict[str, object]) -> StepResult:
    step_name = "attach_volume"
    start = time.monotonic()
    status = "success"
    message = "Том подключён"

    server = context.get("server")
    volume = context.get("volume")
    if not server or not volume:
        duration = time.monotonic() - start
        return StepResult(
            step=step_name,
            status="failed",
            duration=duration,
            message="Нет ресурсов для подключения тома",
        )

    try:
        conn.compute.create_volume_attachment(server, volumeId=volume.id)
        volume = conn.block_storage.wait_for_status(
            volume,
            status="in-use",
            failures=["error"],
            wait=WAIT_TIMEOUT,
            interval=WAIT_INTERVAL,
        )
        context["volume"] = volume
        context["volume_attached"] = True
        message = f"Том {volume.id} подключён к серверу {server.id}"
    except Exception as exc:  # pylint: disable=broad-except
        status = "failed"
        message = _format_error("Ошибка подключения тома", exc)

    duration = time.monotonic() - start
    return StepResult(step=step_name, status=status, duration=duration, message=message)


def live_migrate(conn, context: Dict[str, object]) -> StepResult:
    step_name = "live_migrate"
    start = time.monotonic()
    status = "success"
    message = "Live-migrate завершился"

    server = context.get("server")
    if not server:
        duration = time.monotonic() - start
        return StepResult(
            step=step_name,
            status="failed",
            duration=duration,
            message="Сервер не найден для live-migrate",
        )

    try:
        conn.compute.live_migrate_server(server)
        server = conn.compute.wait_for_server(
            server,
            status="ACTIVE",
            failures=["ERROR"],
            wait=WAIT_TIMEOUT,
            interval=WAIT_INTERVAL,
        )
        context["server"] = server
        message = f"Сервер {server.id} успешно live-migrate"
    except Exception as exc:  # pylint: disable=broad-except
        status = "failed"
        message = _format_error("Live-migrate завершился ошибкой", exc)

    duration = time.monotonic() - start
    return StepResult(step=step_name, status=status, duration=duration, message=message)


def cleanup(conn, context: Dict[str, object]) -> None:
    server = context.get("server")
    volume = context.get("volume")
    should_detach = context.get("volume_attached")

    try:
        if server and volume and should_detach:
            with suppress(Exception):
                conn.compute.detach_volume(server, volume)
                fresh_volume = conn.block_storage.get_volume(volume.id)
                if fresh_volume is not None:
                    conn.block_storage.wait_for_status(
                        fresh_volume,
                        status="available",
                        failures=["error"],
                        wait=WAIT_TIMEOUT,
                        interval=WAIT_INTERVAL,
                    )
        if server:
            with suppress(Exception):
                conn.compute.delete_server(server, ignore_missing=True)
                wait_for_deletion(
                    conn.compute.get_server,
                    server.id,
                    timeout=WAIT_TIMEOUT,
                    interval=WAIT_INTERVAL,
                )
        if volume:
            with suppress(Exception):
                conn.block_storage.delete_volume(volume, ignore_missing=True)
                wait_for_deletion(
                    conn.block_storage.get_volume,
                    volume.id,
                    timeout=WAIT_TIMEOUT,
                    interval=WAIT_INTERVAL,
                )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Ошибка очистки: {exc}", file=sys.stderr)


def main() -> None:
    conn = connect()
    context: Dict[str, object] = {}
    results: List[StepResult] = []

    try:
        for step in (
            check_keystone,
            create_test_volume,
            create_test_server,
            attach_volume,
            live_migrate,
        ):
            result = step(conn, context)
            results.append(result)
            if result.status != "success":
                break
    finally:
        cleanup(conn, context)

    summary = [result.to_dict() for result in results]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
