# Frontend фазы 5

Интерфейс на React, Vite, TypeScript и Cytoscape.js для встроенного workflow AML Agent. Все результаты run поступают из API фазы 4; браузер не рассчитывает роли, scores, кластеры или результат verification.

## Локальный запуск

Запустите API из корня репозитория по инструкции в [docs/API.md](../docs/API.md):

```bash
python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Затем запустите UI в другом терминале:

```bash
cd frontend
npm ci
npm run dev
```

Откройте <http://127.0.0.1:5173>. Vite проксирует `/health` и `/api` на порт 8000. Команда `npm run build` запускает проверку TypeScript и создаёт статические assets. Production-конфигурация Nginx проксирует те же маршруты в backend-сервис.

## Рабочий процесс

1. Для своих данных выберите один или несколько UTF-8 CSV с колонками `src,dst,date,sum_kzt`, укажите seed-GID и нажмите **Prepare dataset**. Без загрузки используется встроенный датасет `bundled`.
2. Оставьте режим **Demo** и запустите анализ. UI создаст run, откроет безопасный SSE trace и запустит выполнение.
3. Следите за сохранённым status и событиями tools во время детерминированной аналитики, создания case, экспорта и verification.
4. После успешной независимой проверки изучите top-20 оценок, сводки кластеров, направленный ego graph на один или два перехода и evidence узлов.
5. Скачайте проверенные CSV, Excel-отчёт и audit. После завершения используйте **Reset demo**.

Если backend запущен с непустым `OPENAI_API_KEY`, интерфейс также разрешает режим **OpenAI live**. В нём модель выбирает те же контролируемые tools, а роли, scores, review case, exports и verification остаются детерминированными. Для live-run кнопка **New run** очищает только локальную браузерную сессию и не удаляет сохранённый серверный audit.

UI хранит в session storage браузера только ID текущего demo-run. После обновления страницы он повторно подключается к сохранённому status и воспроизводит безопасные события. Во всём браузерном приложении GID остаются десятичными строками, включая поиск узлов и ID элементов Cytoscape.

Контракт HTTP и SSE описан в [docs/API.md](../docs/API.md), модели ответов находятся в [`backend/app/api/schemas.py`](../backend/app/api/schemas.py), а клиентский адаптер — в [`src/api.ts`](src/api.ts).
