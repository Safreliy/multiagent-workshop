const $ = (id) => document.getElementById(id);
const state = {
  cursor: 0, jobId: null, priorJobId: null, priorCandidate: null,
  resultShown: null, nodes: {}, activeWorkers: new Set(), models: [],
  shownUserJobs: new Set(), transitions: 0, lastStage: '', polling: false
};

const layouts = {
  single: {
    title: 'Single agent', desc: 'Один агент исследует план, переписывает SQL и проверяет кандидата.',
    nodes: [['agent','Агент','Гипотеза → проверка','01'],['tools','PostgreSQL','EXPLAIN · статистика','02'],['check','Валидатор','Эквивалентность и замеры','03']]
  },
  orchestrator: {
    title: 'Оркестратор', desc: 'Центр распределяет задания специалистам и собирает их результаты.',
    nodes: [['orchestrator','Оркестратор','Управление','01'],['specialists','Специалисты','План · SQL · статистика','02'],['check','Аудитор','Проверка','03']]
  },
  swarm: {
    title: 'Swarm с handoff', desc: 'Специалисты передают друг другу управление и компактное состояние.',
    nodes: [['plan_analyst','План','EXPLAIN','01'],['stats_analyst','Статистика','Индексы','02'],['query_rewriter','SQL','Кандидат','03'],['auditor','Аудитор','Проверка','04']]
  },
  board_swarm: {
    title: 'Параллельный swarm', desc: 'Три исследователя работают одновременно, публикуют гипотезы и артефакты на общей доске.',
    nodes: [['plan_analyst','План','EXPLAIN','01'],['stats_analyst','Статистика','Индексы','02'],['query_rewriter','SQL','Переписывание','03'],['shared_board','Общая доска','Факты · SQL','↗'],['synthesis_rewriter','Синтез','Сбор кандидатов','04'],['auditor','Аудитор','Выбор и проверка','05']]
  }
};

function nodeId(raw) {
  const s = String(raw || '').toLowerCase().replaceAll(' ', '_');
  if (s.includes('plan')) return 'plan_analyst';
  if (s.includes('stat')) return 'stats_analyst';
  if (s.includes('synth')) return 'synthesis_rewriter';
  if (s.includes('audit')) return 'auditor';
  if (s.includes('rewrit') || s.includes('query')) return 'query_rewriter';
  return s;
}

function renderGraph(mode) {
  const layout = layouts[mode] || layouts.single;
  $('graph-title').textContent = layout.title;
  $('graph-desc').textContent = layout.desc;
  $('graph').className = 'graph ' + mode;
  $('graph').replaceChildren();
  state.nodes = {};
  state.activeWorkers.clear();
  for (const [id, title, description, icon] of layout.nodes) {
    const node = document.createElement('div');
    node.className = 'node' + (id === 'shared_board' ? ' board' : '');
    node.dataset.icon = icon;
    const name = document.createElement('b'); name.textContent = title;
    const detail = document.createElement('span'); detail.textContent = description;
    node.append(name, detail); $('graph').append(node);
    state.nodes[id] = node;
  }
  $('board-panel').hidden = mode !== 'board_swarm';
}

function activate(raw, on=true) {
  let id = nodeId(raw);
  if (!state.nodes[id] && state.nodes.specialists) id = 'specialists';
  const node = state.nodes[id];
  if (!node) return;
  if ($('architecture').value !== 'board_swarm' && on) {
    Object.values(state.nodes).forEach((n) => n.classList.remove('active'));
  }
  node.classList.toggle('active', on);
  if (on) node.classList.add('done');
}

function transition(label) {
  const chip = document.createElement('span');
  chip.className = 'transition'; chip.textContent = label;
  $('transitions').append(chip);
  while ($('transitions').children.length > 14) $('transitions').firstChild.remove();
  $('transitions').scrollTop = $('transitions').scrollHeight;
}

function clock(value) {
  const s = Math.max(0, Math.floor(Number(value) || 0));
  return String(Math.floor(s / 60)).padStart(2,'0') + ':' + String(s % 60).padStart(2,'0');
}

function addBubble(kind, title, detail, elapsed, cls) {
  if ($('feed').querySelector('.welcome')) $('feed').replaceChildren();
  const row = document.createElement('div'); row.className = 'event ' + (cls || '');
  const time = document.createElement('div'); time.className = 'event-time'; time.textContent = elapsed == null ? '' : clock(elapsed);
  const bubble = document.createElement('div'); bubble.className = 'bubble';
  const label = document.createElement('div'); label.className = 'kind'; label.textContent = kind;
  const heading = document.createElement('b'); heading.textContent = title;
  bubble.append(label, heading);
  if (detail) {
    const pre = document.createElement('pre'); pre.textContent = detail;
    bubble.append(pre);
  }
  row.append(time, bubble); $('feed').append(row);
  while ($('feed').children.length > 130) $('feed').firstChild.remove();
  $('feed').scrollTop = $('feed').scrollHeight;
  return bubble;
}

