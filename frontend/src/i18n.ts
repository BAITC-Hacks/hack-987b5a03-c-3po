export type Language = "en" | "ru" | "kk";

export const LANGUAGE_KEY = "aml-agent-language";

export const languageOptions: { value: Language; label: string }[] = [
  { value: "kk", label: "Қазақша" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
];

export const locale: Record<Language, string> = {
  en: "en-US",
  ru: "ru-RU",
  kk: "kk-KZ",
};

// Keys are the original English interface copy. The fallback keeps unknown
// server-supplied audit text intact instead of changing its meaning.
const translations: Record<string, [string, string]> = {
  "Analyst workspace": ["Рабочее место аналитика", "Талдаушы кеңістігі"],
  Workspace: ["Рабочее место", "Жұмыс кеңістігі"],
  "Workspace sections": ["Разделы рабочего места", "Жұмыс бөлімдері"],
  Overview: ["Обзор", "Шолу"],
  "Run workflow": ["Запуск анализа", "Талдауды іске қосу"],
  "Priority queue": ["Очередь проверки", "Тексеру кезегі"],
  "Network explorer": ["Исследование сети", "Желіні зерттеу"],
  "Review case": ["Дело для проверки", "Тексеру ісі"],
  "Priority structural indicators for analyst review": [
    "Приоритетные структурные признаки для проверки аналитиком",
    "Талдаушы тексеруіне арналған басым құрылымдық белгілер",
  ],
  "Network indicators for analyst review": [
    "Сетевые признаки для проверки аналитиком",
    "Талдаушы тексеруіне арналған желі белгілері",
  ],
  "Структурные индикаторы для проверки аналитиком": [
    "Структурные индикаторы для проверки аналитиком",
    "Талдаушы тексеруіне арналған құрылымдық көрсеткіштер",
  ],
  "Decision support": ["Поддержка решений", "Шешімді қолдау"],
  "Roles and scores are review hypotheses, never findings of guilt.": [
    "Роли и оценки — гипотезы для проверки, а не вывод о виновности.",
    "Рөлдер мен бағалар — тексеру болжамдары, кінә туралы қорытынды емес.",
  ],
  "Bundled July 2026 · ruleset v1": [
    "Данные за июль 2026 · правила v1",
    "2026 жылғы шілде деректері · ережелер v1",
  ],
  "Select language": ["Выбрать язык", "Тілді таңдау"],
  "Network intelligence · July 2026": [
    "Анализ сети · июль 2026",
    "Желі талдауы · 2026 жылғы шілде",
  ],
  "From transfer network": ["От сети переводов", "Аударымдар желісінен"],
  "to review decision.": ["к решению о проверке.", "тексеру шешіміне."],
  "Starting with 81 seed clients, trace July transfers across four hops. See which accounts merit review first, why, and what evidence supports that choice.":
    [
      "Начиная с 81 исходного клиента, проследите июльские переводы на четыре шага. Узнайте, какие счета проверить в первую очередь, почему и на каких данных основан выбор.",
      "81 бастапқы клиенттен басталып, шілдедегі аударымдарды төрт қадамға дейін қадағалаңыз. Қай шоттарды алдымен тексеру керегін, себебін және дәлелін көріңіз.",
    ],
  "Run started": ["Анализ запущен", "Талдау басталды"],
  "Start bundled demo": ["Запустить демо", "Демоны бастау"],
  "Reset demo": ["Сбросить демо", "Демоны қалпына келтіру"],
  "Real analytics, local case creation, independent verification": [
    "Реальный анализ, локальное дело, независимая проверка",
    "Нақты талдау, жергілікті іс, тәуелсіз тексеру",
  ],
  Evidence: ["Данные", "Дәлелдер"],
  Action: ["Действие", "Әрекет"],
  "Action needs attention": ["Требуется внимание", "Назар аудару қажет"],
  "Dismiss error": ["Закрыть ошибку", "Қатені жабу"],
  Retry: ["Повторить", "Қайталау"],
  "Bundled dataset": ["Встроенный набор данных", "Кірістірілген деректер"],
  "Bundled transaction network": [
    "Встроенная сеть переводов",
    "Кірістірілген аударымдар желісі",
  ],
  "July 1–31, 2026 · four-hop sample": [
    "1–31 июля 2026 · выборка в четыре шага",
    "2026 жылғы 1–31 шілде · төрт қадамдық іріктеме",
  ],
  clients: ["клиентов", "клиент"],
  "directed edges": ["направленных связей", "бағытталған байланыс"],
  transactions: ["транзакций", "транзакция"],
  "seed clients": ["исходных клиентов", "бастапқы клиент"],
  "Data limitations recorded": ["Ограничения данных", "Дерек шектеулері"],
  "Run needs input": ["Нужны действия", "Әрекет қажет"],
  "Analyst reviews the ranked targets and their evidence.": [
    "Аналитик изучает рейтинг объектов и данные по ним.",
    "Талдаушы нысандар рейтингі мен олардың деректерін қарайды.",
  ],
  "Review the run events before starting a new run.": [
    "Перед новым запуском изучите журнал событий.",
    "Жаңа іске қосудың алдында оқиғалар журналын қараңыз.",
  ],
  "Review the run events and resolve the reported error.": [
    "Изучите журнал событий и устраните указанную ошибку.",
    "Оқиғалар журналын қарап, көрсетілген қатені түзетіңіз.",
  ],
  "Review the run events before retrying.": [
    "Перед повтором изучите журнал событий.",
    "Қайталаудың алдында оқиғалар журналын қараңыз.",
  ],
  Execution: ["Выполнение", "Орындау"],
  "One run validates the data, computes evidence, creates a case, and checks every export.":
    [
      "Один запуск проверяет данные, рассчитывает показатели, создаёт дело и проверяет каждый файл.",
      "Бір іске қосу деректерді тексеріп, көрсеткіштерді есептеп, іс құрып, әр файлды тексереді.",
    ],
  "Analysis pipeline": ["Этапы анализа", "Талдау кезеңдері"],
  "Ready to analyze": ["Готово к анализу", "Талдауға дайын"],
  "Verified run complete": ["Проверка завершена", "Тексеру аяқталды"],
  "Run stopped": ["Анализ остановлен", "Талдау тоқтатылды"],
  "Analysis in progress": ["Идёт анализ", "Талдау жүріп жатыр"],
  "Validate dataset": ["Проверить данные", "Деректерді тексеру"],
  "Analyze network": ["Проанализировать сеть", "Желіні талдау"],
  "Prioritize targets": ["Расставить приоритеты", "Басымдықтарды анықтау"],
  "Create review case": ["Создать дело", "Тексеру ісін құру"],
  "Verify results": ["Проверить результат", "Нәтижені тексеру"],
  Completed: ["Готово", "Аяқталды"],
  "In progress": ["Выполняется", "Орындалуда"],
  Waiting: ["Ожидание", "Күту"],
  Run: ["Запуск", "Іске қосу"],
  "Start the bundled demo to create a real run.": [
    "Запустите демо для создания реального запуска.",
    "Нақты іске қосуды жасау үшін демоны бастаңыз.",
  ],
  "Safe execution trace": ["Журнал выполнения", "Орындалу журналы"],
  "What the agent did": ["Что сделал агент", "Агент не істеді"],
  Finished: ["Завершено", "Аяқталды"],
  Live: ["Выполняется", "Орындалуда"],
  Idle: ["Ожидание", "Күту"],
  "No events yet": ["Событий пока нет", "Әзірге оқиға жоқ"],
  "Tool activity and verification will appear here when a run starts.": [
    "После запуска здесь появятся действия инструментов и результаты проверки.",
    "Іске қосылғаннан кейін мұнда құрал әрекеттері мен тексеру нәтижелері көрсетіледі.",
  ],
  "Live trace disconnected. Status still refreshes automatically.": [
    "Журнал отключился. Статус по-прежнему обновляется автоматически.",
    "Журнал ажыратылды. Күй автоматты түрде жаңартылып тұрады.",
  ],
  Priorities: ["Приоритеты", "Басымдықтар"],
  "Review targets": ["Объекты проверки", "Тексеру нысандары"],
  "Deterministic ranking with concrete evidence and explicit uncertainty.": [
    "Воспроизводимый рейтинг с конкретными данными и обозначенной неопределённостью.",
    "Нақты деректер мен көрсетілген белгісіздікке негізделген қайталанатын рейтинг.",
  ],
  targets: ["объектов", "нысан"],
  "Awaiting ranking": ["Ожидание рейтинга", "Рейтинг күтілуде"],
  Rank: ["№", "№"],
  "Client GID": ["GID клиента", "Клиент GID"],
  Role: ["Роль", "Рөлі"],
  Priority: ["Приоритет", "Басымдық"],
  "Evidence & warnings": ["Данные и предупреждения", "Деректер мен ескертулер"],
  Details: ["Подробности", "Толығырақ"],
  "View client": ["Открыть клиента", "Клиентті ашу"],
  "The priority queue appears after independent verification passes.": [
    "Очередь появится после успешной независимой проверки.",
    "Кезек тәуелсіз тексеру сәтті аяқталған соң пайда болады.",
  ],
  "Priority and role scores measure rule strength. They are not probabilities of criminal activity.":
    [
      "Оценки приоритета и роли показывают силу правил, а не вероятность преступной деятельности.",
      "Басымдық пен рөл бағалары ережелердің сәйкестігін көрсетеді, қылмыс ықтималдығын емес.",
    ],
  Explore: ["Исследование", "Зерттеу"],
  "Focus on a cluster, then inspect a bounded directed ego graph.": [
    "Выберите кластер и изучите ограниченный ориентированный граф связей.",
    "Кластерді таңдап, шектелген бағытталған байланыс графын қараңыз.",
  ],
  "Community overview": ["Обзор сообществ", "Топтар шолуы"],
  Clusters: ["Кластеры", "Кластерлер"],
  shown: ["показано", "көрсетілді"],
  Pending: ["Ожидание", "Күту"],
  "Cluster summaries appear after independent verification passes.": [
    "Сведения о кластерах появятся после независимой проверки.",
    "Кластер мәліметтері тәуелсіз тексеруден кейін көрсетіледі.",
  ],
  Cluster: ["Кластер", "Кластер"],
  seeds: ["исходных", "бастапқы"],
  "Selected cluster": ["Выбранный кластер", "Таңдалған кластер"],
  "Internal turnover": ["Внутренний оборот", "Ішкі айналым"],
  "Directed ego graph": ["Ориентированный граф", "Бағытталған граф"],
  Client: ["Клиент", "Клиент"],
  "Select a client": ["Выберите клиента", "Клиентті таңдаңыз"],
  "Graph radius": ["Радиус графа", "Граф радиусы"],
  hop: ["шаг", "қадам"],
  hops: ["шага", "қадам"],
  "Search full client GID": [
    "Поиск по полному GID клиента",
    "Клиенттің толық GID-ін іздеу",
  ],
  Search: ["Найти", "Іздеу"],
  "Loading graph…": ["Загрузка графа…", "Граф жүктелуде…"],
  "Arrows follow transfer direction · Click a node for detail": [
    "Стрелки показывают направление переводов · Нажмите на узел для подробностей",
    "Көрсеткілер аударым бағытын көрсетеді · Мәлімет үшін түйінді басыңыз",
  ],
  "View bounded by server": [
    "Область ограничена сервером",
    "Көріністі сервер шектейді",
  ],
  "Loading network slice…": [
    "Загрузка фрагмента сети…",
    "Желі бөлігі жүктелуде…",
  ],
  "A focused view of the network": ["Фрагмент сети", "Желінің бір бөлігі"],
  "Select a ranked target, a cluster GID, or search by full identifier.": [
    "Выберите объект рейтинга или GID кластера либо введите полный идентификатор.",
    "Рейтингтегі нысанды не кластер GID-ін таңдаңыз немесе толық идентификаторды енгізіңіз.",
  ],
  Outcome: ["Результат", "Нәтиже"],
  "From signal to action": ["От сигнала к действию", "Белгіден әрекетке"],
  "The agent creates a local review case, then independently verifies the output bundle.":
    [
      "Агент создаёт локальное дело и независимо проверяет комплект результатов.",
      "Агент жергілікті тексеру ісін құрып, нәтижелер жинағын тәуелсіз тексереді.",
    ],
  Verified: ["Проверено", "Тексерілді"],
  "Verification failed": ["Проверка не пройдена", "Тексеру өтпеді"],
  "Awaiting verification": ["Ожидание проверки", "Тексеру күтілуде"],
  Before: ["До", "Бұрын"],
  "Unreviewed transfer graph": [
    "Необработанная сеть переводов",
    "Тексерілмеген аударымдар желісі",
  ],
  "Thousands of clients and transfers, without an ordered analyst queue or case record.":
    [
      "Тысячи клиентов и переводов без очереди аналитика и записи о деле.",
      "Мыңдаған клиент пен аударым бар, бірақ талдаушы кезегі мен іс жазбасы жоқ.",
    ],
  "Needs triage": ["Нужен разбор", "Іріктеу қажет"],
  After: ["После", "Кейін"],
  "Review case pending": ["Дело пока не создано", "Іс әлі құрылмады"],
  "ranked clients captured for analyst review.": [
    "клиентов рейтинга добавлено для проверки аналитиком.",
    "рейтингтегі клиент талдаушы тексеруі үшін қосылды.",
  ],
  "A local case is created from the ranked target snapshot.": [
    "Локальное дело создаётся по снимку рейтинга объектов.",
    "Жергілікті іс нысандар рейтингінің көшірмесінен құрылады.",
  ],
  "No case yet": ["Дела пока нет", "Әзірге іс жоқ"],
  "Verified export bundle": [
    "Проверенный комплект файлов",
    "Тексерілген файлдар жинағы",
  ],
  "Evidence you can inspect": [
    "Данные для изучения",
    "Қарауға болатын деректер",
  ],
  "Downloads unlock only when the run completes with passed verification.": [
    "Файлы доступны после успешного завершения независимой проверки.",
    "Файлдар тәуелсіз тексеру сәтті аяқталғаннан кейін қолжетімді болады.",
  ],
  "Node assessments": ["Оценки узлов", "Түйін бағалары"],
  "All 2,248 clients": ["Все 2 248 клиентов", "Барлық 2 248 клиент"],
  "Cluster overview": ["Обзор кластеров", "Кластерлер шолуы"],
  "Community evidence": ["Данные сообществ", "Топтар деректері"],
  "Ranked review targets": [
    "Рейтинг объектов проверки",
    "Тексеру нысандары рейтингі",
  ],
  "Audit record": ["Журнал аудита", "Аудит журналы"],
  "Run and verification": ["Запуск и проверка", "Іске қосу мен тексеру"],
  "AML Agent · Analyst decision support · All actions stay local to the review workspace.":
    [
      "AML Agent · Поддержка решений аналитика · Все действия выполняются локально.",
      "AML Agent · Талдаушы шешімін қолдау · Барлық әрекет жергілікті ортада орындалады.",
    ],
  detail: ["подробности", "мәліметтері"],
  "Client evidence": ["Данные клиента", "Клиент деректері"],
  "Close client detail": [
    "Закрыть сведения о клиенте",
    "Клиент мәліметтерін жабу",
  ],
  "Seed client": ["Исходный клиент", "Бастапқы клиент"],
  "Priority score": ["Оценка приоритета", "Басымдық бағасы"],
  "Role match": ["Соответствие роли", "Рөл сәйкестігі"],
  "Rule evidence": ["Основание по правилам", "Ережелер бойынша дәлел"],
  "Observed network": ["Наблюдаемая сеть", "Бақыланған желі"],
  "In degree": ["Входящих связей", "Кіріс байланыстар"],
  "Out degree": ["Исходящих связей", "Шығыс байланыстар"],
  Incoming: ["Поступило", "Келіп түсті"],
  Outgoing: ["Отправлено", "Жіберілді"],
  Depth: ["Глубина", "Тереңдік"],
  Uncertainty: ["Неопределённость", "Белгісіздік"],
  "No node-specific flags recorded.": [
    "Ограничений для этого узла нет.",
    "Бұл түйінге қатысты шектеулер жоқ.",
  ],
  "At the depth boundary, missing outgoing transfers do not establish a terminal role.":
    [
      "На границе глубины отсутствие исходящих переводов не подтверждает конечную роль.",
      "Тереңдік шегінде шығыс аударымдарының көрінбеуі соңғы рөлді растамайды.",
    ],
  "This assessment supports analyst review and is not a finding of wrongdoing.":
    [
      "Эта оценка помогает аналитику и не является выводом о нарушении.",
      "Бұл баға талдаушыға көмектеседі және құқық бұзушылық туралы қорытынды емес.",
    ],
  "Loading client evidence…": [
    "Загрузка данных клиента…",
    "Клиент деректері жүктелуде…",
  ],
  "Directed transfer network around the selected client": [
    "Ориентированная сеть переводов вокруг выбранного клиента",
    "Таңдалған клиент айналасындағы бағытталған аударымдар желісі",
  ],
  Selected: ["Выбран", "Таңдалды"],
  "The request could not be completed.": [
    "Не удалось выполнить запрос.",
    "Сұрау орындалмады.",
  ],
  "The backend is unavailable. Start the Phase 4 API and retry.": [
    "Сервер недоступен. Запустите локальный API и повторите.",
    "Сервер қолжетімсіз. Жергілікті API-ді іске қосып, қайталаңыз.",
  ],
  "The backend did not return JSON for this request.": [
    "Сервер не вернул JSON для этого запроса.",
    "Сервер бұл сұрауға JSON қайтармады.",
  ],
  "The API returned a GID that is not a decimal string.": [
    "API вернул GID в неверном формате.",
    "API GID-ті қате пішімде қайтарды.",
  ],
  "The execution trace could not be loaded.": [
    "Не удалось загрузить журнал выполнения.",
    "Орындалу журналын жүктеу мүмкін болмады.",
  ],
  "Verified assessments are not yet available.": [
    "Проверенные оценки пока недоступны.",
    "Тексерілген бағалар әлі қолжетімсіз.",
  ],
  "The previous run is no longer available. Start a new demo run.": [
    "Предыдущий запуск больше недоступен. Начните новое демо.",
    "Алдыңғы іске қосу қолжетімсіз. Жаңа демоны бастаңыз.",
  ],
  "Enter a full decimal GID. Letters and punctuation are not accepted.": [
    "Введите полный числовой GID без букв и знаков препинания.",
    "Әріптер мен тыныс белгілерінсіз толық сандық GID енгізіңіз.",
  ],
  "Node evidence is available after the run passes verification.": [
    "Данные узла доступны после успешной проверки запуска.",
    "Түйін деректері іске қосу тексеруден өткеннен кейін қолжетімді.",
  ],
  "Verification is still in progress.": [
    "Проверка ещё выполняется.",
    "Тексеру әлі жүріп жатыр.",
  ],
  "API is unavailable. Check the local server and retry.": [
    "API недоступен. Проверьте локальный сервер и повторите.",
    "API қолжетімсіз. Жергілікті серверді тексеріп, қайталаңыз.",
  ],
  created: ["создан", "құрылды"],
  validated: ["данные проверены", "деректер тексерілді"],
  "graph ready": ["граф готов", "граф дайын"],
  analyzed: ["анализ завершён", "талдау аяқталды"],
  clustered: ["кластеры готовы", "кластерлер дайын"],
  classified: ["роли назначены", "рөлдер анықталды"],
  ranked: ["рейтинг готов", "рейтинг дайын"],
  "case created": ["дело создано", "іс құрылды"],
  exported: ["файлы подготовлены", "файлдар дайын"],
  verified: ["проверено", "тексерілді"],
  completed: ["завершено", "аяқталды"],
  failed: ["ошибка", "қате"],
  "verification failed": ["проверка не пройдена", "тексеру өтпеді"],
  "ready for review": ["готово к проверке", "тексеруге дайын"],
  coordinator: ["координатор", "үйлестіруші"],
  consolidator: ["сборщик", "жинақтаушы"],
  distributor: ["распределитель", "таратушы"],
  transit: ["транзит", "транзит"],
  terminal: ["получатель", "алушы"],
  peripheral: ["периферийный", "шеткі"],
  "seed inflow incomplete": [
    "неполный приток к исходному узлу",
    "бастапқы түйінге кіріс толық емес",
  ],
  "depth boundary": ["граница глубины", "тереңдік шегі"],
  "isolated node": ["изолированный узел", "оқшау түйін"],
  "temporal signal unavailable": [
    "временной сигнал недоступен",
    "уақыт белгісі қолжетімсіз",
  ],
  "date only resolution": ["только дата, без времени", "тек күн, уақыт жоқ"],
  "tool started": ["инструмент запущен", "құрал іске қосылды"],
  "tool completed": ["инструмент завершён", "құрал аяқталды"],
  warning: ["предупреждение", "ескерту"],
  "Agent started": ["Агент запущен", "Агент іске қосылды"],
  "inspect dataset": ["проверка данных", "деректерді тексеру"],
  "build graph": ["построение графа", "граф құру"],
  "compute graph features": ["расчёт признаков", "белгілерді есептеу"],
  "cluster network": ["кластеризация сети", "желіні кластерлеу"],
  "assign roles": ["назначение ролей", "рөлдерді анықтау"],
  "rank targets": ["рейтинг объектов", "нысандар рейтингі"],
  "create review case": ["создание дела", "тексеру ісін құру"],
  "export results": ["экспорт результатов", "нәтижелерді экспорттау"],
  "verify run": ["проверка запуска", "іске қосуды тексеру"],
  started: ["запущено", "басталды"],
  "Local review case created": [
    "Локальное дело создано",
    "Жергілікті тексеру ісі құрылды",
  ],
  "Independent verification passed": [
    "Независимая проверка пройдена",
    "Тәуелсіз тексеру өтті",
  ],
  "Run completed after independent verification": [
    "Запуск завершён после независимой проверки",
    "Іске қосу тәуелсіз тексеруден кейін аяқталды",
  ],
  "Run completed after passed verification": [
    "Запуск завершён после проверки",
    "Іске қосу тексеруден кейін аяқталды",
  ],
  "Seed inflows may be incomplete inside the supplied traversal.": [
    "Входящие переводы исходных клиентов могут быть неполными в этой выборке.",
    "Бұл іріктемеде бастапқы клиенттердің кіріс аударымдары толық болмауы мүмкін.",
  ],
  "Depth-4 nodes may be truncated by the four-hop collection boundary.": [
    "Узлы на глубине 4 могут быть обрезаны границей сбора данных.",
    "4-қадамдағы түйіндер дерек жинау шегінде үзілуі мүмкін.",
  ],
  "Transaction dates do not contain intraday ordering.": [
    "Даты транзакций не показывают порядок операций внутри дня.",
    "Транзакция күндері бір күн ішіндегі реттілікті көрсетпейді.",
  ],
  "The dataset contains no identity attributes or ground-truth labels.": [
    "В наборе нет персональных признаков и подтверждённых меток.",
    "Жинақта жеке белгілер немесе расталған белгілер жоқ.",
  ],
  "Multi-seed connected transfer community for analyst review": [
    "Связанная сеть нескольких исходных клиентов для проверки аналитиком",
    "Бірнеше бастапқы клиент байланысқан аударымдар тобы",
  ],
  "Community organized around a distribution pattern": [
    "Сообщество с признаками распределения средств",
    "Қаражат тарату белгілері бар топ",
  ],
  "Community with observed consolidation indicators": [
    "Сообщество с признаками сбора средств",
    "Қаражат жинақтау белгілері бар топ",
  ],
  "Recipient-heavy community with limited visible onward flow": [
    "Сообщество с множеством получателей и ограниченным видимым исходящим потоком",
    "Алушылары көп, көрінетін шығыс ағыны шектеулі топ",
  ],
  "Transfer community without a dominant structural pattern": [
    "Сообщество переводов без преобладающей структуры",
    "Айқын құрылымы жоқ аударымдар тобы",
  ],
  "Depth-4 boundary: 0 visible outgoing edges; role evidence is insufficient.":
    [
      "Граница глубины 4: видимых исходящих связей 0; данных для роли недостаточно.",
      "4-қадам шегі: көрінетін шығыс байланыс 0; рөлді анықтауға дерек жеткіліксіз.",
    ],
  "Seed has 0 visible incoming and 0 outgoing edges; isolated low-evidence candidate.":
    [
      "У исходного узла 0 видимых входящих и исходящих связей; данных мало.",
      "Бастапқы түйінде көрінетін кіріс және шығыс байланыс 0; дерек аз.",
    ],
};

export function t(text: string, language: Language): string {
  if (language === "en") return text;
  const pair = translations[text];
  return pair ? pair[language === "ru" ? 0 : 1] : text;
}

export function translateTrace(text: string, language: Language): string {
  if (language === "en") return text;
  const direct = t(text, language);
  if (direct !== text) return direct;
  const started = text.match(
    /^(inspect_dataset|build_graph|compute_graph_features|cluster_network|assign_roles|rank_targets|create_review_case|export_results|verify_run) started$/,
  );
  if (started) {
    return `${t(started[1].replaceAll("_", " "), language)} ${t("started", language)}`;
  }
  const patterns: [RegExp, (parts: RegExpMatchArray) => [string, string]][] = [
    [
      /^Dataset is valid: ([\d,]+) nodes, ([\d,]+) edges, ([\d,]+) transactions\.$/,
      (m) => [
        `Данные проверены: ${m[1]} узлов, ${m[2]} связей, ${m[3]} транзакций.`,
        `Деректер тексерілді: ${m[1]} түйін, ${m[2]} байланыс, ${m[3]} транзакция.`,
      ],
    ],
    [
      /^Directed graph built with ([\d,]+) nodes and ([\d,]+) edges; ([\d,]+) depth-boundary nodes flagged\.$/,
      (m) => [
        `Построен ориентированный граф: ${m[1]} узлов, ${m[2]} связей; ${m[3]} узлов на границе глубины отмечено.`,
        `Бағытталған граф құрылды: ${m[1]} түйін, ${m[2]} байланыс; тереңдік шегіндегі ${m[3]} түйін белгіленді.`,
      ],
    ],
    [
      /^Computed (\d+) deterministic feature fields\.$/,
      (m) => [
        `Рассчитано ${m[1]} воспроизводимых признаков.`,
        `${m[1]} қайталанатын белгі есептелді.`,
      ],
    ],
    [
      /^Assigned every node to ([\d,]+) reproducible communities\.$/,
      (m) => [
        `Все узлы распределены по ${m[1]} воспроизводимым кластерам.`,
        `Барлық түйін ${m[1]} қайталанатын кластерге бөлінді.`,
      ],
    ],
    [
      /^Assigned one explainable role to ([\d,]+) nodes\.$/,
      (m) => [
        `${m[1]} узлам назначена объяснимая роль.`,
        `${m[1]} түйінге түсіндірілетін рөл берілді.`,
      ],
    ],
    [
      /^Ranked (\d+) targets for analyst review\.$/,
      (m) => [
        `В рейтинг проверки аналитика включено ${m[1]} объектов.`,
        `Талдаушы тексеруінің рейтингіне ${m[1]} нысан енгізілді.`,
      ],
    ],
    [
      /^Created review case ([0-9a-f-]+) with (\d+) targets\.$/,
      (m) => [
        `Создано дело ${m[1]} с ${m[2]} объектами.`,
        `${m[2]} нысаны бар ${m[1]} ісі құрылды.`,
      ],
    ],
    [
      /^Exported and registered (\d+) controlled artifacts\.$/,
      (m) => [
        `Экспортировано и зарегистрировано ${m[1]} контролируемых файла.`,
        `${m[1]} бақыланатын файл экспортталып, тіркелді.`,
      ],
    ],
    [
      /^Verification passed: (\d+) checks completed\.$/,
      (m) => [
        `Проверка пройдена: выполнено ${m[1]} проверок.`,
        `Тексеру өтті: ${m[1]} тексеріс аяқталды.`,
      ],
    ],
    [
      /^Verification failed: (\d+) checks failed\.$/,
      (m) => [
        `Проверка не пройдена: ${m[1]} ошибок.`,
        `Тексеру өтпеді: ${m[1]} қате.`,
      ],
    ],
  ];
  for (const [pattern, translate] of patterns) {
    const match = text.match(pattern);
    if (match) return translate(match)[language === "ru" ? 0 : 1];
  }
  return text;
}

export function translateEvidence(text: string, language: Language): string {
  if (language === "en") return text;
  const direct = t(text, language);
  if (direct !== text) return direct;
  const patterns: [RegExp, (parts: RegExpMatchArray) => [string, string]][] = [
    [
      /^Observed paths from (\d+) seeds; in\/out degree (\d+)\/(\d+); bridge percentile ([\d.]+)% for review\.$/,
      (m) => [
        `Наблюдаемые пути от ${m[1]} исходных узлов; входящие/исходящие связи ${m[2]}/${m[3]}; показатель связности ${m[4]}% для проверки.`,
        `${m[1]} бастапқы түйіннен бақыланған жолдар; кіріс/шығыс байланыстар ${m[2]}/${m[3]}; байланыстылық көрсеткіші ${m[4]}%.`,
      ],
    ],
    [
      /^Observed ([\d,.]+) KZT from (\d+) payers; sent onward ([\d.]+)%; (\d+) seeds upstream\. Consolidation indicator\.$/,
      (m) => [
        `Получено ${m[1]} KZT от ${m[2]} плательщиков; далее отправлено ${m[3]}%; выше по сети ${m[4]} исходных узлов. Признак сбора средств.`,
        `${m[2]} төлеушіден ${m[1]} KZT алынды; әрі қарай ${m[3]}% жіберілді; желі басында ${m[4]} бастапқы түйін бар. Жинақтау белгісі.`,
      ],
    ],
    [
      /^Observed ([\d,.]+) KZT sent to (\d+) recipients and (\d+) payers; fan-out indicator for review\.$/,
      (m) => [
        `${m[1]} KZT отправлено ${m[2]} получателям при ${m[3]} плательщиках; признак распределения для проверки.`,
        `${m[3]} төлеушіден ${m[2]} алушыға ${m[1]} KZT жіберілді; тексеруге арналған тарату белгісі.`,
      ],
    ],
    [
      /^Observed in\/out ([\d,.]+)\/([\d,.]+) KZT; pass-through ([\d.]+); rapid outflow (.+)\.$/,
      (m) => [
        `Входящий/исходящий поток ${m[1]}/${m[2]} KZT; транзитная доля ${m[3]}; быстрый отток ${m[4]}.`,
        `Кіріс/шығыс ағын ${m[1]}/${m[2]} KZT; транзит үлесі ${m[3]}; жылдам шығыс ${m[4]}.`,
      ],
    ],
    [
      /^Observed ([\d,.]+) KZT from (\d+) payers and 0 KZT visible outflow; recipient indicator for review\.$/,
      (m) => [
        `${m[2]} плательщиков отправили ${m[1]} KZT, видимый исходящий поток — 0 KZT; признак получателя для проверки.`,
        `${m[2]} төлеушіден ${m[1]} KZT алынды, көрінетін шығыс ағын — 0 KZT; тексеруге арналған алушы белгісі.`,
      ],
    ],
    [
      /^Observed in\/out degree (\d+)\/(\d+) and flow ([\d,.]+)\/([\d,.]+) KZT; no stronger v1 role indicator\.$/,
      (m) => [
        `Входящие/исходящие связи ${m[1]}/${m[2]}, поток ${m[3]}/${m[4]} KZT; более сильного признака роли v1 нет.`,
        `Кіріс/шығыс байланыстар ${m[1]}/${m[2]}, ағын ${m[3]}/${m[4]} KZT; v1 рөліне күштірек белгі жоқ.`,
      ],
    ],
  ];
  for (const [pattern, translate] of patterns) {
    const match = text.match(pattern);
    if (match) return translate(match)[language === "ru" ? 0 : 1];
  }
  return text;
}
