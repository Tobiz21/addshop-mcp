# Развёртывание Addshop MCP на Windows

## 1. Установка

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Создайте отдельные каталоги для state, uploads и зашифрованной сессии. Не
размещайте их в Git, общей сетевой папке или каталоге сайта.

## 2. Вход без сохранения пароля

```powershell
.venv\Scripts\python session_login_cli.py `
  --base-url https://SHOP.myaddshop.ru `
  --session-file C:\secure\addshop-mcp\session.dpapi.json
```

Пароль вводится скрыто и после входа удаляется из памяти процесса. Сохраняются
только cookies, зашифрованные Windows DPAPI. Расшифровать их может тот же
Windows-пользователь на том же компьютере. При завершении сессии повторите вход.

## 3. Начальная политика

Начинайте с:

```text
ADDSHOP_ROLE=reader
ADDSHOP_WRITES_ENABLED=0
```

Запустите `addshop_local_doctor`, `addshop_contract_health` и
`addshop_create_snapshot`. После проверки выберите роль `content` или `commerce`
и только затем включите `ADDSHOP_WRITES_ENABLED=1`.

## 4. Подключение MCP

Команда сервера:

```powershell
.venv\Scripts\python server.py
```

Транспорт — stdio. Передайте переменные из `.env.example` средствами MCP-клиента
или системной службы. Не помещайте пароль, cookie или API-токены в конфигурацию.

## 5. Интеграционные секреты

Создавайте отдельные переменные `ADDSHOP_SECRET_*`. В аргумент MCP передается
только ссылка `env:ADDSHOP_SECRET_NAME`. Само значение не попадает модели.

## 6. Production-проверка

1. `addshop_local_doctor` возвращает `ready=true`.
2. `addshop_contract_health` возвращает `all_ok=true`.
3. `addshop_write_safety_status` показывает валидную audit chain.
4. Создан snapshot с SHA-256.
5. Проверена роль и ограничение скорости.
6. Первый apply выполняется только на неопубликованном MCP TEST.
7. `python quality_gate.py` завершается с `passed=true` и оценкой не ниже 9,5.

## 7. Аварийная остановка

Установите `ADDSHOP_WRITES_ENABLED=0` и перезапустите MCP-процесс. Чтение,
диагностика, аудит и сравнение снимков продолжат работать.
