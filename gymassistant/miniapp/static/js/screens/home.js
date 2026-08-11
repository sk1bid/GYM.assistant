/**
 * Главный экран: что сегодня и куда идти дальше.
 *
 * В боте это было меню уровня 0 из трёх картинок с подписями. Здесь на первом экране
 * сразу видно тренировку сегодняшнего дня — то, зачем приложение и открывают.
 *
 * Приветствия наверху нет намеренно: имя пользователь и так знает, а строка с ним
 * отодвигала вниз единственное, ради чего экран открыт. День недели — заголовок,
 * ближайшее упражнение — акцентный блок, остальное — список на разделителях.
 */
import { api, cached } from './../api.js';
import { go } from './../router.js';
import * as rest from './../rest.js';
import { alert } from './../tg.js';
import { escape, on, onAll, plural, render, same, sheet } from './../ui.js';
import { createProgram } from './programs.js';

/**
 * Иконки — инлайновый SVG в стиле навигации (24×24, stroke 1.8, currentColor),
 * а не эмодзи и не глифы вроде «↻»: эмодзи рисуются шрифтом системы и разнятся по
 * платформам, а «↻» читалось как «обновить» и было залито акцентом без причины.
 */
function icon(paths, size = 20, sw = 1.8) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="${sw}" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}
const ICON = {
  dumbbell: icon('<path d="M6.5 8v8M4 10.5v3M17.5 8v8M20 10.5v3M6.5 12h11"/>'),
  repeat:   icon('<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>', 16),
  clock:    icon('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', 18),
  calendar: icon('<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/>', 18),
  flame:    icon('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>', 15),
  check:    icon('<path d="M5 12.5l4.5 4.5L19 7"/>', 22, 2.4),
  play:     '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
};

export async function homeScreen() {
  // Если экран уже открывали в этот заход, рисуем его немедленно из последнего
  // ответа и только потом идём на сервер: переход перестаёт упираться в запрос.
  const known = cached('api/bootstrap');
  if (known) paint(known);

  const data = await api.bootstrap();
  rest.sync(data.rest);

  // Перерисовываем, только если данные реально другие: лишний render() сбрасывает
  // обработчики и заставляет экран моргать там, где ничего не изменилось.
  if (!known || !same(known, data)) paint(data);
}

function paint(data) {
  render(todayBlock(data));

  on('#start', 'click', () => go(startPath(data)));
  on('#other-day', 'click', pickDay);
  on('#new-program', 'click', createProgram);
  on('#do-missed', 'click', () => go(`/workout/${data.missed.id}`));
  onAll('[data-go]', 'click', (node) => go(node.dataset.go));
}

/**
 * Выбор дня для внеплановой тренировки.
 *
 * Сервер это умел с самого начала — /training/start принимает любой день программы, —
 * но попасть туда можно было только через расписание, открыв нужный день и найдя
 * внизу кнопку. В день отдыха главный экран и вовсе оказывался тупиком: «упражнений
 * не запланировано» и всё.
 *
 * Дни берутся из того же ответа, что рисует расписание: он почти всегда уже в кэше,
 * поэтому шторка открывается мгновенно.
 */
async function pickDay() {
  const { days } = cached('api/schedule') || await api.schedule();
  const trainable = (days || []).filter((day) => day.exercises.length);

  if (!trainable.length) {
    return alert('В программе нет дней с упражнениями.');
  }

  const form = sheet(`
    <h2>Какой день тренируем?</h2>
    <p class="hint">Тренировка запишется сегодняшним числом — расписание не сдвинется.</p>
    ${trainable.map((day) => `
      <button class="list-item" data-pick="${day.id}">
        <span class="grow">
          <span class="title">${escape(day.day_of_week)}</span><br>
          <span class="sub">${escape(day.exercises.map((e) => e.name).join(', '))}</span>
        </span>
        <span class="chev">›</span>
      </button>
    `).join('')}
  `);

  form.node.querySelectorAll('[data-pick]').forEach((button) => {
    button.onclick = () => {
      form.close();
      go(`/workout/${button.dataset.pick}`);
    };
  });
}

function startPath(data) {
  // Незавершённая тренировка продолжается с того же места, а не начинается заново.
  return data.active ? '/workout' : `/workout/${data.today.id}`;
}