function addUser(sql, instruction, jobId) {
  if (state.shownUserJobs.has(jobId)) return;
  state.shownUserJobs.add(jobId);
  const heading = instruction ? instruction : 'Оптимизируй этот SQL';
  addBubble('ВЫ', heading, sql, null, 'user');
}

function addBoard(event) {
  $('board-panel').hidden = false;
  const board = $('board');
  const placeholder = board.querySelector('.muted');
  if (placeholder) placeholder.remove();
  const card = document.createElement('div'); card.className = 'board-item';
  const title = document.createElement('strong');
  title.textContent = 'r' + (event.revision ?? '?') + ' · ' + (event.author || 'агент') + ' / ' + (event.kind || 'факт');
  const summary = document.createElement('p'); summary.textContent = event.text || 'Новый артефакт';
  card.append(title, summary);
  if (event.artifact_id) {
    const open = document.createElement('button'); open.type = 'button'; open.className = 'text-button';
    open.textContent = 'Открыть SQL · ' + event.artifact_id;
    open.addEventListener('click', async () => {
      try {
        const data = await jsonFetch('/api/artifact?id=' + encodeURIComponent(event.artifact_id));
        const bubble = addBubble('АРТЕФАКТ', event.artifact_id + ' / ' + (event.author || 'агент'), data.sql, null, '');
        const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'copy'; copy.textContent = 'Копировать SQL';
        copy.addEventListener('click', () => navigator.clipboard.writeText(data.sql).then(() => {copy.textContent = 'Скопировано';}));
        bubble.append(copy);
      } catch (error) { $('message').textContent = error.message; }
    });
    card.append(open);
  }
  board.append(card);
  while (board.children.length > 12) board.firstChild.remove();
  board.scrollTop = board.scrollHeight;
  $('board-revision').textContent = 'r' + (event.revision ?? '?');
  activate('shared_board');
}

function addEvent(event) {
  const labels = {
    architecture_start:['СИСТЕМА','Поиск запущен'],
    llm_start:['LLM','Формирует следующий шаг'],
    llm_end:['АГЕНТ','Ответ модели'],
    tool_start:['ИНСТРУМЕНТ',event.name || 'SQL tool'],
    tool_end:['РЕЗУЛЬТАТ ИНСТРУМЕНТА',event.name || 'SQL tool'],
    worker_start:['АГЕНТ',String(event.worker || 'специалист') + ' начал работу'],
    worker_end:['АГЕНТ',String(event.worker || 'специалист') + ' завершил работу'],
    handoff:['HANDOFF',String(event.source || '?') + ' → ' + String(event.destination || '?')],
    board_post:['ОБЩАЯ ДОСКА',String(event.author || 'агент') + ' опубликовал ' + String(event.kind || 'артефакт')],
    board_read:['ОБЩАЯ ДОСКА',String(event.reader || 'агент') + ' прочитал доску'],
    run_end:['СИСТЕМА','Работа агентов завершена'],
    run_error:['ОШИБКА','Пайплайн остановился'],
    tool_error:['ОШИБКА',event.name || 'Инструмент'],
    llm_error:['ОШИБКА','Запрос модели завершился с ошибкой']
  };
  const info = labels[event.event];
  if (!info) return;
  addBubble(info[0], info[1], event.text || '', event.elapsed_s,
            event.event.includes('error') ? 'error' : '');
  if (event.event === 'board_post') addBoard(event);
  if (event.event === 'worker_start') {
    activate(event.worker); state.activeWorkers.add(nodeId(event.worker));
    transition('Старт: ' + event.worker);
  } else if (event.event === 'worker_end') {
    if ($('architecture').value === 'board_swarm') activate(event.worker, false);
    else activate('orchestrator');
    state.activeWorkers.delete(nodeId(event.worker));
    transition('Готово: ' + event.worker);
  } else if (event.event === 'handoff') {
    activate(event.destination); transition(event.source + ' → ' + event.destination);
  } else if (event.event === 'tool_start') {
    if ($('architecture').value === 'single') activate('tools');
    transition(event.name || 'tool');
  } else if (event.event === 'tool_end' && $('architecture').value === 'single') activate('agent');
  else if (event.event === 'architecture_start') activate($('architecture').value === 'single' ? 'agent' : $('architecture').value === 'orchestrator' ? 'orchestrator' : 'plan_analyst');
  else if (event.event === 'run_end') activate('check');
  if (event.event === 'run_end' && event.metrics) setMetrics(event.metrics);
}

