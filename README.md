# create_vm.py

Скрипт `create_vm.py` упрощает создание виртуальной машины в OpenStack: он
создаёт корневой том из заранее выбранного образа, поднимает сервер и выводит
основные параметры готовой ВМ.

## Требования

* Python 3.8+
* Библиотека [`openstacksdk`](https://docs.openstack.org/openstacksdk/latest/)
* Настроенные переменные окружения OpenStack (`OS_`), например через
  `source openrc_*.sh`

## Подготовка

1. Убедись, что значения констант в начале скрипта соответствуют твоей среде:
   * `IMAGE_ID` — UUID образа для корневого тома
   * `NETWORK_NAME` — имя сети, куда подключить ВМ
   * `KEY_NAME` — имя загруженного в OpenStack SSH-ключа
   * `SECURITY_GROUP_ID` — UUID security group
   * `SERVER_NAME_PREFIX` — префикс имени сервера по умолчанию
2. Установи зависимости:

   ```bash
   pip install openstacksdk
   ```

## Использование

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

## Вывод

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
