/**
 * Профиль, история и прогресс.
 *
 * История в боте была списком с пагинацией по пять штук, а её кнопки ссылались на UUID
 * из модульного словаря (utils/temporary_storage.py): словарь не чистился, тёк, и после
 * рестарта пода все кнопки истории умирали. Здесь id тренировки просто лежит в адресе.
 *
 * Графики — то, чего в чате не было в принципе: рисовать линию прогресса символами
 * никто не стал, поэтому от всей истории пользователю доставался плоский список чисел.
 */
import { api, cached } from './../api.js';
import { go } from './../router.js';
import { haptic } from './../tg.js';
import {
  escape, formatDate, on, onAction, plural, render, same, sheet, shortDate, volume, weight,
} from './../ui.js';

const ACTIVITY_PATH = 'api/stats/activity?weeks=18';

export async function profileScreen() {
  // Мгновенная отрисовка из последнего ответа, свежий — следом. См. home.js.
  const known = cached('api/profile');
  // Снимок ДО запроса: api.activity() перезапишет кэш, и сравнивать было бы не с чем.
  const knownActivity = cached(ACTIVITY_PATH);

  if (known) paintProfile(known, knownActivity);

  // Календарь не должен ронять экран: профиль обязан открыться и без него.
  const [data, activity] = await Promise.all([
    api.profile(),
    api.activity().catch(() => null),
  ]);

  if (!known || !same([known, knownActivity], [data, activity])) {
    paintProfile(data, activity);
  }
}

function paintProfile(data, activity) {
  render(`
    <h1>Профиль</h1>

    <div class="row">
      <div class="grow">
        <div class="lead">${escape(data.user.name)}</div>
        <div class="hint num">${weight(data.user.weight)} кг</div>
      </div>
      <button class="btn secondary small" id="edit">Изменить</button>
    </div>

    <div class="section-title">Всего</div>
    <div class="stats">
      ${stat(data.total.sessions, 'тренировок')}
      ${stat(data.total.sets, 'подходов')}
      ${stat(volume(data.total.volume), 'поднято')}
    </div>

    ${heatmap(activity)}

    <div class="section-title">Разделы</div>

    <!-- Программы стоят первыми: из трёх разделов это единственный, где что-то
         настраивают, — остальные два только показывают уже накопленное. -->
    <button class="list-item" data-go="/programs">
      <span class="grow"><span class="title">Программы</span><br>
        <span class="sub">${programsSub(data.programs)}</span></span>
      <span class="chev">›</span>
    </button>

    <button class="list-item" data-go="/records">
      <span class="grow"><span class="title">Рекорды</span><br>
        <span class="sub">Личные максимумы и прогресс</span></span>
      <span class="chev">›</span>
    </button>

    <button class="list-item" data-go="/history">
      <span class="grow"><span class="title">История тренировок</span><br>
        <span class="sub">${plural(data.total.sessions, 'тренировка', 'тренировки', 'тренировок')}</span></span>
      <span class="chev">›</span>
    </button>
  `);

  onAction('[data-go]', (node) => go(node.dataset.go));

  on('#edit', 'click', () => {
    const form = sheet(`
      <h2>Профиль</h2>
      <div class="field">
        <label>Имя</label>
        <input type="text" id="name" maxlength="20" value="${escape(data.user.name)}">
      </div>
      <div class="field">
        <label>Вес, кг</label>
        <input type="number" id="weight" inputmode="decimal" step="0.5" value="${weight(data.user.weight)}">
      </div>
      <button class="btn" id="save">Сохранить</button>
    `);

    form.node.querySelector('#save').onclick = async () => {
      const name = form.node.querySelector('#name').value.trim();
      const value = parseFloat(form.node.querySelector('#weight').value);
      if (!name || !(value > 0)) return;

      await api.updateProfile(name, value);
      form.close();
      haptic('success');
      profileScreen();
    };
  });
}

/**
 * Подпись строки «Программы».
 *
 * Имя активной программы, а не «N программ»: активная всегда одна, и знать
 * пользователь хочет именно её. Число программ — вторично и на экране списка видно.
 */
