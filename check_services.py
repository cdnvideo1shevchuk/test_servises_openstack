#!/usr/bin/env python3
"""Сценарий проверки базовых сервисов OpenStack (Keystone, Cinder, Nova)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable

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
    delete_server,
    delete_volume,
    detach_volume,
    ensure_positive_size,
    resolve_base_resources,
    wait_for_server,
    wait_for_volume,
)


@dataclass
class StepResult:
    """Итог выполнения отдельного шага."""

    name: str
    success: bool
    duration: float
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "duration": self.duration,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class ResourceState:
    """Следит за созданными ресурсами, чтобы корректно их удалить."""

    root_volume_id: str | None = None
    server_id: str | None = None
    server_name: str | None = None
    data_volume_id: str | None = None
    data_volume_attached: bool = False
    floating_ip_id: str | None = None
    floating_ip_address: str | None = None
    snapshot_id: str | None = None


StepFunc = Callable[[], Any]


def run_step(name: str, func: StepFunc) -> tuple[StepResult, Any]:
    """Запускает отдельный шаг и возвращает результат вместе с данными."""

    start = time.monotonic()
    try:
        data = func()
    except Exception as exc:  # noqa: BLE001 - фиксируем оригинальную ошибку
        duration = time.monotonic() - start
        return (
            StepResult(name=name, success=False, duration=duration, message=str(exc)),
            None,
        )
    else:
        duration = time.monotonic() - start
        message = "OK"
        if isinstance(data, dict) and data.get("__message"):
            message = data["__message"]
        return (
            StepResult(
                name=name,
                success=True,
                duration=duration,
                message=message,
                details=data if isinstance(data, dict) else {},
            ),
            data,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flavor", help="Имя/ID flavor, напр. amd-1-1")
    parser.add_argument("root_volume_type", help="Тип корневого тома: SSD | HDD | NVME")
    parser.add_argument("root_size", type=int, help="Размер корневого тома в ГБ")
    parser.add_argument(
        "host",
        nargs="?",
        default=None,
        help="Опционально: o2nX или FQDN compute-ноды",
    )
    parser.add_argument("--name", default=None, help="Имя тестовой ВМ")
    parser.add_argument(
        "--data-volume-type",
        default=None,
        help="Тип дополнительного тома (по умолчанию как корневой)",
    )
    parser.add_argument(
        "--data-volume-size",
        type=int,
        default=10,
        help="Размер дополнительного тома (ГБ)",
    )
    parser.add_argument(
        "--skip-live-migration",
        action="store_true",
        help="Не выполнять live migration",
    )
    parser.add_argument(
        "--migration-target",
        default=None,
        help="Явно указать compute-ноду для live migration",
    )
    parser.add_argument(
        "--with-floating-ip",
        action="store_true",
        help="Создать и привязать floating IP (Neutron)",
    )
    parser.add_argument(
        "--with-snapshot",
        action="store_true",
        help="Создать снапшот дополнительного тома",
    )
    parser.add_argument(
        "--with-console",
        action="store_true",
        help="Запросить консольный вывод ВМ",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести итоговую сводку в JSON вместо таблицы",
    )
    return parser.parse_args()


def format_summary(results: list[StepResult], *, json_output: bool) -> str:
    if json_output:
        return json.dumps([r.as_dict() for r in results], indent=2, ensure_ascii=False)

    lines = ["\n=== Итоговая сводка ==="]
    header = f"{'Шаг':30} | {'Статус':8} | {'Время':>8} | Сообщение"
    lines.append(header)
    lines.append("-" * len(header))
    for res in results:
        status = "OK" if res.success else "FAIL"
        lines.append(f"{res.name:30} | {status:8} | {res.duration:8.2f} | {res.message}")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    ensure_positive_size(args.root_size, what="Размер корневого тома")
    ensure_positive_size(args.data_volume_size, what="Размер доп. тома")

    server_name = args.name or f"{SERVER_NAME_PREFIX}_hc"
    host_fqdn = build_host_fqdn(args.host)
    migration_target = build_host_fqdn(args.migration_target)

    conn = connect()
    state = ResourceState()
    results: list[StepResult] = []

    try:
        step_result, data = run_step("keystone", conn.authorize)
        results.append(step_result)
        if not step_result.success:
            raise SystemExit(1)

        def resolve_resources() -> dict[str, Any]:
            resolved = resolve_base_resources(
                conn,
                network_name=NETWORK_NAME,
                flavor_name=args.flavor,
                security_group_id=SECURITY_GROUP_ID,
            )
            return {
                "network_id": resolved.network_id,
                "flavor_id": resolved.flavor_id,
                "security_group": resolved.security_group_name,
                "__message": "Ресурсы найдены",
            }

        step_result, data = run_step("lookup", resolve_resources)
        results.append(step_result)
        if not step_result.success:
            raise SystemExit(1)
        network_id = data["network_id"]
        flavor_id = data["flavor_id"]
        security_group_name = data["security_group"]

        def create_root_volume() -> dict[str, Any]:
            volume = create_volume(
                conn,
                name=f"{server_name}-root",
                size_gb=args.root_size,
                volume_type=args.root_volume_type,
                image_id=IMAGE_ID,
            )
            state.root_volume_id = volume.id
            return {
                "volume_id": volume.id,
                "status": volume.status,
            }

        step_result, data = run_step("root-volume", create_root_volume)
        results.append(step_result)
        if not step_result.success:
            raise SystemExit(1)

        def create_server() -> dict[str, Any]:
            server = create_server_from_volume(
                conn,
                name=server_name,
                flavor_id=flavor_id,
                volume_id=state.root_volume_id,
                network_id=network_id,
                security_group_name=security_group_name,
                key_name=KEY_NAME,
                host_fqdn=host_fqdn,
            )
            state.server_id = server.id
            state.server_name = server.name
            server = conn.compute.get_server(server.id)
            return {
                "server_id": server.id,
                "status": server.status,
                "hypervisor": getattr(server, "hypervisor_hostname", None),
            }

        step_result, data = run_step("server", create_server)
        results.append(step_result)
        if not step_result.success:
            raise SystemExit(1)

        def create_data_volume() -> dict[str, Any]:
            volume = create_volume(
                conn,
                name=f"{server_name}-data",
                size_gb=args.data_volume_size,
                volume_type=args.data_volume_type or args.root_volume_type,
            )
            state.data_volume_id = volume.id
            return {"volume_id": volume.id, "status": volume.status}

        step_result, data = run_step("data-volume", create_data_volume)
        results.append(step_result)
        if not step_result.success:
            raise SystemExit(1)

        def attach_volume() -> dict[str, Any]:
            server = conn.compute.get_server(state.server_id)
            attachment = conn.compute.create_volume_attachment(
                server,
                volumeId=state.data_volume_id,
            )
            state.data_volume_attached = True
            wait_for_volume(
                conn,
                conn.block_storage.get_volume(state.data_volume_id),
                status="in-use",
            )
            return {
                "attachment_id": attachment.id,
                "device": getattr(attachment, "device", None),
            }

        step_result, _ = run_step("attach", attach_volume)
        results.append(step_result)
        if not step_result.success:
            raise SystemExit(1)

        if not args.skip_live_migration:
            def live_migrate() -> dict[str, Any]:
                server = conn.compute.get_server(state.server_id)
                initial_host = getattr(server, "OS-EXT-SRV-ATTR:host", None)
                conn.compute.live_migrate_server(server, host=migration_target)
                server = wait_for_server(conn, server)
                new_host = getattr(server, "OS-EXT-SRV-ATTR:host", None)
                return {
                    "from": initial_host,
                    "to": new_host,
                    "__message": "Миграция выполнена" if initial_host != new_host else "Миграция завершена (хост не изменился)",
                }

            step_result, _ = run_step("live-migrate", live_migrate)
            results.append(step_result)
            if not step_result.success:
                raise SystemExit(1)

        if args.with_floating_ip:
            def floating_ip() -> dict[str, Any]:
                server = conn.compute.get_server(state.server_id)
                fip = conn.network.create_ip(floating_network_id=network_id)
                state.floating_ip_id = fip.id
                state.floating_ip_address = fip.floating_ip_address
                conn.compute.add_floating_ip_to_server(
                    server,
                    fip.floating_ip_address,
                )
                server = conn.compute.get_server(server.id)
                return {
                    "floating_ip": fip.floating_ip_address,
                    "addresses": server.addresses,
                }

            step_result, _ = run_step("floating-ip", floating_ip)
            results.append(step_result)
            if not step_result.success:
                raise SystemExit(1)

        if args.with_snapshot:
            def snapshot() -> dict[str, Any]:
                volume = conn.block_storage.get_volume(state.data_volume_id)
                snapshot = conn.block_storage.create_snapshot(
                    name=f"{server_name}-snap",
                    volume_id=volume.id,
                    force=True,
                )
                snapshot = conn.block_storage.wait_for_status(
                    snapshot,
                    status="available",
                    failures=["error"],
                )
                state.snapshot_id = snapshot.id
                return {
                    "snapshot_id": snapshot.id,
                    "status": snapshot.status,
                }

            step_result, _ = run_step("snapshot", snapshot)
            results.append(step_result)
            if not step_result.success:
                raise SystemExit(1)

        if args.with_console:
            def console() -> dict[str, Any]:
                output = conn.compute.get_server_console_output(state.server_id, length=50)
                # Ограничим длину сообщения, чтобы не засорять вывод.
                truncated = (output[:500] + "...") if len(output) > 500 else output
                return {
                    "lines": truncated,
                    "__message": "Получен вывод консоли",
                }

            step_result, _ = run_step("console", console)
            results.append(step_result)
            if not step_result.success:
                raise SystemExit(1)

    except SystemExit:
        # Прерываем сценарий, но всё равно выполняем очистку.
        pass
    finally:
        if state.server_id and state.data_volume_attached and state.data_volume_id:
            with suppress(Exception):
                detach_volume(
                    conn,
                    server_id=state.server_id,
                    volume_id=state.data_volume_id,
                )
        if state.floating_ip_id and state.floating_ip_address and state.server_id:
            with suppress(Exception):
                server = conn.compute.get_server(state.server_id)
                conn.compute.remove_floating_ip_from_server(
                    server,
                    state.floating_ip_address,
                )
            with suppress(Exception):
                conn.network.delete_ip(state.floating_ip_id, ignore_missing=True)
        if state.snapshot_id:
            with suppress(Exception):
                snapshot = conn.block_storage.get_snapshot(state.snapshot_id)
                if snapshot:
                    conn.block_storage.delete_snapshot(snapshot, ignore_missing=True)
                    conn.block_storage.wait_for_delete(snapshot)
        if state.server_id:
            with suppress(Exception):
                delete_server(conn, state.server_id)
        if state.data_volume_id:
            with suppress(Exception):
                delete_volume(conn, state.data_volume_id)
        if state.root_volume_id:
            with suppress(Exception):
                delete_volume(conn, state.root_volume_id)

    print(format_summary(results, json_output=args.json))

    # Если какой-то шаг провалился — вернём ненулевой код возврата.
    if any(not res.success for res in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
