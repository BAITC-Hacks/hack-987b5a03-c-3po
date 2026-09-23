# Проверенные CSV на встроенном датасете

Два независимых запуска `aml-agent-tools` на текущем коде выполнили реальные analytics, tools, создание локального review case, экспорт и `verify_run`. Каждый run завершился с `verification_status=passed`: все 19 проверок пройдены. Три CSV побайтово совпали между запусками.

| Файл | Строк данных | SHA-256 |
|---|---:|---|
| `nodes_roles.csv` | 2 248 | `702a6d4f5c52c723f289f74f9c4082498ad08bfa80f9cbdbdedf87fad6b7a27e` |
| `clusters.csv` | 91 | `84a5aeb4a7829a1990b1031f5a48fe20cc6f48ffd1b6169fe69cabba71e8d04b` |
| `top_nodes.csv` | 20 | `0186a70620ce970e74e087cdda687c5db7b534c494d6da18506caa21645cb432` |

Датасет содержит 2 248 узлов, 3 119 агрегированных направленных рёбер и 4 840 транзакций. В каждом run создан один локальный review case по top-20. Значения GID в CSV сохраняются как `int64`; в API и JSON они передаются десятичными строками.

После установки backend по [README](../README.md) воспроизведите проверенный экспорт из корня репозитория:

```bash
.venv/bin/aml-agent-tools --data data --database var/aml-agent.sqlite3 --artifacts artifacts --top 20
```

В Windows PowerShell используйте:

```powershell
.\.venv\Scripts\aml-agent-tools.exe --data data --database var\aml-agent.sqlite3 --artifacts artifacts --top 20
```

Каждый запуск создаёт отдельную папку `artifacts/<run_id>/` с тремя CSV и `audit.json`. Запуск с теми же данными и ruleset `v1` должен воспроизвести хеши выше. Таблица хешей в [контрольной точке фаз 0–2](PHASES_0_2_RESULTS.md) относится к более раннему состоянию реализации.

Сами CSV не добавлены в Git: `AGENTS.md` запрещает коммит generated artifacts. Их можно получить указанной командой или скачать через UI после успешной независимой проверки.