/**
 * Идущая тренировка — весь экран целиком.
 *
 * Пока она не закрыта, главная говорит про неё, а не про календарь. Раньше про
 * тренировку знала только ветка «сегодня есть упражнения», а тренируют не
 * обязательно сегодняшний день: пропущенный отрабатывают кнопкой, любой другой
 * выбирают шторкой. Отсюда две беды сразу. В день отдыха экран уходил в ветку
 * «упражнений не запланировано» и о тренировке не говорил ни слова — вернуться
 * в неё с главной было нельзя вовсе (в навигации экрана тренировки нет), только
 * закрыть и открыть приложение заново. А в обычный день заголовок, счётчик и
 * список упражнений оставались СЕГОДНЯШНИМИ, хотя отрабатывалась суббота.
 *
 * Поэтому здесь всё — про тренируемый день: его имя в заголовке, его упражнения
 * в списке, выделено то, что делать сейчас. Сегодняшнее число не теряется: оно
 * уходит в подпись, когда день не совпадает.
 */
function activeBlock(data) {
  const active = data.active;
  const day = active.day;
  const exercises = day?.exercises || [];
  const elsewhere = day && day.day_of_week !== data.today_name;

  return `
    <div class="overline flush">тренировка идёт</div>
    <h1>${escape(day ? day.day_of_week : 'Тренировка')}</h1>
    <p class="subtitle">
      ${active.total ? `${active.done} из ${active.total} подходов` : ''}
      ${elsewhere ? ` · сегодня ${escape(data.today_name.toLowerCase())}` : ''}
    </p>

    ${exercises.length ? sessionCard(exercises, active.next_exercise_id) : ''}

    <button class="btn mt-4" id="start">
      ${ICON.play}
      Продолжить тренировку
    </button>

    ${weeklyCard(data.week)}
  `;
}

function todayBlock(data) {
  // Идущая тренировка старше всех остальных состояний экрана: и «нет программы»,
  // и «день отдыха» рассказывали бы не про то, чем человек занят прямо сейчас.
  if (data.active) return activeBlock(data);

  if (!data.has_program) {
    // Кнопка открывает саму форму, а не список программ. Вести на список было
    // нечестно: там пусто и стоит такая же кнопка — лишний экран ровно на том шаге,
    // где человек видит приложение впервые.
    return `
      <h1>Программы ещё нет</h1>
      <p class="subtitle">Возьмите готовую программу или соберите свою —
      и приложение будет вести вас подход за подходом.</p>
      <button class="btn" id="new-program">Создать программу</button>
    `;
  }

  const today = data.today;

  if (!today || !today.exercises.length) {
    return `
      <div class="overline flush">сегодня</div>
      <h1>${escape(data.today_name)}</h1>
      <p class="subtitle">Упражнений не запланировано — день отдыха.</p>

      ${missedRow(data.missed)}

      <!-- В день отдыха это главная кнопка экрана: раньше здесь был тупик —
           «не запланировано» и ссылка на расписание. -->
      <button class="btn mt-3" id="other-day">Тренироваться по другому дню</button>
      <button class="btn secondary mt-2" data-go="/schedule">Открыть расписание</button>

      ${weeklyCard(data.week)}
    `;
  }

  const exercises = today.exercises;
  const sets = exercises.reduce((sum, e) => sum + e.sets, 0);

  return `
    <div class="overline flush">сегодня</div>
    <h1>${escape(data.today_name)}</h1>
    <p class="subtitle">
      ${plural(exercises.length, 'упражнение', 'упражнения', 'упражнений')} ·
      ${plural(sets, 'подход', 'подхода', 'подходов')}
    </p>

    ${sessionCard(exercises, exercises[0]?.id)}

    <button class="btn mt-4" id="start">
      ${ICON.play}
      Начать тренировку
    </button>

    ${missedRow(data.missed)}
    <button class="btn outlined mt-3" id="other-day">
      ${ICON.calendar}
      Тренировать другой день
    </button>

    ${weeklyCard(data.week)}
  `;
}

/**
 * Недельная цель и серия — мотивационная сводка внизу главной.
 *
 * Кольцо показывает прогресс к цели недели (число тренировочных дней в программе);
 * серия — тихой строкой с огоньком и только начиная с двух недель: «серия: 1 неделя»
 * бессмысленна, а отсутствие серии не повод укорять. Когда цель закрыта, кольцо
 * заполнено с галочкой, а серия уходит в акцент — это момент награды.
 *
 * Просить можно только выполнимое. Строка «ещё N тренировок» раньше была разностью
 * цели и сделанного, поэтому в пятницу с нулём отработанных дней требовала четырёх
 * тренировок за два оставшихся дня. Теперь потолок — `week.left`, число ещё не
 * прошедших тренировочных дней программы: цель в кольце остаётся честной (0/4),
 * а просьба сходится с календарём. Когда таких дней не осталось вовсе, экран
 * перестаёт требовать и просто закрывает неделю.
 *
 * Грейс серии не подписан намеренно. «0/4» рядом с «серия: 6 недель» кажется
 * противоречием, только если вчитываться, а пояснение «держится до конца недели»
 * стоило второй строки и объясняло механику вместо того, чтобы показывать факт.
 */
