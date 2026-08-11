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
  const rounds = data.program?.circular_rounds || 1;

  const row = (exercise) => `
    <div class="ex-row" data-id="${exercise.id}">
      <button class="ex-main" data-edit="${exercise.id}">
        <span class="title">${escape(exercise.name)}</span>
      </button>
      <!-- Подходы отдельной кнопкой: их правят чаще всего остального, и ради
           «3 × 10 → 4 × 8» уходить на отдельный экран и возвращаться не нужно. -->
      <button class="num-btn" data-sets="${exercise.id}"
              aria-label="Подходы и повторения">${exercise.sets} × ${exercise.reps}</button>
      <span class="handle" data-swipe="off" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round"><path d="M4 9h16M4 15h16"/></svg>
      </span>
    </div>
  `;

  render(`
    <h1>${escape(data.day.day_of_week)}</h1>
    <p class="subtitle">
      ${exercises.length
        ? `${plural(exercises.length, 'упражнение', 'упражнения', 'упражнений')} ·
           ${plural(sets, 'подход', 'подхода', 'подходов')}`
        : 'День отдыха'}
    </p>

    ${exercises.length ? '' : '<div class="empty"><p>Пока пусто.</p></div>'}

    <div class="ex-list" id="list">
      ${groupExercises(exercises).map((group) => (
        group.circle
          ? `<div class="circuit">
               <button class="circuit-head" data-rounds>
                 Круговой блок · ${plural(rounds, 'круг', 'круга', 'кругов')}
               </button>
               ${group.items.map(row).join('')}
             </div>`
          : group.items.map(row).join('')
      )).join('')}
      <div class="drop-line" hidden></div>
    </div>

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

  onAll('[data-rounds]', 'click', () => {
    editRounds(data.program, () => dayScreen({ id }));
  });

  // Порядок — не косметика: подряд идущие круговые собираются в один круговой
  // блок, поэтому перетаскивание меняет саму структуру тренировки.
  reorder(document.getElementById('list'), async (ids) => {
    await api.exercises.order(data.day.id, ids);
    haptic('success');
    await dayScreen({ id });
  });
}

/**
 * Перетаскивание упражнений за ручку.
 *
 * Стрелки ↑↓ убраны: чтобы поднять упражнение на три позиции, их надо было нажать
 * трижды, и одна из двух всегда была погашена. Тащим ЗА РУЧКУ, а не за строку —
 * тогда жест не спорит ни с прокруткой страницы, ни с длинным нажатием, и не нужен
 * порог задержки, из-за которого перетаскивание всегда начинается с паузы.
 *
 * Место высадки показывает линия, а не расступающиеся строки: строки живут внутри
 * разных контейнеров (круговой блок оборачивает свои), и раздвигать их пришлось бы
 * с оглядкой на высоту шапки блока. Линия от вёрстки не зависит вовсе.
 *
 * У края экрана список подкручивается сам — иначе длинный день пришлось бы возить
 * в два приёма.
 */
function reorder(list, save) {
  if (!list) return;

  let dragged = null;      // строка, которую тащат
  let rows = [];           // все строки в порядке экрана, с их серединами
  let target = 0;          // индекс, куда встанет строка
  let startY = 0;          // где взялись, в координатах СТРАНИЦЫ
  let frame = 0;

  // Считаем от страницы, а не от окна: у края список подкручивается сам, и
  // в оконных координатах строка уезжала бы из-под пальца ровно на прокрутку.
  const pageY = (event) => event.clientY + window.scrollY;

  const line = list.querySelector('.drop-line');

  const measure = () => {
    rows = [...list.querySelectorAll('.ex-row')].map((node) => {
      const box = node.getBoundingClientRect();
      return { node, top: box.top, bottom: box.bottom, middle: box.top + box.height / 2 };
    });
  };

  /** Куда встанет строка, если отпустить палец сейчас. */
  const place = (y) => {
    const base = rows.filter((r) => r.node !== dragged);
    let index = base.findIndex((r) => y < r.middle);
    if (index < 0) index = base.length;

    target = index;

    const edge = index < base.length
      ? base[index].top
      : (base[base.length - 1]?.bottom ?? 0);

    line.hidden = false;
    line.style.top = `${edge - list.getBoundingClientRect().top}px`;
  };

  /** Подкрутка у краёв: ближе 72 px к границе — едем со скоростью до 12 px за кадр. */
  const autoscroll = (y) => {
    const zone = 72;
    const speed = y < zone ? -(zone - y) / 6
      : y > window.innerHeight - zone ? (y - (window.innerHeight - zone)) / 6
      : 0;

    cancelAnimationFrame(frame);
    if (!speed) return;

    const step = () => {
      if (!dragged) return;
      window.scrollBy(0, Math.max(-12, Math.min(12, speed)));
      measure();
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  };

  list.querySelectorAll('.handle').forEach((handle) => {
    handle.addEventListener('pointerdown', (event) => {
      dragged = handle.closest('.ex-row');
      startY = pageY(event);

      handle.setPointerCapture(event.pointerId);
      dragged.classList.add('dragging');
      document.body.classList.add('reordering');
      haptic('light');

      measure();
      place(event.clientY);
      event.preventDefault();
    });

    handle.addEventListener('pointermove', (event) => {
      if (!dragged) return;

      dragged.style.transform = `translateY(${pageY(event) - startY}px)`;
      place(event.clientY);
      autoscroll(event.clientY);
      event.preventDefault();
    });

    const drop = async () => {
      if (!dragged) return;

      const node = dragged;
      dragged = null;
      cancelAnimationFrame(frame);

      node.classList.remove('dragging');
      node.style.transform = '';
      document.body.classList.remove('reordering');
      line.hidden = true;

      const ids = rows.map((r) => Number(r.node.dataset.id));
      const from = ids.indexOf(Number(node.dataset.id));
      const rest = ids.filter((_, i) => i !== from);
      rest.splice(target, 0, Number(node.dataset.id));

      // Порядок не изменился — сервер дёргать незачем.
      if (rest.every((value, i) => value === ids[i])) return;
      await save(rest);
    };

    handle.addEventListener('pointerup', drop);
    handle.addEventListener('pointercancel', drop);
  });
}

/**
 * Кругов в блоке.
 *
 * Это настройка ПРОГРАММЫ: `build_plan` разворачивает по ней все круговые блоки
 * дня, отдельного числа у блока в схеме нет. Поэтому правка отсюда меняет круги
 * во всех круговых блоках программы — про это и написано в шторке, чтобы число
 * на блоке не выглядело его собственным.
 */
function editRounds(program, reload) {
  if (!program) return;

  const form = sheet(`
    <h2>Кругов в блоке</h2>

    <div class="stepper compact">
      <button data-delta="-1" aria-label="Кругов: минус 1">−</button>
      <div class="value">
        <input id="rounds" type="number" inputmode="numeric"
               value="${program.circular_rounds}" min="1" max="20">
      </div>
      <button data-delta="1" aria-label="Кругов: плюс 1">+</button>
    </div>

    <p class="hint mt-3">Общее число для всех круговых блоков программы.</p>

    <button class="btn mt-4" id="save">Готово</button>
  `);

  const input = form.node.querySelector('#rounds');

  form.node.querySelectorAll('[data-delta]').forEach((button) => {
    button.onclick = () => {
      const value = (parseInt(input.value, 10) || 1) + Number(button.dataset.delta);
      input.value = Math.min(20, Math.max(1, value));
    };
  });

  form.node.querySelector('#save').onclick = async () => {
    await api.programs.update(program.id, {
      circular_rounds: parseInt(input.value, 10),
    });
    form.close();
    haptic('success');
    reload();
  };
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
