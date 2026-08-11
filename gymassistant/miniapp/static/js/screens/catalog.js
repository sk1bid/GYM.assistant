/**
 * Каталог упражнений и настройка упражнения в дне.
 *
 * В боте это были уровни 5–7 меню: категории → упражнения в категории → добавление,
 * и отдельная ветка настроек, где из всех параметров упражнения можно было менять
 * ровно один — количество подходов. Повторения не менялись никогда: обработчик
 * вызывался только с tp="sets", и base_reps у всех навсегда оставался равен 10.
 *
 * Главное, что здесь появилось потом, — ПАЧКА. Упражнения выбирают группами: зашёл
 * в «Спину», взял тягу, подтягивания и блок. Раньше каждое добавление уводило обратно
 * в день, и за следующим надо было идти сюда заново: «Добавить» → категория →
 * упражнение → и ещё вопрос «как выполнять?». Четыре тапа на упражнение, шестнадцать
 * на день из четырёх — при том, что осмысленный выбор в них ровно один.
 */
import { api } from './../api.js';
import { go } from './../router.js';
import { confirm, haptic } from './../tg.js';
import { escape, on, onAction, onAll, plural, render, sheet } from './../ui.js';

/**
 * Категории и поиск по всему каталогу сразу.
 *
 * Поиск идёт по списку, приехавшему вместе с категориями, а не запросом на каждую
 * букву: каталог невелик, а сетевой круг до пода — 50–90 мс, то есть заметная
 * задержка на каждый символ. Ищем по всем группам вместе — помнить, дельты «жим
 * стоя» или грудь, пользователь не обязан.
 */
export async function catalogScreen({ dayId }) {
  const { categories, exercises } = await api.catalog.categories();

  render(`
    <h1>Добавить упражнение</h1>

    <input type="search" id="q" class="search" autocomplete="off"
           placeholder="Найти упражнение" aria-label="Поиск по каталогу">

    <div id="results" hidden></div>

    <div id="groups">
      ${categories.map((category) => `
        <button class="list-item" data-category="${category.id}">
          <span class="grow">
            <span class="title">${escape(category.name)}</span><br>
            <span class="sub">${category.count} в каталоге</span>
          </span>
          <span class="chev">›</span>
        </button>
      `).join('')}

      <div class="section-title">Своё</div>
      <button class="list-item" id="mine">
        <span class="grow"><span class="title">Мои упражнения</span><br>
          <span class="sub">Создать или изменить</span></span>
        <span class="chev">›</span>
      </button>
    </div>
  `);

  onAction('[data-category]', (node) => go(`/catalog/${dayId}/${node.dataset.category}`));

  // День передаём дальше: своё упражнение почти всегда заводят посреди сборки дня,
  // и возвращать человека в каталог руками — тот самый тупик, из-за которого
  // созданное упражнение приходилось искать заново.
  on('#mine', 'click', () => go(`/my-exercises/${dayId}`));

  const input = document.getElementById('q');
  const results = document.getElementById('results');
  const groups = document.getElementById('groups');

  input.addEventListener('input', () => {
    const query = input.value.trim().toLowerCase();
    const empty = query.length < 2;

    results.hidden = empty;
    groups.hidden = !empty;
    if (empty) return;

    const found = exercises.filter((e) => e.name.toLowerCase().includes(query));

    results.innerHTML = found.length
      ? found.map((e) => `
          <button class="list-item" data-pick="${e.id}" data-kind="${e.kind}">
            <span class="grow">
              <span class="title">${escape(e.name)}
                ${e.kind === 'user' ? '<span class="pill">своё</span>' : ''}</span><br>
              <span class="sub">${escape(e.description || '')}</span>
            </span>
            <span class="chev">+</span>
          </button>
        `).join('')
      : `<div class="empty"><p>Ничего не нашлось.</p>
           <button class="btn secondary small" id="create-found">Создать своё упражнение</button></div>`;

    // Обработчики вешаются заново на каждый ввод: содержимое блока целиком новое.
    results.querySelectorAll('[data-pick]').forEach((node) => {
      node.onclick = async () => {
        if (node.disabled) return;
        node.disabled = true;
        haptic('light');
        await addToDay(dayId, [{ id: Number(node.dataset.pick), kind: node.dataset.kind }]);
      };
    });

    const create = results.querySelector('#create-found');
    if (create) create.onclick = () => go(`/my-exercises/${dayId}`);
  });
}

