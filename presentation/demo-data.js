window.DEMO_DATA = {
  "label": "Записанные трассы: Luna на серверном SQL replay и локальный пилот DeepSeek Board Swarm",
  "sources": {
    "traces": "results/luna-proxy-20260921-231311/runs",
    "server": "results/server-replay-20260922-1208/runs/summary.json",
    "board": "results/board-swarm-deepseek-pilot2-20260922"
  },
  "modes": {
    "single": {
      "source": "run-0002-single",
      "traceSha256": "b914f9b333323221e677aaa09076087c6471ba15ae96db45922ab64afbd894cc",
      "candidateId": "luna-single-1",
      "metrics": {
        "serverPairedSpeedup": 2.041,
        "serverBaselineMs": 2266,
        "serverCandidateMs": 1115,
        "serverPairs": 10
      },
      "events": [
        {
          "sequence": 1,
          "t": 0.0,
          "kind": "start",
          "actor": "single",
          "tool": "",
          "summary": "Агент получил задачу сохранить точный результат SQL и ускорить запрос.",
          "evidence": "Один агент ведёт весь цикл."
        },
        {
          "sequence": 6,
          "t": 2.5,
          "kind": "tool",
          "actor": "single",
          "tool": "get_indexes",
          "summary": "Изучает существующие индексы.",
          "evidence": "Диагностика физической схемы; менять индексы в этом профиле нельзя."
        },
        {
          "sequence": 7,
          "t": 2.5,
          "kind": "tool",
          "actor": "single",
          "tool": "get_schema",
          "summary": "Читает схему и связи таблиц.",
          "evidence": "Определяет ключи и кратность соединений."
        },
        {
          "sequence": 8,
          "t": 2.5,
          "kind": "tool",
          "actor": "single",
          "tool": "get_statistics",
          "summary": "Проверяет статистику распределений.",
          "evidence": "Оценивает селективность фильтров."
        },
        {
          "sequence": 20,
          "t": 11.3,
          "kind": "tool",
          "actor": "single",
          "tool": "explain",
          "summary": "Исследует план исходного запроса.",
          "evidence": "Ищет повторяющиеся коррелированные подзапросы."
        },
        {
          "sequence": 21,
          "t": 11.3,
          "kind": "tool",
          "actor": "single",
          "tool": "benchmark",
          "summary": "Измеряет исходное время.",
          "evidence": "До выбора кандидата фиксирует baseline."
        },
        {
          "sequence": 28,
          "t": 33.7,
          "kind": "tool",
          "actor": "single",
          "tool": "submit_candidate",
          "summary": "Проверяет первый вариант переписывания.",
          "evidence": "Детерминированная проверка результата."
        },
        {
          "sequence": 29,
          "t": 42.2,
          "kind": "validation",
          "actor": "single",
          "tool": "submit_candidate",
          "summary": "Первый вариант неэквивалентен.",
          "evidence": "Проверка нашла различия в строках; вариант отклонён."
        },
        {
          "sequence": 33,
          "t": 50.6,
          "kind": "tool",
          "actor": "single",
          "tool": "submit_candidate",
          "summary": "Подаёт исправленный SQL.",
          "evidence": "Агент сохранил кратность и семантику исходного результата."
        },
        {
          "sequence": 34,
          "t": 74.6,
          "kind": "validation",
          "actor": "single",
          "tool": "submit_candidate",
          "summary": "Исправленный вариант прошёл проверку.",
          "evidence": "Результат на тестовой БД совпал; измерено ускорение в исходном запуске."
        },
        {
          "sequence": 38,
          "t": 85.0,
          "kind": "tool",
          "actor": "single",
          "tool": "benchmark",
          "summary": "Дополнительно измеряет кандидата.",
          "evidence": "Пять замеров после успешной проверки."
        },
        {
          "sequence": 39,
          "t": 85.0,
          "kind": "tool",
          "actor": "single",
          "tool": "explain_analyze",
          "summary": "Сверяет фактический план кандидата.",
          "evidence": "Проверяет наблюдаемую работу PostgreSQL."
        },
        {
          "sequence": 46,
          "t": 102.7,
          "kind": "tool",
          "actor": "single",
          "tool": "submit_candidate",
          "summary": "Финальная проверка SQL.",
          "evidence": "Повторная детерминированная валидация."
        },
        {
          "sequence": 47,
          "t": 136.5,
          "kind": "validation",
          "actor": "single",
          "tool": "submit_candidate",
          "summary": "Финальный SQL прошёл проверку на тестовой БД.",
          "evidence": "Этот SQL затем отдельно переизмерен на сервере 10 раз."
        }
      ]
    },
    "orchestrator": {
      "source": "run-0001-orchestrator",
      "traceSha256": "3763296a990242a52443cf1e18241d554c8928c25abf51756169335f8e34ddde",
      "candidateId": "luna-orchestrator-1",
      "metrics": {
        "serverPairedSpeedup": 2.092,
        "serverBaselineMs": 2263,
        "serverCandidateMs": 1079,
        "serverPairs": 10
      },
      "events": [
        {
          "sequence": 1,
          "t": 0.5,
          "kind": "start",
          "actor": "orchestrator",
          "tool": "",
          "summary": "Оркестратор разбивает исследование на роли.",
          "evidence": "Эксперимент допускает только переписывание SQL."
        },
        {
          "sequence": 5,
          "t": 4.8,
          "kind": "worker_start",
          "actor": "plan_analyst",
          "tool": "ask_plan_analyst",
          "summary": "Параллельно запускает аналитика плана.",
          "evidence": "Задача: найти повторяющуюся работу и риски семантики."
        },
        {
          "sequence": 7,
          "t": 4.8,
          "kind": "worker_start",
          "actor": "stats_analyst",
          "tool": "ask_stats_analyst",
          "summary": "Параллельно запускает аналитика статистики.",
          "evidence": "Задача: схема, индексы, селективность."
        },
        {
          "sequence": 13,
          "t": 9.4,
          "kind": "tool",
          "actor": "plan_analyst",
          "tool": "explain",
          "summary": "Аналитик плана читает EXPLAIN.",
          "evidence": "Проверяет коррелированные подзапросы."
        },
        {
          "sequence": 23,
          "t": 9.6,
          "kind": "tool",
          "actor": "stats_analyst",
          "tool": "get_schema",
          "summary": "Аналитик статистики изучает таблицы.",
          "evidence": "Индексы и статистика используются как подсказки для переписывания."
        },
        {
          "sequence": 37,
          "t": 13.9,
          "kind": "tool",
          "actor": "plan_analyst",
          "tool": "explain_analyze",
          "summary": "Аналитик плана запрашивает фактический план.",
          "evidence": "PostgreSQL выполняет исходный запрос; выводы последуют после результата."
        },
        {
          "sequence": 43,
          "t": 22.4,
          "kind": "worker_end",
          "actor": "plan_analyst",
          "tool": "",
          "summary": "Аналитик плана возвращает выводы.",
          "evidence": "В фактическом плане тысячи повторных проходов; оркестратор получает короткое резюме."
        },
        {
          "sequence": 52,
          "t": 42.2,
          "kind": "worker_end",
          "actor": "stats_analyst",
          "tool": "",
          "summary": "Аналитик статистики возвращает выводы.",
          "evidence": "Ограничения индексов и кратности соединений учтены."
        },
        {
          "sequence": 57,
          "t": 48.8,
          "kind": "worker_start",
          "actor": "query_rewriter",
          "tool": "ask_query_rewriter",
          "summary": "Запускает переписывание SQL.",
          "evidence": "Передаёт гипотезу, свидетельства и кандидат через структурированное состояние."
        },
        {
          "sequence": 61,
          "t": 57.6,
          "kind": "tool",
          "actor": "query_rewriter",
          "tool": "benchmark",
          "summary": "Переписчик сравнивает варианты.",
          "evidence": "Общие отфильтрованные заказы и отдельные агрегаты по клиентам."
        },
        {
          "sequence": 73,
          "t": 93.9,
          "kind": "tool",
          "actor": "query_rewriter",
          "tool": "benchmark",
          "summary": "Измеряет улучшенный вариант.",
          "evidence": "Оставляет независимые агрегаты, чтобы не перемножить строки."
        },
        {
          "sequence": 79,
          "t": 108.1,
          "kind": "worker_end",
          "actor": "query_rewriter",
          "tool": "",
          "summary": "Переписчик возвращает лучший кандидат.",
          "evidence": "Краткий отчёт и SQL передаются оркестратору."
        },
        {
          "sequence": 84,
          "t": 117.4,
          "kind": "worker_start",
          "actor": "auditor",
          "tool": "ask_auditor",
          "summary": "Подключает аудитора.",
          "evidence": "Аудитор проверяет NULL, кратность строк, порядок и типы колонок."
        },
        {
          "sequence": 87,
          "t": 121.7,
          "kind": "tool",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Аудитор подаёт итоговый кандидат.",
          "evidence": "Финальная проверка выполняется кодом."
        },
        {
          "sequence": 88,
          "t": 154.9,
          "kind": "validation",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Результат на тестовой БД совпал.",
          "evidence": "Этот кандидат затем отдельно переизмерен на сервере 10 раз."
        },
        {
          "sequence": 93,
          "t": 164.2,
          "kind": "worker_end",
          "actor": "auditor",
          "tool": "",
          "summary": "Аудитор завершает работу.",
          "evidence": "Оркестратор получает проверенный итог."
        }
      ]
    },
    "swarm": {
      "source": "run-0003-swarm",
      "traceSha256": "212c02681bf5d7b55c788b8871a827d00ab03f3ebd535d1c6e9c1e41b511cb56",
      "candidateId": "luna-swarm-1",
      "metrics": {
        "serverPairedSpeedup": 1.907,
        "serverBaselineMs": 2257,
        "serverCandidateMs": 1184,
        "serverPairs": 10
      },
      "events": [
        {
          "sequence": 1,
          "t": 0.1,
          "kind": "start",
          "actor": "plan_analyst",
          "tool": "",
          "summary": "Рой начинает с аналитика плана.",
          "evidence": "Центрального постоянного управляющего нет."
        },
        {
          "sequence": 4,
          "t": 10.2,
          "kind": "tool",
          "actor": "plan_analyst",
          "tool": "explain",
          "summary": "Аналитик читает план исходного SQL.",
          "evidence": "Диагностирует коррелированные подзапросы."
        },
        {
          "sequence": 5,
          "t": 10.2,
          "kind": "tool",
          "actor": "plan_analyst",
          "tool": "explain_analyze",
          "summary": "Запрашивает фактический план.",
          "evidence": "PostgreSQL ещё выполняет запрос; результат появится следующим событием."
        },
        {
          "sequence": 10,
          "t": 13.4,
          "kind": "result",
          "actor": "plan_analyst",
          "tool": "explain_analyze",
          "summary": "Фактический план готов.",
          "evidence": "6 376 строк результата; подзапросы многократно повторяются."
        },
        {
          "sequence": 18,
          "t": 17.5,
          "kind": "handoff",
          "actor": "query_rewriter",
          "tool": "transfer_to_query_rewriter",
          "summary": "Plan Analyst → Query Rewriter.",
          "evidence": "Переданы task, hypothesis, evidence, candidate_sql=null."
        },
        {
          "sequence": 22,
          "t": 29.2,
          "kind": "tool",
          "actor": "query_rewriter",
          "tool": "explain",
          "summary": "Переписчик проверяет свой план.",
          "evidence": "Отфильтрованные заказы вычисляются один раз."
        },
        {
          "sequence": 27,
          "t": 34.4,
          "kind": "tool",
          "actor": "query_rewriter",
          "tool": "explain_analyze",
          "summary": "Измеряет кандидат.",
          "evidence": "Агрегаты разнесены по клиентам без перемножения строк."
        },
        {
          "sequence": 33,
          "t": 45.2,
          "kind": "handoff",
          "actor": "auditor",
          "tool": "transfer_to_auditor",
          "summary": "Query Rewriter → Auditor.",
          "evidence": "Переданы гипотеза, измерения и candidate_sql."
        },
        {
          "sequence": 37,
          "t": 52.2,
          "kind": "tool",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Аудитор проверяет первый вариант.",
          "evidence": "Проверка точного результата выполняется кодом."
        },
        {
          "sequence": 38,
          "t": 60.2,
          "kind": "validation",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Первый вариант отклонён.",
          "evidence": "Обнаружены различия в строках результата."
        },
        {
          "sequence": 42,
          "t": 69.9,
          "kind": "tool",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Аудитор проверяет исправленный SQL.",
          "evidence": "Корректирует семантику и подаёт снова."
        },
        {
          "sequence": 43,
          "t": 103.5,
          "kind": "validation",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Финальный вариант прошёл проверку на тестовой БД.",
          "evidence": "Этот SQL затем отдельно переизмерен на сервере 10 раз."
        }
      ]
    },
    "board_swarm": {
      "source": "run-0001-board_swarm",
      "traceSha256": "0d11f5675cbd81fe7e8cf8bb04b56a46a7a3ddbdb2a32c8a5a9d517008e4ae20",
      "candidateId": "DeepSeek Board Swarm pilot 2",
      "measurement": "Локальный пилот DeepSeek · финальная проверка",
      "metrics": {
        "speedup": 1.981,
        "baselineMs": 3930,
        "candidateMs": 1983
      },
      "events": [
        {
          "sequence": 1,
          "t": 1.8,
          "kind": "start",
          "actor": "board",
          "tool": "",
          "summary": "Три специалиста получают независимые направления поиска.",
          "evidence": "Запуск DeepSeek: план, статистика и переписывание SQL."
        },
        {
          "sequence": 3,
          "t": 1.8,
          "kind": "board_post",
          "actor": "plan_analyst",
          "tool": "",
          "summary": "На доске появляется гипотеза о дорогих узлах плана.",
          "evidence": "Планировщик должен проверить реальные узлы и оценки строк."
        },
        {
          "sequence": 4,
          "t": 1.8,
          "kind": "board_post",
          "actor": "stats_analyst",
          "tool": "",
          "summary": "На доске появляется гипотеза о селективности и индексах.",
          "evidence": "Выводы будут опираться на схему и статистику."
        },
        {
          "sequence": 5,
          "t": 1.8,
          "kind": "board_post",
          "actor": "query_rewriter",
          "tool": "",
          "summary": "Переписчик ставит гипотезу об устранении повторной работы.",
          "evidence": "Потенциальный SQL будет сохранён как отдельный артефакт."
        },
        {
          "sequence": 6,
          "t": 1.9,
          "kind": "worker_start",
          "actor": "stats_analyst",
          "tool": "",
          "summary": "Аналитик статистики начал работу.",
          "evidence": "Три исследователя работают одновременно."
        },
        {
          "sequence": 7,
          "t": 1.9,
          "kind": "worker_start",
          "actor": "plan_analyst",
          "tool": "",
          "summary": "Аналитик плана начал работу.",
          "evidence": "Он изучает фактическую стоимость исходного запроса."
        },
        {
          "sequence": 8,
          "t": 1.9,
          "kind": "worker_start",
          "actor": "query_rewriter",
          "tool": "",
          "summary": "Переписчик начал работу.",
          "evidence": "Он ищет эквивалентный SQL и проверяет кандидатов."
        },
        {
          "sequence": 19,
          "t": 4.3,
          "kind": "board_read",
          "actor": "query_rewriter",
          "tool": "",
          "summary": "Переписчик читает общую доску.",
          "evidence": "Получает три начальные гипотезы."
        },
        {
          "sequence": 25,
          "t": 4.3,
          "kind": "board_read",
          "actor": "plan_analyst",
          "tool": "",
          "summary": "Аналитик плана читает общую доску.",
          "evidence": "Видит начальные гипотезы других ролей."
        },
        {
          "sequence": 40,
          "t": 4.7,
          "kind": "board_read",
          "actor": "stats_analyst",
          "tool": "",
          "summary": "Аналитик статистики читает общую доску.",
          "evidence": "Видит начальные гипотезы других ролей."
        },
        {
          "sequence": 76,
          "t": 46.2,
          "kind": "tool",
          "actor": "plan_analyst",
          "tool": "explain_analyze",
          "summary": "Аналитик плана запрашивает EXPLAIN ANALYZE.",
          "evidence": "Проверяет фактическое выполнение на PostgreSQL."
        },
        {
          "sequence": 78,
          "t": 49.6,
          "kind": "tool",
          "actor": "stats_analyst",
          "tool": "explain",
          "summary": "Аналитик статистики запрашивает EXPLAIN.",
          "evidence": "Сопоставляет план с индексами и селективностью."
        },
        {
          "sequence": 81,
          "t": 56.7,
          "kind": "phase_limit",
          "actor": "plan_analyst",
          "tool": "",
          "summary": "Аналитик плана достиг лимита своей фазы.",
          "evidence": "Он не успел опубликовать итоговое наблюдение на доске."
        },
        {
          "sequence": 86,
          "t": 56.8,
          "kind": "phase_limit",
          "actor": "stats_analyst",
          "tool": "",
          "summary": "Аналитик статистики достиг лимита своей фазы.",
          "evidence": "Его анализ не стал новой записью для остальных."
        },
        {
          "sequence": 92,
          "t": 122.6,
          "kind": "board_post",
          "actor": "query_rewriter",
          "tool": "sql-0004",
          "summary": "Первый измеренный SQL опубликован как артефакт.",
          "evidence": "Локальный benchmark: 2,55 с; эквивалентность ещё не проверена."
        },
        {
          "sequence": 98,
          "t": 169.4,
          "kind": "board_post",
          "actor": "query_rewriter",
          "tool": "sql-0005",
          "summary": "Второй SQL добавлен на доску.",
          "evidence": "Локальный benchmark: 5,10 с; первый вариант быстрее."
        },
        {
          "sequence": 121,
          "t": 274.8,
          "kind": "worker_end",
          "actor": "query_rewriter",
          "tool": "",
          "summary": "Параллельная фаза поиска завершена.",
          "evidence": "Переписчик оставил на доске два SQL-артефакта."
        },
        {
          "sequence": 123,
          "t": 274.9,
          "kind": "board_read",
          "actor": "synthesis_rewriter",
          "tool": "",
          "summary": "Синтезатор читает доску после параллельной фазы.",
          "evidence": "На доске два SQL-артефакта; итоговых находок Plan и Stats нет."
        },
        {
          "sequence": 124,
          "t": 274.9,
          "kind": "worker_start",
          "actor": "synthesis_rewriter",
          "tool": "",
          "summary": "Синтезатор приступает к сборке кандидата.",
          "evidence": "Он может извлечь полные SQL из артефактов по ID."
        },
        {
          "sequence": 127,
          "t": 277.4,
          "kind": "tool",
          "actor": "synthesis_rewriter",
          "tool": "get_artifact",
          "summary": "Синтезатор открывает SQL-артефакт.",
          "evidence": "Общая доска хранит ссылку и краткое резюме, полный SQL — отдельно."
        },
        {
          "sequence": 148,
          "t": 381.1,
          "kind": "board_post",
          "actor": "synthesis_rewriter",
          "tool": "sql-0006",
          "summary": "Синтезатор публикует новый SQL-артефакт.",
          "evidence": "Кандидат измерен; окончательная проверка корректности впереди."
        },
        {
          "sequence": 152,
          "t": 417.0,
          "kind": "board_post",
          "actor": "synthesis_rewriter",
          "tool": "",
          "summary": "На доске зафиксировано ограничение семантики.",
          "evidence": "Поздняя доставка и рискованный поставщик должны относиться к одному заказу."
        },
        {
          "sequence": 153,
          "t": 417.0,
          "kind": "worker_end",
          "actor": "synthesis_rewriter",
          "tool": "",
          "summary": "Синтезатор завершил работу.",
          "evidence": "На доске новый SQL и краткое объяснение риска семантики."
        },
        {
          "sequence": 154,
          "t": 417.0,
          "kind": "board_read",
          "actor": "auditor",
          "tool": "",
          "summary": "Аудитор читает общую доску.",
          "evidence": "Получает кандидатов и ограничения перед финальной проверкой."
        },
        {
          "sequence": 155,
          "t": 417.0,
          "kind": "worker_start",
          "actor": "auditor",
          "tool": "",
          "summary": "Аудитор начал проверку.",
          "evidence": "Он выбирает SQL-артефакт и вызывает детерминированный validator."
        },
        {
          "sequence": 158,
          "t": 419.4,
          "kind": "tool",
          "actor": "auditor",
          "tool": "get_artifact",
          "summary": "Аудитор открывает полный SQL.",
          "evidence": "Код кандидата берётся из артефакта, а не из пересказа на доске."
        },
        {
          "sequence": 164,
          "t": 451.1,
          "kind": "tool",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "Аудитор отправляет кандидат на проверку.",
          "evidence": "PostgreSQL сравнивает результат и измеряет время."
        },
        {
          "sequence": 165,
          "t": 507.7,
          "kind": "validation",
          "actor": "auditor",
          "tool": "submit_candidate",
          "summary": "SQL прошёл проверку эквивалентности.",
          "evidence": "Результат совпал с исходным; после аудита запуск отдельно переизмерил кандидат."
        },
        {
          "sequence": 169,
          "t": 581.3,
          "kind": "board_post",
          "actor": "auditor",
          "tool": "",
          "summary": "Аудитор публикует решение на доске.",
          "evidence": "Итоговый SQL и свидетельства доступны остальным ролям."
        },
        {
          "sequence": 170,
          "t": 581.3,
          "kind": "worker_end",
          "actor": "auditor",
          "tool": "",
          "summary": "Аудитор завершил проверку.",
          "evidence": "Кандидат принят детерминированным validator."
        },
        {
          "sequence": 171,
          "t": 581.3,
          "kind": "run_end",
          "actor": "board",
          "tool": "",
          "summary": "Запуск завершён: правильный SQL и ускорение 1,98×.",
          "evidence": "Финальный локальный замер: 3,93 → 1,98 с; это один пилот DeepSeek."
        }
      ]
    }
  }
};
