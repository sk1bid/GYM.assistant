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

export async function dayScreen({ id }) {
  const data = await api.day(Number(id));
  const exercises = data.exercises;
  const sets = exercises.reduce((sum, e) => sum + e.sets, 0);
  const rounds = data.program?.circular_rounds || 1;

  /*
    Список ПЛОСКИЙ: круговой блок обозначен рамкой на строках и шапкой-соседом,
    а не контейнером вокруг них.

    Обёртка была бы честнее по разметке, но ломает перетаскивание: строка,
    переезжающая из круга наружу, меняла бы родителя, и вычислять, куда она едет,
    пришлось бы с оглядкой на две системы координат. В одном контейнере перестановка
    — это insertBefore, а принадлежность блоку пересчитывается заново по соседям
    ровно тем же правилом, по которому её считает сервер.
  */
  const row = (exercise) => `
    <div class="ex-row" data-id="${exercise.id}" data-circle="${exercise.circle ? 1 : 0}">
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

    <div class="ex-list" id="list">${exercises.map(row).join('')}</div>

    <button class="btn secondary mt-4" data-add>Добавить упражнение</button>

    ${exercises.length ? '<button class="btn mt-2" id="start">Начать тренировку</button>' : ''}
  `);

  const list = document.getElementById('list');
  const openRounds = () => editRounds(data.program, () => dayScreen({ id }));

  decorate(list, rounds, openRounds);

  on('#start', 'click', () => go(`/workout/${data.day.id}`));
  onAction('[data-add]', () => go(`/catalog/${data.day.id}`));
  onAction('[data-edit]', (node) => go(`/day/${data.day.id}/exercise/${node.dataset.edit}`));

  onAll('[data-sets]', 'click', (node) => {
    const exercise = exercises.find((e) => e.id === Number(node.dataset.sets));
    editSets(exercise, () => dayScreen({ id }));
  });

  // Порядок — не косметика: подряд идущие круговые собираются в один круговой
  // блок, поэтому перетаскивание меняет саму структуру тренировки.
  reorder(list, () => decorate(list, rounds, openRounds), async (ids) => {
    try {
      await api.exercises.order(data.day.id, ids);
    } catch (error) {
      // Экран уже показывает новый порядок — если сервер его не принял, честнее
      // перерисоваться от него, чем оставить картинку, которой нет в базе.
      await dayScreen({ id });
      throw error;
    }
  });
}

/**
 * Расставляет признаки круговых блоков по плоскому списку.
 *
 * Блок — это подряд идущие круговые упражнения, ровно как считает `build_plan`
 * на сервере. Пересчитывается на каждое перемещение, поэтому строка, вытащенная
 * из круга, перестаёт быть его частью сразу под пальцем, а не после перезагрузки
 * экрана: перетаскивание здесь меняет структуру тренировки, и видеть это надо
 * в момент, когда решение принимается.
 */