/** Отправляет выбранное в день и возвращает туда же. */
async function addToDay(dayId, picked, circle = false) {
  await api.exercises.addMany(Number(dayId), picked.map((item) => ({
    [item.kind === 'admin' ? 'admin_exercise_id' : 'user_exercise_id']: item.id,
    circle_training: circle,
  })));

  haptic('success');
  go(`/day/${dayId}`);
}

/**
 * Упражнения категории — отмечаем нужные и добавляем разом.
 *
 * Прежде тап добавлял упражнение немедленно и уводил в день, а перед этим ещё
 * спрашивал шторкой «обычное или круговое». Вопрос задавался КАЖДЫЙ раз, хотя
 * круговые нужны меньшинству, и тот же самый флаг всё это время жил переключателем
 * на экране упражнения — один факт вводился дважды. Теперь добавление обычное,
 * а круг — общий тумблер внизу, на всю пачку сразу: круговой блок и собирается
 * из подряд идущих упражнений, поодиночке его всё равно не задать.
 */
export async function categoryScreen({ dayId, categoryId }) {
  const { exercises } = await api.catalog.category(Number(categoryId));

  // Ключ «вид:id», а не сам объект: id пресета и id личного упражнения — числа из
  // разных таблиц и вполне могут совпасть.
  const picked = new Map();
  const key = (kind, id) => `${kind}:${id}`;

  render(`
    <h1>Выберите упражнения</h1>
    <p class="subtitle">Отметьте всё, что нужно, — добавятся одним махом</p>

    ${exercises.length ? '' : '<div class="empty">В этой группе пока пусто</div>'}

    <div class="pick-list">
      ${exercises.map((exercise) => `
        <label class="pick" data-kind="${exercise.kind}">
          <input type="checkbox" value="${exercise.id}">
          <span class="box"></span>
          <span class="grow">
            <span class="title">
              ${escape(exercise.name)}
              ${exercise.kind === 'user' ? '<span class="pill">своё</span>' : ''}
            </span><br>
            <span class="sub">${escape(exercise.description || '')}</span>
          </span>
        </label>
      `).join('')}
    </div>

    <button class="list-item mt-3" id="create-own">
      <span class="grow"><span class="title">Создать своё упражнение</span><br>
        <span class="sub">Если нужного нет в каталоге</span></span>
      <span class="chev">+</span>
    </button>

    <!-- Панель прижата к низу экрана поверх содержимого, а не стоит в конце списка:
         в конец пришлось бы доскроллить, а решение «добавить» созревает на любой
         строке. Появляется вместе с первой галочкой; список этого не замечает,
         потому что место под неё зарезервировано отступом .pick-list. -->
    <div class="pick-bar" id="bar" hidden>
      <label class="switch compact">
        <span class="grow"><span class="lead">Круговым блоком</span></span>
        <input type="checkbox" id="circle">
        <span class="track"></span>
      </label>
      <button class="btn" id="add">Добавить</button>
    </div>
  `);

  const bar = document.getElementById('bar');
  const button = document.getElementById('add');

  const sync = () => {
    bar.hidden = picked.size === 0;
    button.textContent = `Добавить ${plural(picked.size, 'упражнение', 'упражнения', 'упражнений')}`;
  };

  onAll('.pick input', 'change', (input) => {
    const kind = input.closest('.pick').dataset.kind;
    const id = Number(input.value);

    if (input.checked) picked.set(key(kind, id), { id, kind });
    else picked.delete(key(kind, id));

    haptic('light');
    sync();
  });

  on('#create-own', 'click', () => go(`/my-exercises/${dayId}`));

  onAction('#add', async () => {
    if (!picked.size) return;

    // Порядок — тот, в котором упражнения стоят на экране, а не в котором их
    // отмечали: список читается сверху вниз, и круговой блок собирается так же.
    const order = new Map(exercises.map((e, index) => [key(e.kind, e.id), index]));
    const items = [...picked.values()].sort(
      (a, b) => order.get(key(a.kind, a.id)) - order.get(key(b.kind, b.id)),
    );

    await addToDay(dayId, items, document.getElementById('circle').checked);
  });
}

