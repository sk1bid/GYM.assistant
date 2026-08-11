/**
 * Программы тренировок и их настройки.
 *
 * Настройки отдыха — главное, что здесь появилось. В боте эти поля лежали в БД
 * (rest_between_set, circular_rounds, circular_rest_*), читались во время тренировки,
 * но интерфейса к ним не существовало: все тренировались с дефолтными пятью минутами
 * между подходами и не имели способа это изменить. А rest_between_exercise и вовсе
 * клали в FSM и ни разу не читали — отдыха между упражнениями просто не было.
 */
import { api, cached } from './../api.js';
import { go } from './../router.js';
import { confirm, haptic } from './../tg.js';
import { clock, escape, on, onAction, plural, render, same, sheet } from './../ui.js';

export async function programsScreen() {
  // Мгновенная отрисовка из последнего ответа, свежий — следом. См. home.js.
  const known = cached('api/programs');
  if (known) paintPrograms(known.programs);

  const data = await api.programs.list();
  if (!known || !same(known, data)) paintPrograms(data.programs);
}

function paintPrograms(programs) {
  render(`
    <h1>Программы</h1>

    ${programs.length ? '' : `
      <div class="empty">
        <p>Программ пока нет.</p>
        <p class="hint">Программа — это упражнения, разложенные по дням недели.</p>
      </div>
    `}

    ${programs.map((program) => `
      <button class="list-item" data-open="${program.id}">
        <span class="grow">
          <span class="title">${escape(program.name)}</span>
          ${program.active ? '<span class="pill on">активна</span>' : ''}
          <br>
          <span class="sub">${plural(program.filled_days, 'день', 'дня', 'дней')} заполнено</span>
        </span>
        <span class="chev">›</span>
      </button>
    `).join('')}

    <button class="btn secondary mt-4" id="create">Создать программу</button>
  `);

  onAction('[data-open]', (node) => go(`/program/${node.dataset.open}`));
  on('#create', 'click', createProgram);
}

/**
 * Создание программы: сначала ЧТО, потом как назвать.
 *
 * Раньше здесь было голое поле названия и обещание «создадутся все семь дней». Тому,
 * кто знает, что такое сплит, этого хватало; новичок же получал семь строк «отдых»
 * и оставался с ними один на один — он не знает ни какие упражнения брать, ни
 * сколько дней в неделю ходить. Готовая программа снимает ровно этот барьер:
 * править готовое умеют все, сочинять с чистого листа — далеко не все.
 *
 * Шторка вызывается и с главной, где пустое состояние ведёт прямо сюда: раньше
 * кнопка «Создать программу» открывала СПИСОК программ с такой же кнопкой внутри,
 * то есть лишний экран на самом первом шаге новичка.
 */
export async function createProgram() {
  const { templates } = await api.programs.templates();

  const form = sheet(`
    <h2>Новая программа</h2>
    <p class="hint">Готовую можно править как свою.</p>

    ${templates.map((template) => `
      <button class="list-item" data-template="${template.id}">
        <span class="grow">
          <span class="title">${escape(template.name)}</span><br>
          <span class="sub">${escape(template.subtitle)}</span><br>
          <span class="sub num">${plural(template.days, 'день', 'дня', 'дней')} ·
            ${plural(template.preview.reduce((n, d) => n + d.exercises.length, 0),
              'упражнение', 'упражнения', 'упражнений')}</span>
        </span>
        <span class="chev">›</span>
      </button>
    `).join('')}

    <div class="section-title">Или с нуля</div>
    <button class="list-item" data-template="">
      <span class="grow">
        <span class="title">Пустая</span><br>
        <span class="sub">Собрать самому</span>
      </span>
      <span class="chev">›</span>
    </button>
  `);

  form.node.querySelectorAll('[data-template]').forEach((button) => {
    button.onclick = () => {
      const id = button.dataset.template || null;
      form.close();
      nameProgram(id, templates.find((t) => t.id === id));
    };
  });
}

/** Второй шаг: название. У готовой программы оно уже подставлено. */
function nameProgram(templateId, template) {
  const form = sheet(`
    <h2>Название</h2>

    <div class="field">
      <input type="text" id="name" maxlength="50" placeholder="Например: Силовая, 3 дня"
             value="${escape(template?.name || '')}">
    </div>

    ${template ? `
      <p class="hint">${escape(template.preview.map((d) => d.day_of_week).join(' · '))}</p>
    ` : `
      <p class="hint">Семь дней недели, пустых. Программа сразу станет активной.</p>
    `}

    <button class="btn mt-4" id="save">Создать</button>
  `);

  const input = form.node.querySelector('#name');

  form.node.querySelector('#save').onclick = async () => {
    const name = input.value.trim();
    if (!name) return;

    const { program } = await api.programs.create(name, templateId);
    form.close();
    haptic('success');
    go(`/program/${program.id}`);
  };
}

