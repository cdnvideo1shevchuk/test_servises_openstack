#!/usr/bin/env python3
import argparse
import os
import sys
import time
import openstack
from openstack import exceptions as os_exc

# ===== Константы под твою среду =====
IMAGE_ID = "47f3d709-a13b-41e0-b930-634e726bcfe8"   # Ubuntu-24.04-LTS
NETWORK_NAME = "public"
KEY_NAME = "ssh_shevchuk"
SECURITY_GROUP_ID = "f32b1e84-b8e3-44c9-845a-bd242c7707b5"
SERVER_NAME_PREFIX = "shevchuk"

# ===== Аргументы =====
p = argparse.ArgumentParser(
    description="Создать ВМ: FLAVOR VOLUME_TYPE SIZE_GB [HOST]\n"
                "Пример: create_vm.py amd-1-1 SSD 30 o2n40"
)
p.add_argument("flavor", help="Имя/ID flavor, напр. amd-1-1")
p.add_argument("volume_type", help="Тип тома Cinder: SSD | NVME | HDD | SSD-MA")
p.add_argument("size_gb", type=int, help="Размер корневого тома в ГБ")
p.add_argument("host", nargs="?", default=None,
               help="Опционально: o2nX или FQDN, напр. o2n40 или o2n40.001.gpucloud.ru")
p.add_argument("--name", default=None, help="Имя ВМ (опционально)")
args = p.parse_args()

name = args.name or f"{SERVER_NAME_PREFIX}_auto"
host = args.host
if host and "." not in host:
    host = f"{host}.001.gpucloud.ru"

# ===== Подключение =====
# Берёт креды из OS_* после `source openrc_*.sh`
conn = openstack.connect(cloud=os.getenv("OS_CLOUD", "envvars"),
                         compute_api_version="2.74")

def die(msg, code=1):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)

try:
    # сеть, flavor, SG
    net = conn.network.find_network(NETWORK_NAME, ignore_missing=False)
    flv = conn.compute.find_flavor(args.flavor, ignore_missing=False)
    sg = conn.network.get_security_group(SECURITY_GROUP_ID)
    if not sg:
        die(f"Security group {SECURITY_GROUP_ID} не найден")

    # корневой том нужного типа из образа
    vol = conn.block_storage.create_volume(
        name=f"{name}-root",
        size=args.size_gb,
        image_id=IMAGE_ID,
        volume_type=args.volume_type,
    )
    vol = conn.block_storage.wait_for_status(vol, status="available", failures=["error"])
    host_fqdn = None
    if args.host:
        host_fqdn = args.host if "." in args.host else f"{args.host}.001.gpucloud.ru"

    # создание сервера: сеть по UUID, SG по имени (Nova применит к автопорту)
    server_kwargs = dict(
        name=name,
        flavor_id=flv.id,
        block_device_mapping_v2=[{
            "boot_index": 0,
            "uuid": vol.id,
            "source_type": "volume",
            "destination_type": "volume",
            "delete_on_termination": True,
        }],
        networks=[{"uuid": net.id}],
        security_groups=[{"name": sg.name}],
        key_name=KEY_NAME,
    )
    if host_fqdn:
        server_kwargs["availability_zone"] = f"nova:{host_fqdn}"

    srv = conn.compute.create_server(**server_kwargs)
    srv = conn.compute.wait_for_server(srv, status="ACTIVE", failures=["ERROR"])

    # вывод
    srv = conn.compute.get_server(srv.id)
    hyper = getattr(srv, "hypervisor_hostname", None)


    print("OK")
    print(f"id={srv.id}")
    print(f"name={srv.name}")
    print(f"hypervisor=" + (hyper or "(unknown)"))
    print(f"addresses={srv.addresses or {}}")
    print(f"volume_id={vol.id}")

except os_exc.ResourceNotFound as e:
    die(f"Ресурс не найден: {e}")
except os_exc.HttpException as e:
    die(f"HTTP error: {e}")
except Exception as e:
    die(str(e))