/** Настройки упражнения в дне: подходы, повторения, круговое. */
export async function exerciseScreen({ dayId, id }) {
  const data = await api.day(Number(dayId));
  const exercise = data.exercises.find((e) => e.id === Number(id));

  if (!exercise) return go(`/day/${dayId}`, { replace: true });

  render(`
    <h1>${escape(exercise.name)}</h1>
    <p class="subtitle">${escape(exercise.description || '')}</p>

    <!-- Подписи только в <label>: у степпера есть и своя, .unit, но здесь она
         повторила бы слово в слово то, что уже написано над полем. -->
    <label for="sets">Подходов</label>
    <div class="stepper compact">
        <button data-field="sets" data-delta="-1" aria-label="Подходов: минус 1">−</button>
        <div class="value">
          <input id="sets" type="number" inputmode="numeric" value="${exercise.sets}" min="1" max="20">
        </div>
        <button data-field="sets" data-delta="1" aria-label="Подходов: плюс 1">+</button>
    </div>

    <label class="mt-4" for="reps">Повторений в подходе</label>
    <div class="stepper compact">
        <button data-field="reps" data-delta="-1" aria-label="Повторений: минус 1">−</button>
        <div class="value">
          <input id="reps" type="number" inputmode="numeric" value="${exercise.reps}" min="1" max="100">
        </div>
        <button data-field="reps" data-delta="1" aria-label="Повторений: плюс 1">+</button>
    </div>

    <div class="switch-card mt-4">
      <label class="switch">
        <span class="grow">
          <span class="lead">Круговое</span><br>
          <span class="hint">В круге с соседними круговыми упражнениями</span>
        </span>
        <input type="checkbox" id="circle" ${exercise.circle ? 'checked' : ''}>
        <span class="track"></span>
      </label>
    </div>

    <button class="btn mt-4" id="save">Сохранить</button>
    <button class="btn danger mt-2" id="remove">Убрать из дня</button>
  `);

  onAction('[data-field]', (node) => {
    const input = document.getElementById(node.dataset.field);
    const value = (parseInt(input.value, 10) || 0) + parseInt(node.dataset.delta, 10);
    input.value = Math.max(1, value);
  });

  on('#save', 'click', async () => {
    await api.exercises.update(exercise.id, {
      sets: parseInt(document.getElementById('sets').value, 10),
      reps: parseInt(document.getElementById('reps').value, 10),
      circle_training: document.getElementById('circle').checked,
    });
    haptic('success');
    go(`/day/${dayId}`);
  });

  on('#remove', 'click', async () => {
    if (!await confirm('Убрать упражнение из этого дня?')) return;
    await api.exercises.remove(exercise.id);
    haptic('warning');
    go(`/day/${dayId}`);
  });
}

/**
 * Личные упражнения: создать, изменить, удалить.
 *
 * `dayId` приезжает, когда сюда пришли посреди сборки дня. Тогда созданное
 * упражнение сразу и кладётся в этот день — иначе человек, впервые заводящий своё,
 * упирался в тупик: создал и остался на экране списка, а добавить его в день можно
 * было только вернувшись в каталог и найдя себя среди пресетов.
 */