function weeklyCard(week) {
  if (!week || !week.goal) return '';

  const met = week.done >= week.goal;
  const short = Math.max(0, week.goal - week.done);
  const ahead = Number.isInteger(week.left) ? week.left : short;
  const ask = Math.min(short, ahead);

  const R = 23;
  const C = 2 * Math.PI * R;
  const done = Math.min(week.done, week.goal);
  const offset = met ? 0 : C * (1 - done / week.goal);

  const streak = week.streak >= 2
    ? `<div class="streak${met ? ' on' : ''}">${ICON.flame}
         Серия: ${plural(week.streak, 'неделя', 'недели', 'недель')}</div>`
    : (met ? '' : '<div class="hint mt-1">до цели недели</div>');

  return `
    <div class="week-card mt-4">
      <div class="week-ring">
        <svg viewBox="0 0 54 54">
          <circle class="bg" cx="27" cy="27" r="${R}"/>
          <circle class="fg" cx="27" cy="27" r="${R}"
                  stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/>
        </svg>
        <span class="label">${met ? ICON.check : `${done}/${week.goal}`}</span>
      </div>
      <div class="grow">
        <div class="overline flush">эта неделя</div>
        <div class="lead">${met ? 'Цель недели закрыта'
          : ask > 0 ? `Ещё ${plural(ask, 'тренировка', 'тренировки', 'тренировок')}`
          : 'План недели позади'}</div>
        ${streak}
      </div>
    </div>
  `;
}

/**
 * Упражнения дня одним блоком; выделенная строка — то, что делать сейчас.
 *
 * Раньше ближайшее упражнение жило в отдельной акцентной карточке, оторванной от
 * списка остальных, — и выглядело объектом другого сорта, а не первым из четырёх.
 * Теперь это выделенная строка той же карточки: видно, что это все упражнения дня,
 * просто одно из них — ближайшее.
 *
 * `currentId` приходит снаружи, а не считается здесь: до старта это просто первое
 * упражнение, а в идущей тренировке — текущий шаг с сервера, и он вполне может
 * оказаться третьим по списку. Выделять в этом случае первое значило бы врать.
 * null — выделять нечего (план отработан целиком, осталось «Завершить»).
 */
function sessionCard(exercises, currentId) {
  return `
    <div class="session">
      ${exercises.map((e) => (e.id === currentId ? nextRow(e) : exRow(e))).join('')}
    </div>
  `;
}

function nextRow(e) {
  return `
    <div class="ex-line next">
      <span class="chip">${ICON.dumbbell}</span>
      <span class="grow">
        <span class="overline tiny">следующее</span>
        <span class="lead">${escape(e.name)}</span>
      </span>
      <span class="num sets">${sets(e)}</span>
    </div>
  `;
}

function exRow(e) {
  return `
    <div class="ex-line">
      <span class="mark">${e.circle ? ICON.repeat : ''}</span>
      <span class="grow name">${escape(e.name)}</span>
      <span class="num sets">${sets(e)}</span>
    </div>
  `;
}

/** Один формат подходов на весь экран: «3 × 10» с тонкими шпациями. */
function sets(e) {
  return `${e.sets} × ${e.reps}`;
}

/**
 * Пропущенный день — тихой строкой, а не карточкой с собственной кнопкой.
 *
 * Показывается без укора: программа задана днями недели, «пропустил» значит лишь,
 * что день прошёл без тренировки. Отработать можно сегодня — запись ляжет
 * сегодняшним числом, расписание не поедет. Раньше это был второй полноширинный
 * CTA прямо под «Начать тренировку» — две призывные кнопки стопкой спорили за нажатие.
 */
function missedRow(missed) {
  if (!missed) return '';

  const when = missed.days_ago === 1
    ? 'вчера'
    : `${plural(missed.days_ago, 'день', 'дня', 'дней')} назад`;

  return `
    <button class="missed-row mt-4" id="do-missed">
      <span class="ic">${ICON.clock}</span>
      <span class="grow">Пропущено: <b>${escape(missed.day_of_week)}</b> · ${when}</span>
      <span class="do">Отработать →</span>
    </button>
  `;
}

