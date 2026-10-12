# Масштабирование интеллекта через мультиагентность — работает ли?

Код и материалы доклада о сравнении агентных архитектур на одной задаче оптимизации PostgreSQL. В одной экспериментальной ячейке модель, инструменты, исходный SQL и данные одинаковы. Итоговые SQL проверяются программно: результат сравнивается с baseline через двусторонний `EXCEPT ALL`, затем запросы измеряются парами с чередованием порядка и прогревом.

| Архитектура | Как работает |
|---|---|
| `single` | Один агент исследует план, статистику, переписывает и проверяет SQL. |
| `orchestrator` | Управляющий агент вызывает специализированных агентов как инструменты. |
| `swarm` | Специалисты передают друг другу управление через handoff. |
| `board_swarm` | Несколько специалистов исследуют гипотезы параллельно и обмениваются артефактами через общую доску. |

Эксперимент включает режимы `query_only`, `indexes`, `hints`, `all`. База для каждой попытки создаётся из фиксированного шаблона. SQL tools не дают изменять данные; физические изменения разрешены только через ограниченные индексные tools соответствующего режима.

## Быстрый старт

Нужны Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/) и Docker Compose. Команды выполняются из корня репозитория:

```bash
cp .env.example .env
uv sync --locked --extra dev
# Укажите OPENROUTER_API_KEY и OPENROUTER_MODELS в .env
uv run benchmark --all --runs 3 --profiles query_only --advisors off --dry-run
uv run benchmark --architecture single --runs 1 --profiles query_only --advisors off
```

В PowerShell замените `cp` на `Copy-Item`. Если `uv` не установлен, сначала установите его или используйте Python-окружение с зависимостями из `pyproject.toml`. `--dry-run` показывает размер матрицы без расходов на LLM. Реальный прогон создаёт локальную БД, вызывает платный API модели и записывает файлы в игнорируемую папку `results/`.

Для полного сравнения трёх исходных архитектур:

```bash
uv run benchmark --architecture single --runs 3 --profiles query_only --advisors off
uv run benchmark --architecture orchestrator --runs 3 --profiles query_only --advisors off
uv run benchmark --architecture swarm --runs 3 --profiles query_only --advisors off
```

`--all` включает также `board_swarm`. Перед большим прогоном задайте денежный предел через `--max-cost-usd` и цены модели в `MODEL_PRICES_JSON`. Профили с индексами и хинтами требуют локальный образ из `db/Dockerfile.v2` с `pg_hint_plan`. Docker Compose использует порт `55433`; если он занят, поменяйте вместе `SQL_AGENT_V2_POSTGRES_PORT` и порт в `BENCHMARK_ADMIN_DSN`.

## Данные последнего эксперимента

[Сводка 3×3](presentation/materials/report.md) содержит последний серверный повтор: три модели × три архитектуры × три финальных SQL, каждый SQL измерен десять раз после завершения работы ИИ. [Сырые пары времени](presentation/materials/samples.jsonl), [параметры и контрольные суммы](presentation/materials/manifest.json) и [27 финальных SQL](data/server-replay/candidates/) позволяют проверить расчёт. Манифест опубликован с относительными путями; сами LLM-трассы и ключи не входят в репозиторий.

Чтобы заново измерить сохранённые SQL на своей машине без LLM, сначала создайте `.env` из `.env.example`. API-ключ для этих команд не нужен. Поднимите фиксированную БД и выполните повтор:

```bash
uv run benchmark --db-only
uv run python -m benchmark.replay --output results/my-replay --blocks 10
```

Этот повтор всё же требует Docker/PostgreSQL, поэтому его времена будут зависеть от вашего железа и нагрузки. Методика описана в [документе эксперимента](presentation/materials/experiment.md). Исходные цены моделей приведены в [расчёте стоимости](presentation/cost-estimate.json); они не являются фактическим счётом провайдера для всех запусков.

Чтобы пересчитать опубликованную таблицу из сохранённых замеров, не запуская БД или LLM:

```bash
uv run python -c "import json; from pathlib import Path; from benchmark.replay import load_sources, summarize; _, candidates = load_sources(); samples = [json.loads(line) for line in Path('presentation/materials/samples.jsonl').read_text(encoding='utf-8').splitlines() if line]; print(json.dumps(summarize(candidates, samples)['per_group'], indent=2))"
```

## Презентация и демо

[Автономный HTML](presentation/multiagent-workshop-standalone.html) можно скачать и открыть одним файлом: слайды, изображения и записанные траектории встроены; сервер и интернет для просмотра не нужны. Живое LLM-демо запускается в серверной версии:

```bash
uv run python -m presentation.serve
```

Откройте `http://127.0.0.1:8768/index.html`. Записанная траектория работает локально без API и БД. Живое демо требует настроенной модели и PostgreSQL. [Источники и дополнительные материалы](presentation/sources.html) опубликованы рядом с презентацией.

Пересобрать автономный файл из исходников: `uv run python presentation/build_standalone.py`.

## Структура

- `agents/`, `tools/` — архитектуры, координация и ограниченные SQL tools;
- `benchmark/` — задача, запуск, проверка, отчёт и повтор SQL;
- `db/` — фиксированная тестовая БД и Docker Compose;
- `tests/` — проверки без платных вызовов LLM;
- `presentation/` — автономные HTML-слайды, записанное и живое демо;
- `data/server-replay/` — SQL финального сравнения.

Проверка кода: `uv sync --extra dev`, затем `uv run pytest -q` и `uv run ruff check .`. Подробности первой версии эксперимента и устройства общей доски находятся в [описании результата](FINAL_EXPERIMENT.md) и [Board Swarm](BOARD_SWARM.md).