export async function myExercisesScreen({ dayId } = {}) {
  const [{ exercises }, { categories }] = await Promise.all([
    api.userExercises.list(),
    api.catalog.categories(),
  ]);

  const categoryName = (id) => categories.find((c) => c.id === id)?.name || '';

  render(`
    <h1>Мои упражнения</h1>
    ${dayId ? '<p class="subtitle">Созданное сразу добавится в день</p>' : ''}

    ${exercises.length ? '' : `
      <div class="empty">
        <p>Своих упражнений пока нет.</p>
        <p class="hint">Добавьте то, чего нет в каталоге.</p>
      </div>
    `}

    ${exercises.map((exercise) => `
      <div class="ex-row">
        <button class="ex-main" data-edit="${exercise.id}">
          <span class="title">${escape(exercise.name)}</span><br>
          <span class="sub">${escape(categoryName(exercise.category_id))}</span>
        </button>
        ${dayId ? `<button class="icon-btn" data-add="${exercise.id}"
                           aria-label="Добавить в день">+</button>` : ''}
      </div>
    `).join('')}

    <button class="btn secondary mt-4" id="create">Создать упражнение</button>
  `);

  on('#create', 'click', () => editUserExercise(null, categories, dayId));
  onAll('[data-edit]', 'click', (node) => {
    const exercise = exercises.find((e) => e.id === Number(node.dataset.edit));
    editUserExercise(exercise, categories, dayId);
  });

  onAction('[data-add]', async (node) => {
    await addToDay(dayId, [{ id: Number(node.dataset.add), kind: 'user' }]);
  });
}

function editUserExercise(exercise, categories, dayId) {
  const form = sheet(`
    <h2>${exercise ? 'Изменить' : 'Новое упражнение'}</h2>

    <div class="field">
      <label>Название</label>
      <input type="text" id="name" maxlength="150" value="${escape(exercise?.name || '')}">
    </div>

    <div class="field">
      <label>Группа мышц</label>
      <select id="category">
        ${categories.map((c) => `
          <option value="${c.id}" ${exercise?.category_id === c.id ? 'selected' : ''}>
            ${escape(c.name)}
          </option>
        `).join('')}
      </select>
    </div>

    <!-- Описание последним и подписано необязательным: без него упражнение
         прекрасно работает, а тремя равнозначными полями форма выглядела анкетой. -->
    <div class="field">
      <label>Описание <span class="hint">— не обязательно</span></label>
      <textarea id="description" maxlength="1000">${escape(exercise?.description || '')}</textarea>
    </div>

    <button class="btn" id="save">${exercise || !dayId ? 'Сохранить' : 'Создать и добавить в день'}</button>
    ${exercise ? '<button class="btn danger mt-2" id="remove">Удалить</button>' : ''}
  `);

  form.node.querySelector('#save').onclick = async () => {
    const payload = {
      name: form.node.querySelector('#name').value.trim(),
      description: form.node.querySelector('#description').value.trim(),
      category_id: Number(form.node.querySelector('#category').value),
    };

    if (!payload.name) return;

    if (exercise) {
      await api.userExercises.update(exercise.id, payload);
      form.close();
      haptic('success');
      return myExercisesScreen({ dayId });
    }

    const created = await api.userExercises.create(payload);
    form.close();
    haptic('success');

    // Пришли из дня — за этим сюда и шли: кладём созданное туда и уходим.
    if (dayId) return addToDay(dayId, [{ id: created.exercise.id, kind: 'user' }]);
    myExercisesScreen({ dayId });
  };

  const removeButton = form.node.querySelector('#remove');
  if (removeButton) {
    removeButton.onclick = async () => {
      // Упражнение может стоять в днях программы — там оно удалится каскадом
      // вместе со всей историей подходов по нему. Предупреждаем честно.
      if (!await confirm('Удалить упражнение? Оно исчезнет из всех программ вместе с историей.')) return;

      await api.userExercises.remove(exercise.id);
      form.close();
      haptic('warning');
      myExercisesScreen({ dayId });
    };
  }
}