/** Одна программа: дни, активация, настройки, удаление. */
export async function programScreen({ id }) {
  const data = await api.programs.days(Number(id));
  const program = data.program;

  const filled = data.days.filter((day) => day.exercises.length).length;

  render(`
    <h1>${escape(program.name)}</h1>
    <p class="subtitle">${program.active ? 'Активная программа' : 'Не активна'}</p>

    <!-- Пока не заполнен ни один день, экран обязан сказать, что делать: сам по себе
         список из семи одинаковых строк «отдых» выглядит готовым, а не пустым. -->
    ${filled ? '' : `
      <div class="empty">
        <p>Дни пока пустые.</p>
        <p class="hint">Откройте тот, в который тренируетесь. Остальные — выходные.</p>
      </div>
    `}

    ${data.days.map((day) => `
      <button class="list-item" data-day="${day.id}">
        <span class="grow">
          <span class="title">${escape(day.day_of_week)}</span><br>
          <span class="sub${day.exercises.length ? '' : ' quiet'}">
            ${day.exercises.length
              ? escape(day.exercises.map((e) => e.name).join(', '))
              : 'выходной'}
          </span>
        </span>
        <span class="chev">›</span>
      </button>
    `).join('')}

    <div class="section-title">Программа</div>

    <button class="btn secondary" id="settings">Настройки отдыха и кругов</button>

    ${program.active
      ? '<button class="btn ghost mt-2" id="deactivate">Сделать неактивной</button>'
      : '<button class="btn mt-2" id="activate">Сделать активной</button>'}

    <button class="btn danger mt-2" id="remove">Удалить программу</button>
  `);

  onAction('[data-day]', (node) => go(`/day/${node.dataset.day}`));

  on('#settings', 'click', () => openSettings(program, () => programScreen({ id })));

  on('#activate', 'click', async () => {
    await api.programs.activate(program.id);
    haptic('success');
    programScreen({ id });
  });

  on('#deactivate', 'click', async () => {
    await api.programs.deactivate(program.id);
    haptic('warning');
    programScreen({ id });
  });

  on('#remove', 'click', async () => {
    if (!await confirm(`Удалить «${program.name}»? Дни и упражнения удалятся вместе с ней.`)) return;

    await api.programs.remove(program.id);
    haptic('warning');
    go('/programs');
  });
}

function openSettings(program, reload) {
  const settings = program.settings;

  const form = sheet(`
    <h2>Настройки</h2>

    <div class="section-title">Обычные упражнения</div>
    ${seconds('rest_between_set', 'Отдых между подходами', settings.rest_between_set)}
    ${seconds('rest_between_exercise', 'Отдых между упражнениями', settings.rest_between_exercise)}

    <div class="section-title">Круговые</div>
    <div class="field">
      <label>Кругов в блоке</label>
      <input type="number" id="circular_rounds" inputmode="numeric"
             min="1" max="20" value="${settings.circular_rounds}">
    </div>
    ${seconds('circular_rest_between_exercise', 'Отдых между упражнениями в круге',
              settings.circular_rest_between_exercise)}
    ${seconds('circular_rest_between_rounds', 'Отдых между кругами',
              settings.circular_rest_between_rounds)}

    <button class="btn mt-4" id="save">Сохранить</button>
  `);

  form.node.querySelector('#save').onclick = async () => {
    const value = (id) => Number(form.node.querySelector(`#${id}`).value);

    await api.programs.update(program.id, {
      rest_between_set: value('rest_between_set'),
      rest_between_exercise: value('rest_between_exercise'),
      circular_rounds: value('circular_rounds'),
      circular_rest_between_exercise: value('circular_rest_between_exercise'),
      circular_rest_between_rounds: value('circular_rest_between_rounds'),
    });

    form.close();
    haptic('success');
    reload();
  };

  // Отдых задаётся в секундах, но думают о нём в минутах — показываем и то и другое.
  form.node.querySelectorAll('[data-seconds]').forEach((input) => {
    const hint = form.node.querySelector(`#${input.id}-hint`);
    const update = () => { hint.textContent = clock(Number(input.value) || 0); };
    input.addEventListener('input', update);
    update();
  });
}

function seconds(id, label, value) {
  return `
    <div class="field">
      <label>${label}</label>
      <div class="row">
        <input type="number" id="${id}" data-seconds inputmode="numeric"
               min="0" max="3600" step="15" value="${value}">
        <span class="hint secs" id="${id}-hint"></span>
      </div>
    </div>
  `;
}
