/**
 * Расписание и тренировочный день.
 *
 * В боте расписание было календарём на инлайн-кнопках — свёрнутый на неделю,
 * развёрнутый на месяц. Но программа задаётся по дням недели, а не по датам:
 * календарь показывал числа, за которыми не стояло ничего, кроме дня недели.
 * Поэтому здесь честная неделя Пн→Вс с сегодняшним днём, поднятым наверх смыслом.
 */
import { api, cached } from './../api.js';
import { go } from './../router.js';
import { haptic } from './../tg.js';
import { escape, on, onAction, onAll, plural, render, same, sheet } from './../ui.js';

export async function scheduleScreen() {
  // Мгновенная отрисовка из последнего ответа, свежий — следом. См. home.js.
  const known = cached('api/schedule');
  if (known) paintSchedule(known);

  const data = await api.schedule();
  if (!known || !same(known, data)) paintSchedule(data);
}

function paintSchedule(data) {
  if (!data.program) {
    render(`
      <h1>Расписание</h1>
      <div class="empty">
        <p>Нет активной программы.</p>
        <button class="btn secondary small" id="to-programs">К программам</button>
      </div>
    `);
    return on('#to-programs', 'click', () => go('/programs'));
  }

  render(`
    <h1>Расписание</h1>
    <p class="subtitle">${escape(data.program.name)}</p>

    ${data.days.map((day) => {
      const isToday = day.day_of_week === data.today;

      return `
        <button class="list-item" data-day="${day.id}">
          <span class="grow">
            <span class="title">${escape(day.day_of_week)}</span>
            ${isToday ? '<span class="pill on">сегодня</span>' : ''}
            <br>
            <span class="sub">
              ${day.exercises.length
                ? escape(day.exercises.map((e) => e.name).join(', '))
                : 'отдых'}
            </span>
          </span>
          <span class="chev">›</span>
        </button>
      `;
    }).join('')}
  `);

  onAction('[data-day]', (node) => go(`/day/${node.dataset.day}`));
}

/**
 * Круговые блоки — из подряд идущих круговых упражнений.
 *
 * Правило это серверное (`build_plan` в services/workout.py), но до сих пор оно
 * нигде не было видно: круговые стояли обычными строками с одинаковой пилюлей
 * «круговое», и что именно СОСЕДСТВО собирает их в один круг, догадаться было
 * неоткуда. Отсюда и группировка при отрисовке — она просто показывает то, по чему
 * тренировка и так пойдёт.
 */
function groupExercises(exercises) {
  const groups = [];

  for (const exercise of exercises) {
    const last = groups[groups.length - 1];
    if (exercise.circle && last?.circle) last.items.push(exercise);
    else groups.push({ circle: exercise.circle, items: [exercise] });
  }

  return groups;
}