function decorate(list, rounds, onRounds) {
  list.querySelectorAll('.circuit-head').forEach((node) => node.remove());

  const rows = [...list.querySelectorAll('.ex-row')];

  rows.forEach((node, index) => {
    const circle = node.dataset.circle === '1';
    const before = index > 0 && rows[index - 1].dataset.circle === '1';
    const after = index < rows.length - 1 && rows[index + 1].dataset.circle === '1';

    node.classList.toggle('in-circuit', circle);
    node.classList.toggle('circuit-last', circle && !after);

    if (!circle || before) return;

    const head = document.createElement('button');
    head.className = 'circuit-head';
    head.textContent = `Круговой блок · ${plural(rounds, 'круг', 'круга', 'кругов')}`;
    head.onclick = onRounds;
    list.insertBefore(head, node);
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
 * Перестановка происходит СРАЗУ, под пальцем: как только строка перевалила середину
 * соседа, она меняется с ним местами в разметке, а соседи разъезжаются анимацией.
 * Раньше здесь была линия-указатель и перерисовка с сервера на отпускании — то есть
 * названия менялись местами уже после того, как палец отпустил, с задержкой на
 * сетевой круг. Порядок уезжает на сервер молча, вдогонку; экран его не ждёт,
 * потому что показывает ровно то, что человек только что сделал руками.
 *
 * Соседей двигаем приёмом FLIP: замерили, где строки были, переставили в разметке,
 * замерили, где стали, — и проигрываем разницу. Считать смещения самим тут нельзя:
 * строка, покидающая круговой блок, меняет высоту всего блока (у него появляется
 * или исчезает шапка), и «сдвинуть на высоту строки» дало бы промах ровно на неё.
 */
function reorder(list, redecorate, save) {
  if (!list) return;

  let dragged = null;
  let offset = 0;        // на сколько строка смещена от своего места в разметке
  let lastY = 0;         // прошлая позиция пальца, в координатах СТРАНИЦЫ
  let order = [];        // порядок на момент начала жеста — с чем сравнивать
  let frame = 0;

  // От страницы, а не от окна: у края список подкручивается сам, и в оконных
  // координатах строка уезжала бы из-под пальца ровно на величину прокрутки.
  const pageY = (event) => event.clientY + window.scrollY;
  const ids = () => [...list.querySelectorAll('.ex-row')].map((n) => Number(n.dataset.id));

  /** Переставляет строку и доводит соседей на новые места. */
  const shuffle = (mutate) => {
    const rows = [...list.querySelectorAll('.ex-row')];
    const was = new Map(rows.map((node) => [node, node.getBoundingClientRect().top]));
    const before = dragged.getBoundingClientRect().top;

    mutate();
    redecorate();

    // Своё смещение пересчитываем так, чтобы строка не дёрнулась: место в разметке
    // у неё теперь другое, а под пальцем она обязана остаться там же, где была.
    offset += before - dragged.getBoundingClientRect().top;
    dragged.style.transform = `translateY(${offset}px)`;

    for (const node of rows) {
      if (node === dragged) continue;

      const shift = was.get(node) - node.getBoundingClientRect().top;
      if (!shift) continue;

      node.style.transition = 'none';
      node.style.transform = `translateY(${shift}px)`;

      requestAnimationFrame(() => {
        node.style.transition = '';
        node.style.transform = '';
      });
    }

    haptic('light');
  };

  /** Перевалили середину соседа — меняемся с ним местами. */
  const consider = (clientY) => {
    for (const node of list.querySelectorAll('.ex-row')) {
      if (node === dragged) continue;

      const box = node.getBoundingClientRect();
      if (clientY < box.top || clientY > box.bottom) continue;

      const middle = box.top + box.height / 2;
      const above = node.compareDocumentPosition(dragged) & Node.DOCUMENT_POSITION_FOLLOWING;

      if (above && clientY < middle) shuffle(() => node.before(dragged));
      else if (!above && clientY > middle) shuffle(() => node.after(dragged));
      return;
    }
  };

  /** У края экрана список едет сам — иначе длинный день пришлось бы возить в два приёма. */
  const autoscroll = (clientY) => {
    const zone = 72;
    const speed = clientY < zone ? -(zone - clientY) / 6
      : clientY > window.innerHeight - zone ? (clientY - (window.innerHeight - zone)) / 6
      : 0;

    cancelAnimationFrame(frame);
    if (!speed) return;

    const step = () => {
      if (!dragged) return;
      window.scrollBy(0, Math.max(-12, Math.min(12, speed)));
      consider(clientY);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  };

  list.querySelectorAll('.handle').forEach((handle) => {
    const start = (event) => {
      dragged = handle.closest('.ex-row');
      offset = 0;
      lastY = pageY(event);
      order = ids();

      handle.setPointerCapture(event.pointerId);
      dragged.classList.add('dragging');
      document.body.classList.add('reordering');
      haptic('light');
      event.preventDefault();
    };

    const move = (event) => {
      if (!dragged) return;

      const y = pageY(event);
      offset += y - lastY;
      lastY = y;
      dragged.style.transform = `translateY(${offset}px)`;

      consider(event.clientY);
      autoscroll(event.clientY);
      event.preventDefault();
    };

    const drop = async () => {
      if (!dragged) return;

      const node = dragged;
      dragged = null;
      cancelAnimationFrame(frame);

      // Возврат на место — с доводкой: строка уже стоит в нужной позиции разметки,
      // осталось погасить смещение, накопленное пальцем.
      //
      // Снимаем поднятие по таймеру, а не по transitionend: если палец не сдвинул
      // строку ни на пиксель, гасить нечего, перехода не будет — и события тоже,
      // а строка так и осталась бы висеть с тенью.
      const settled = Math.abs(offset) < 1;
      node.style.transition = settled ? 'none' : '';
      node.style.transform = '';

      setTimeout(() => {
        node.style.transition = '';
        node.classList.remove('dragging');
      }, settled ? 0 : 200);

      document.body.classList.remove('reordering');

      const next = ids();
      if (next.every((value, index) => value === order[index])) return;

      haptic('success');
      await save(next);
    };

    handle.addEventListener('pointerdown', start);
    handle.addEventListener('pointermove', move);
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
