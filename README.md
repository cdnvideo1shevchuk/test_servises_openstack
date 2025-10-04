# Скрипты для ручных проверок OpenStack

В репозитории два скрипта:

* `create_vm.py` — быстрая обёртка, создающая тестовую ВМ из заранее заданных
  параметров.
* `check_services.py` — сценарий проверки базовых сервисов (Keystone, Cinder,
  Nova) и, при желании, дополнительных шагов (floating IP, снапшот, консоль).

## Требования

* Python 3.8+
* Установленный клиент OpenStack для Ubuntu (`python3-openstackclient`). Он
  подтягивает и сам CLI, и библиотеку [`openstacksdk`](https://docs.openstack.org/openstacksdk/latest/),
  которую использует скрипт.
* Настроенные переменные окружения OpenStack (`OS_`), например через
  `source openrc_*.sh`

## Подготовка

1. Убедись, что значения констант в `openstack_utils.py` соответствуют твоей
   среде:
   * `IMAGE_ID` — UUID образа для корневого тома
   * `NETWORK_NAME` — имя сети, куда подключить ВМ
   * `KEY_NAME` — имя загруженного в OpenStack SSH-ключа
   * `SECURITY_GROUP_ID` — UUID security group
   * `SERVER_NAME_PREFIX` — префикс имени сервера по умолчанию
2. Установи зависимости. Для Ubuntu 22.04+ достаточно установить официальный
   пакет клиента:

   ```bash
   sudo apt install python3-openstackclient
   ```

   Если хочется управлять окружением через `pip`, можно поставить SDK
   непосредственно:

   ```bash
   pip install --user openstacksdk
   ```

## create_vm.py

### Использование

```bash
python3 create_vm.py FLAVOR VOLUME_TYPE SIZE_GB [HOST] [--name NAME]
```

* `FLAVOR` — имя или ID нужного flavor, например `amd-1-1`
* `VOLUME_TYPE` — тип корневого тома (`SSD`, `NVME`, `HDD`, `SSD-MA` и т.д.)
* `SIZE_GB` — размер корневого тома в гигабайтах
* `HOST` *(опционально)* — compute-нода; можно указать короткое имя (`o2n40`),
  домен добавится автоматически
* `--name` *(опционально)* — пользовательское имя сервера

Пример:

```bash
python3 create_vm.py amd-1-1 SSD 20 o2n9 --name shevchuk_auto
```

### Вывод

При успешном выполнении скрипт печатает итоговую информацию о ресурсе:

```
OK
id=<uuid>
name=<vm-name>
hypervisor=<fqdn>
addresses={<network-info>}
volume_id=<volume-uuid>
```

В случае ошибки сообщение будет выведено в STDERR, а код возврата будет > 0.

## check_services.py

Сценарий автоматизирует цепочку действий, которые обычно выполняешь вручную
при проверке сервисов после аварий: запрос токена в Keystone, создание и
подготовка томов в Cinder, развёртывание и миграцию ВМ в Nova. Скрипт
автоматически очищает все созданные ресурсы.

### Использование

```
python3 check_services.py FLAVOR ROOT_VOLUME_TYPE ROOT_SIZE_GB [HOST] [опции]
```

Основные аргументы совпадают с `create_vm.py`. Дополнительные флаги:

* `--data-volume-type TYPE` и `--data-volume-size SIZE` — контролируют
  параметры дополнительного тома, который будет подключён к ВМ для проверки
  Cinder.
* `--skip-live-migration` — отключает live migration.
* `--migration-target HOST` — явно задаёт целевой hypervisor для миграции.
* `--with-floating-ip` — проверяет Neutron: создаёт floating IP и назначает его
  серверу.
* `--with-snapshot` — создаёт снапшот дополнительного тома.
* `--with-console` — запрашивает консольный вывод ВМ (первые 50 строк).
* `--json` — печатает сводку в JSON.

### Пример запуска

```
python3 check_services.py amd-1-1 SSD 20 o2n9 --with-floating-ip --with-console
```

### Вывод

После выполнения скрипт выводит таблицу (или JSON при `--json`) с этапами
проверки, статусами и временем выполнения. При любой ошибке команда завершается
с кодом `1`, но перед этим всё созданное будет удалено.