export async function dayScreen({ id }) {
  const data = await api.day(Number(id));
  const exercises = data.exercises;
  const sets = exercises.reduce((sum, e) => sum + e.sets, 0);

  // Стрелки гасятся по месту упражнения в ДНЕ, а не внутри кругового блока:
  // «выше» переносит через границу блока — так круговое и выводят из круга.
  const row = (exercise) => {
    const index = exercises.indexOf(exercise);

    return `
      <div class="ex-row">
        <button class="ex-main" data-edit="${exercise.id}">
          <span class="title">${escape(exercise.name)}</span><br>
          <span class="sub">нажмите, чтобы настроить</span>
        </button>
        <!-- Подходы отдельной кнопкой: их правят чаще всего остального, и ради
             «3 × 10 → 4 × 8» уходить на отдельный экран и возвращаться не нужно. -->
        <button class="num-btn" data-sets="${exercise.id}"
                aria-label="Подходы и повторения">${exercise.sets} × ${exercise.reps}</button>
        <button class="icon-btn" data-up="${exercise.id}"
                aria-label="Выше" ${index === 0 ? 'disabled' : ''}>↑</button>
        <button class="icon-btn" data-down="${exercise.id}"
                aria-label="Ниже" ${index === exercises.length - 1 ? 'disabled' : ''}>↓</button>
      </div>
    `;
  };

  render(`
    <h1>${escape(data.day.day_of_week)}</h1>
    <p class="subtitle">
      ${exercises.length
        ? `${plural(exercises.length, 'упражнение', 'упражнения', 'упражнений')} ·
           ${plural(sets, 'подход', 'подхода', 'подходов')}`
        : 'День отдыха'}
    </p>

    ${exercises.length ? '' : `
      <div class="empty">
        <p>В этот день ничего не запланировано.</p>
      </div>
    `}

    ${groupExercises(exercises).map((group) => (
      group.circle
        ? `<div class="circuit">
             <div class="circuit-head">Круговой блок · ${plural(group.items.length,
               'упражнение', 'упражнения', 'упражнений')} в круге</div>
             ${group.items.map(row).join('')}
           </div>`
        : group.items.map(row).join('')
    )).join('')}

    <button class="btn secondary mt-4" data-add>Добавить упражнение</button>

    ${exercises.length ? '<button class="btn mt-2" id="start">Начать тренировку</button>' : ''}
  `);

  on('#start', 'click', () => go(`/workout/${data.day.id}`));
  onAction('[data-add]', () => go(`/catalog/${data.day.id}`));
  onAction('[data-edit]', (node) => go(`/day/${data.day.id}/exercise/${node.dataset.edit}`));

  onAll('[data-sets]', 'click', (node) => {
    const exercise = exercises.find((e) => e.id === Number(node.dataset.sets));
    editSets(exercise, () => dayScreen({ id }));
  });

  // Порядок упражнений — не косметика: подряд идущие круговые собираются в один
  // круговой блок, поэтому перестановка меняет саму структуру тренировки.
  onAction('[data-up]', async (node) => {
    await api.exercises.move(Number(node.dataset.up), true);
    await dayScreen({ id });
  });

  onAction('[data-down]', async (node) => {
    await api.exercises.move(Number(node.dataset.down), false);
    await dayScreen({ id });
  });
}

/** Подходы и повторения — шторкой, не уходя с экрана дня. */
function editSets(exercise, reload) {
  const form = sheet(`
    <h2>${escape(exercise.name)}</h2>

    <div class="pair">
      <div>
        <label for="sets">Подходов</label>
        <div class="stepper compact">
          <button data-field="sets" data-delta="-1" aria-label="Подходов: минус 1">−</button>
          <div class="value">
            <input id="sets" type="number" inputmode="numeric"
                   value="${exercise.sets}" min="1" max="20">
          </div>
          <button data-field="sets" data-delta="1" aria-label="Подходов: плюс 1">+</button>
        </div>
      </div>
      <div>
        <label for="reps">Повторений</label>
        <div class="stepper compact">
          <button data-field="reps" data-delta="-1" aria-label="Повторений: минус 1">−</button>
          <div class="value">
            <input id="reps" type="number" inputmode="numeric"
                   value="${exercise.reps}" min="1" max="100">
          </div>
          <button data-field="reps" data-delta="1" aria-label="Повторений: плюс 1">+</button>
        </div>
      </div>
    </div>

    <button class="btn mt-4" id="save">Готово</button>
  `);

  const field = (name) => form.node.querySelector(`#${name}`);

  form.node.querySelectorAll('[data-field]').forEach((button) => {
    button.onclick = () => {
      const input = field(button.dataset.field);
      input.value = Math.max(1, (parseInt(input.value, 10) || 0) + Number(button.dataset.delta));
    };
  });

  form.node.querySelector('#save').onclick = async () => {
    await api.exercises.update(exercise.id, {
      sets: parseInt(field('sets').value, 10),
      reps: parseInt(field('reps').value, 10),
    });
    form.close();
    haptic('success');
    reload();
  };
}