function setMetrics(metrics) {
  const total = Number(metrics.input_tokens || 0) + Number(metrics.output_tokens || 0);
  $('tokens').textContent = metrics.input_tokens == null || metrics.output_tokens == null ? '—' : total.toLocaleString('ru-RU');
  $('llm-calls').textContent = metrics.llm_calls ?? '—';
  $('tool-calls').textContent = metrics.tool_calls ?? '—';
  const priced = state.models.find((model) => model.id === $('model').value)?.budget_kind === 'usd';
  $('cost').textContent = !priced || metrics.cost_accounted_usd == null ? '—' : '$' + Number(metrics.cost_accounted_usd).toFixed(3);
}

function resultBubble(result) {
  const speedup = result.speedup ? Number(result.speedup).toFixed(2) + '×' : '—';
  const title = result.correct ? 'SQL проверен · ускорение ' + speedup : 'Кандидат не прошёл проверку';
  const bubble = addBubble('ИТОГ', title, result.error || (result.correct ? 'Результат совпал с исходным запросом на тестовых данных; время измерено парными замерами.' : 'Корректный кандидат не получен.'), null,
                           result.correct ? '' : 'error');
  if (result.candidate_sql) {
    const details = document.createElement('details'); details.className = 'candidate';
    const summary = document.createElement('summary'); summary.textContent = 'Финальный SQL · показать';
    const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'copy'; copy.textContent = 'Копировать SQL';
    copy.addEventListener('click', () => navigator.clipboard.writeText(result.candidate_sql).then(() => {copy.textContent = 'Скопировано';}).catch(() => {copy.textContent = 'Выделите SQL ниже';}));
    const use = document.createElement('button'); use.type = 'button'; use.className = 'copy'; use.textContent = 'Взять как новый baseline';
    use.addEventListener('click', () => {
      $('sql').value = result.candidate_sql; $('instruction').value = '';
      state.priorJobId = null; $('sql').focus(); details.open = true;
      $('message').textContent = 'Следующий прогон сравнит новый SQL с этим кандидатом как baseline.';
    });
    const code = document.createElement('pre'); code.textContent = result.candidate_sql;
    details.append(summary, copy, use, code); bubble.append(details);
  }
}

function setResult(result) {
  if (!result) return;
  $('correct').textContent = result.correct ? 'Да' : result.status === 'completed' ? 'Нет' : '—';
  $('speedup').textContent = result.speedup ? Number(result.speedup).toFixed(2) + '×' : '—';
  if (result.metrics) setMetrics(result.metrics);
  if (result.candidate_sql) state.priorCandidate = result.candidate_sql;
  if (result.status && state.resultShown !== state.jobId) {
    resultBubble(result); state.resultShown = state.jobId;
  }
}

async function jsonFetch(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'HTTP ' + response.status);
  return body;
}

function budgetLabel() {
  const model = state.models.find((m) => m.id === $('model').value);
  if (!model) { $('budget').textContent = '—'; return; }
  $('budget').textContent = model.budget_kind === 'usd'
    ? '$' + Number(model.max_cost_usd).toFixed(2) + ' · ' + model.max_llm_calls + ' вызовов'
    : model.max_llm_calls + ' вызовов · ' + model.max_output_tokens_per_call + ' output/call';
}