function programsSub(programs) {
  if (!programs) return 'Упражнения по дням недели';
  if (programs.active) return escape(programs.active);
  return programs.count
    ? plural(programs.count, 'программа', 'программы', 'программ') + ' · ни одна не активна'
    : 'Программы ещё нет';
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
                'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/**
 * Календарь тренировок «квадратиками», как на GitHub.
 *
 * Сетка раскладывается по колонкам (grid-auto-flow: column) на семь строк, а сервер
 * отдаёт дни подряд начиная с понедельника — поэтому день недели совпадает с номером
 * строки сам собой, без единого вычисления на клиенте.
 */
function heatmap(activity) {
  if (!activity || !activity.days.length) return '';

  /*
    Насыщенность клетки — КВАРТИЛЬ дня среди своих же тренировочных дней, как на
    GitHub, а не доля от максимума.

    Доля от максимума проигрывает на выбросах: один марафонский день на 60 подходов
    прижимает обычные 15 к отметке 0.25, и вся карта уходит в самый бледный уровень —
    хотя эти 15 и есть типичная тренировка. Квартиль сравнивает день не с рекордом,
    а с распределением: цвет означает «больше половины моих дней», и шкала занята
    целиком при любом разбросе.

    Пороги считаются только по дням с тренировками: нули — это фон карты, а не
    нижняя четверть активности, иначе они утянули бы границы вниз.
  */
  const counts = activity.days.map((d) => d.sets).filter(Boolean).sort((a, b) => a - b);
  const at = (p) => counts[Math.min(counts.length - 1, Math.floor(counts.length * p))];
  const [q1, q2, q3] = [at(0.25), at(0.5), at(0.75)];

  const level = (sets) => {
    if (!sets) return 0;
    // Разброса нет — все тренировки одинаковы по объёму, и делить их не на что:
    // ровная средняя заливка честнее, чем сплошной первый квартиль.
    if (q1 === q3) return 3;
    if (sets <= q1) return 1;
    if (sets <= q2) return 2;
    if (sets <= q3) return 3;
    return 4;
  };

  const days = activity.days;
  const columns = Math.ceil(days.length / 7);

  /*
    Хвост текущей недели. Сервер отдаёт дни строго по сегодняшний, а сетка течёт
    по колонкам на семь строк — поэтому последняя колонка обрывалась на середине:
    в понедельник от неё оставался ровно один квадрат, да ещё и обведённый как
    «сегодня», и он висел над пустотой, читаясь как сбой отрисовки.

    Достраиваем недостающие дни на клиенте: данных в них нет и быть не может, это
    чистая геометрия сетки, и в ответе API таким клеткам делать нечего. Фон у них
    прозрачный, а не `--heat-0`, иначе «завтра» стало бы неотличимо от «отдыхал».
  */
  const tail = '<span class="future"></span>'.repeat(columns * 7 - days.length);

  // Месяц подписываем только там, где он начинается, — иначе подписи сольются.
  let previousMonth = -1;
  const months = Array.from({ length: columns }, (_, column) => {
    const day = days[column * 7];
    if (!day) return '<span></span>';

    const month = new Date(day.date).getMonth();
    if (month === previousMonth) return '<span></span>';

    previousMonth = month;
    return `<span>${MONTHS[month]}</span>`;
  }).join('');

  const cells = days.map((day) => {
    const caption = day.sets
      ? `${shortDate(day.date)}: ${plural(day.sets, 'подход', 'подхода', 'подходов')}`
      : `${shortDate(day.date)} — без тренировки`;

    return `<span data-l="${level(day.sets)}"${day.date === activity.today ? ' class="today"' : ''}
                  title="${escape(caption)}"></span>`;
  }).join('');

  return `
    <div class="section-title">Активность</div>
    <p class="subtitle">
      ${plural(activity.active_days, 'тренировка', 'тренировки', 'тренировок')}
      за ${plural(activity.weeks, 'неделю', 'недели', 'недель')}
    </p>

    <div class="heat-months">${months}</div>
    <div class="heat-grid">${cells}${tail}</div>

    <div class="heat-legend">
      меньше
      <i data-l="0"></i><i data-l="1"></i><i data-l="2"></i><i data-l="3"></i><i data-l="4"></i>
      больше
    </div>
  `;
}

/** Три числа в ряд: они и есть содержимое профиля, поэтому набраны как цифры. */
function stat(value, label) {
  return `
    <div class="stat">
      <div class="v">${value}</div>
      <div class="k">${label}</div>
    </div>
  `;
}

export async function historyScreen() {
  const { sessions } = await api.history();

  render(`
    <h1>История</h1>

    ${sessions.length ? '' : '<div class="empty">Тренировок пока не было</div>'}

    ${sessions.map((session) => `
      <button class="list-item" data-session="${session.id}">
        <span class="grow">
          <span class="title">${escape(formatDate(session.date))}</span><br>
          <span class="sub">
            ${plural(session.exercises, 'упражнение', 'упражнения', 'упражнений')} ·
            ${plural(session.sets, 'подход', 'подхода', 'подходов')} ·
            ${volume(session.volume)}
          </span>
        </span>
        <span class="chev">›</span>
      </button>
    `).join('')}
  `);

  onAction('[data-session]', (node) => go(`/history/${node.dataset.session}`));
}

export async function sessionScreen({ id }) {
  const data = await api.historyDetail(id);

  render(`
    <h1>${escape(formatDate(data.session.date))}</h1>
    <p class="subtitle">
      ${plural(data.session.sets, 'подход', 'подхода', 'подходов')} · ${volume(data.session.volume)}
    </p>

    ${data.exercises.map((exercise) => `
      <div class="section-title">${escape(exercise.name)}</div>
      ${exercise.sets.map((set, i) => `
        <div class="set-chip">
          <span class="n">Подход ${i + 1}</span>
          <span class="v">${weight(set.weight)} кг × ${set.reps}</span>
        </div>
      `).join('')}
    `).join('')}
  `);
}

export async function recordsScreen() {
  const { records } = await api.stats();

  render(`
    <h1>Рекорды</h1>

    ${records.length ? '' : `
      <div class="empty">
        <p>Рекордов пока нет.</p>
        <p class="hint">Проведите первую тренировку.</p>
      </div>
    `}

    ${records.map((record) => `
      <button class="list-item" data-exercise="${record.exercise_id}">
        <span class="grow">
          <span class="title">${escape(record.name)}</span><br>
          <span class="sub">лучший подход: ${volume(record.max_volume)} объёма</span>
        </span>
        <span class="lead num">${weight(record.max_weight)} кг</span>
      </button>
    `).join('')}
  `);

  onAction('[data-exercise]', (node) => go(`/progress/${node.dataset.exercise}`));
}

export async function progressScreen({ id }) {
  const data = await api.exerciseProgress(Number(id));
  const points = data.points;
  const dates = points.map((p) => p.date);

  render(`
    <h1>${escape(data.name)}</h1>
    <p class="subtitle">
      ${plural(points.length, 'тренировка', 'тренировки', 'тренировок')} с этим упражнением
    </p>

    ${points.length < 2 ? `
      <div class="empty">
        <p>Для графика нужно хотя бы две тренировки.</p>
      </div>
    ` : `
      <div class="section-title">Рабочий вес, кг</div>
      ${lineChart(points.map((p) => p.max_weight), dates)}

      <div class="section-title">Объём за тренировку, кг</div>
      ${lineChart(points.map((p) => p.volume), dates, true)}
    `}

    <div class="section-title">По тренировкам</div>
    ${points.slice().reverse().map((point) => `
      <div class="set-chip">
        <span class="n">${escape(formatDate(point.date))}</span>
        <span class="v">${weight(point.max_weight)} кг · ${volume(point.volume)}</span>
      </div>
    `).join('')}
  `);
}

/**
 * Линейный график на голом SVG.
 *
 * Библиотеку тянуть незачем: точек десятки, а внешние скрипты в Mini App — это ещё
 * и лишняя загрузка на телефоне в зале, где интернет обычно так себе.
 *
 * Пропорции не растягиваем: раньше стоял preserveAspectRatio="none", и на широком
 * экране вместе с картинкой растягивались подписи и толщина линии. Теперь высота
 * считается от ширины, а SVG остаётся собой.
 */
function lineChart(values, dates, alt = false) {
  const W = 320;
  const H = 170;
  const LEFT = 30;
  const RIGHT = 10;
  const TOP = 14;
  const BOTTOM = 28;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;

  const x = (i) => LEFT + (i * (W - LEFT - RIGHT)) / Math.max(1, values.length - 1);
  const y = (v) => TOP + (1 - (v - min) / span) * (H - TOP - BOTTOM);

  const line = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const area = `${line} L${x(values.length - 1).toFixed(1)},${H - BOTTOM} L${LEFT},${H - BOTTOM} Z`;

  const dots = values
    .map((v, i) => `<circle class="dot" cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3"></circle>`)
    .join('');

  return `
    <svg class="chart${alt ? ' alt' : ''}" viewBox="0 0 ${W} ${H}"
         role="img" aria-label="График: от ${Math.round(min)} до ${Math.round(max)}">
      <line class="grid" x1="${LEFT}" y1="${TOP}" x2="${W - RIGHT}" y2="${TOP}"></line>
      <line class="grid" x1="${LEFT}" y1="${H - BOTTOM}" x2="${W - RIGHT}" y2="${H - BOTTOM}"></line>

      <path class="area" d="${area}"></path>
      <path class="line" d="${line}"></path>
      ${dots}

      <text class="label" x="0" y="${TOP + 3}">${Math.round(max)}</text>
      <text class="label" x="0" y="${H - BOTTOM + 3}">${Math.round(min)}</text>

      <text class="label" x="${LEFT}" y="${H - 8}">${escape(shortDate(dates[0]))}</text>
      <text class="label" x="${W - RIGHT}" y="${H - 8}" text-anchor="end">
        ${escape(shortDate(dates[dates.length - 1]))}
      </text>
    </svg>
  `;
}