function resetResult() {
  for (const id of ['correct','speedup','tokens','llm-calls','tool-calls','cost']) $(id).textContent = '—';
  $('elapsed').textContent = '00:00';
  $('board').replaceChildren();
  const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = 'Публикации появятся при параллельном исследовании.';
  $('board').append(hint); $('board-revision').textContent = 'r0';
  $('transitions').replaceChildren();
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  const requestedJobId = state.jobId;
  try {
    let payload = await jsonFetch('/api/state?after=' + state.cursor);
    if (requestedJobId !== state.jobId) return;
    let job = payload.job;
    if (job && job.id !== state.jobId) {
      state.jobId = job.id; state.cursor = 0; state.resultShown = null;
      $('architecture').value = job.architecture; $('model').value = job.model;
      renderGraph(job.architecture); resetResult(); budgetLabel();
      $('sql').value = job.baseline_sql || $('sql').value;
      addUser(job.baseline_sql, job.instruction, job.id);
      payload = await jsonFetch('/api/state?after=0');
      job = payload.job;
      if (!job || job.id !== state.jobId) return;
    }
    for (const event of payload.events || []) {
      state.cursor = Math.max(state.cursor, Number(event.sequence) || 0);
      addEvent(event); $('elapsed').textContent = clock(event.elapsed_s);
    }
    const active = !!job?.running;
    $('start').disabled = active || !$('model').value;
    $('stop').disabled = !active;
    $('architecture').disabled = active; $('model').disabled = active;
    $('sql').disabled = active; $('instruction').disabled = active;
    $('sample').disabled = active;
    if (job) {
      const stage = job.stage?.stage || 'starting';
      const names = {starting:'Запуск',connecting:'База данных',preparing:'Новая копия БД',agent:'Агенты работают',finished:'Завершено',failed:'Ошибка',stopped:'Остановлено'};
      $('stage').textContent = names[stage] || stage;
      $('stage').className = 'stage ' + (active ? 'running' : stage === 'failed' ? 'failed' : '');
      if (stage !== state.lastStage) {
        state.lastStage = stage;
        if (stage === 'failed' || stage === 'stopped') {
          $('message').textContent = job.stage.error || job.stage.cleanup_error || (stage === 'stopped' ? 'Прогон остановлен' : 'Прогон завершился с ошибкой');
        }
      }
      setResult(job.result);
      if (!active && job.result?.candidate_sql) state.priorJobId = job.id;
    }
  } catch (error) { $('message').textContent = 'Локальный сервер: ' + error.message; }
  finally { state.polling = false; }
}

async function start(event) {
  event.preventDefault();
  $('message').textContent = '';
  const sql = $('sql').value.trim(), instruction = $('instruction').value.trim();
  if (!sql) { $('message').textContent = 'Вставьте SELECT или выберите пример SQL.'; $('sql').focus(); return; }
  try {
    const body = {architecture:$('architecture').value, model:$('model').value,
                  baseline_sql:sql, instruction};
    if (state.priorJobId) body.previous_job_id = state.priorJobId;
    const result = await jsonFetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    state.jobId = result.id; state.cursor = 0; state.resultShown = null; state.lastStage = '';
    renderGraph(body.architecture); resetResult();
    addUser(sql, instruction, result.id);
    $('instruction').value = '';
    await poll();
  } catch (error) { $('message').textContent = error.message; }
}

async function stop() {
  try {
    await jsonFetch('/api/stop', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    await poll();
  } catch (error) { $('message').textContent = error.message; }
}

async function showSchema() {
  try {
    const data = await jsonFetch('/api/schema');
    const dialog = document.createElement('div'); dialog.className = 'schema-dialog';
    const title = document.createElement('h2'); title.textContent = 'Схема тестовой базы';
    const code = document.createElement('pre'); code.textContent = data.schema;
    const close = document.createElement('button'); close.className = 'stop'; close.textContent = 'Закрыть';
    close.addEventListener('click', () => dialog.remove());
    dialog.append(title, code, close); document.body.append(dialog);
  } catch (error) { $('message').textContent = error.message; }
}

async function init() {
  if (new URLSearchParams(location.search).has('embed')) {
    document.body.classList.add('embed');
    document.addEventListener('keydown', (event) => {
      const direction = event.key === 'PageDown' ? 1 : event.key === 'PageUp' ? -1 : 0;
      if (!direction || window.parent === window) return;
      event.preventDefault();
      window.parent.postMessage({type:'deck-nav',direction}, 'http://127.0.0.1:8768');
    });
  }
  renderGraph($('architecture').value);
  $('architecture').addEventListener('change', () => renderGraph($('architecture').value));
  $('model').addEventListener('change', budgetLabel);
  $('composer').addEventListener('submit', start);
  $('stop').addEventListener('click', stop);
  $('sample').addEventListener('click', async () => {
    try { $('sql').value = (await jsonFetch('/api/sample')).sql; $('sql').focus(); state.priorJobId = null; }
    catch (error) { $('message').textContent = error.message; }
  });
  $('schema').addEventListener('click', showSchema);
  try {
    const config = await jsonFetch('/api/config');
    state.models = config.models || [];
    $('model').replaceChildren();
    for (const model of state.models) {
      const option = document.createElement('option'); option.value = model.id;
      option.textContent = model.id + (model.provider === 'direct' ? ' · direct' : ' · OpenRouter');
      $('model').append(option);
    }
    const directDeepSeek = state.models.find((m) => m.provider === 'direct' && /deepseek/i.test(m.id));
    if (directDeepSeek) $('model').value = directDeepSeek.id;
    if (!state.models.length) $('message').textContent = 'В .env нет настроенной модели. Для OpenRouter нужны ключ и ценовой потолок.';
    budgetLabel(); await poll(); setInterval(poll, 1200);
  } catch (error) { $('message').textContent = error.message; }
}

init();
