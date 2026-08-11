/**
 * Перелистывание разделов пальцем.
 *
 * Вести можно двумя способами, и оба сводятся к одной величине — доле пути
 * до соседнего раздела:
 *
 *   * по странице — палец влево, страницы едут влево, справа приходит следующий
 *     раздел. Пузырёк при этом движется НАВСТРЕЧУ пальцу: он показывает не палец,
 *     а то, куда переезжает выделение;
 *
 *   * по нижней панели — тянем сам пузырёк, и он идёт ЗА пальцем, как в iOS.
 *     Страницы едут в обратную сторону, потому что уехать вправо по вкладкам
 *     значит сдвинуть содержимое влево.
 *
 * Разные знаки здесь не непоследовательность, а разная природа жеста: в первом
 * случае пальцем двигают страницу, во втором — сам индикатор.
 */
import * as nav from './nav.js';
import { assume, paintInto } from './router.js';
import { haptic } from './tg.js';
import { activePane, idlePane, swapPanes } from './ui.js';

const viewport = document.getElementById('viewport');
const track = document.getElementById('track');
const navBar = document.getElementById('nav');

/*
  Прогноз точки остановки — формула инерционной прокрутки Apple («Designing Fluid
  Interfaces»): projected = position + velocity * rate / (1 - rate), где rate это
  decelerationRate обычной прокрутки, 0.998. При скорости в долях за миллисекунду
  множитель равен rate / (1 - rate) ≈ 499 мс свободного хода.

  Решение принимается по ПРОГНОЗУ, а не по пройденному пути. Раньше отдельно
  сравнивались расстояние и скорость — и быстрый короткий бросок откатывался
  назад, хотя рука уже считала переход состоявшимся.
*/
const PROJECTION = 0.998 / (1 - 0.998);

/**
 * Насколько далеко должен «долететь» прогноз, чтобы переход состоялся.
 *
 * Не половина: строгая середина ощущается сопротивлением, потому что человек
 * отпускает раньше, чем доводит до неё. Треть пути — та граница, на которой
 * намерение уже однозначно.
 */
const COMMIT = 0.34;

/**
 * Сглаживание скорости.
 *
 * Скорость по одному последнему отрезку между событиями — это шум: палец перед
 * отпусканием подрагивает, и мгновенный замер запросто даёт около нуля. Вес
 * свежего замера высокий, чтобы бросок всё же читался мгновенно.
 */
const SMOOTH = 0.6;

/** Палец замер перед отпусканием — значит бросок отменён, инерции нет. */
const STALE_MS = 90;

/** Во сколько раз горизонталь должна превышать вертикаль (только для жеста по странице). */
const ANGLE = 1.3;

/** Сопротивление у крайних разделов: тянется, но втрое тяжелее. */
const RESIST = 0.33;

/* ------------------------------------------------------------------ состояние */

let origin = 0;        // раздел, который сейчас лежит в ПОКАЗАННОЙ панели
let fraction = 0;      // сдвиг относительно origin: +1 это ровно следующий
let velocity = 0;      // доля пути в миллисекунду, по последнему отрезку

let startX = 0;
let startY = 0;
let lastX = 0;
let lastAt = 0;

let base = 0;          // абсолютная позиция, от которой считается сдвиг пальца
let homeIndex = 0;     // раздел, на котором взаимодействие началось (для прокрутки)

let source = null;     // 'page' | 'nav'
let tracking = false;
let sliding = false;
let decided = false;
let lifted = false;    // панели подняты: идёт жест или его доводка
let suppressClick = false;

/**
 * Номер текущей доводки.
 *
 * Прерывание пружины — это не «остановить», а «объявить недействительной»:
 * её кадр может быть уже запланирован, и он не должен ни рисовать, ни, тем более,
 * доводить дело до подмены панелей.
 */
let settleId = 0;

let frame = 0;
let peekPath = null;
let scrollWas = 0;

/* ------------------------------------------------------------------ помощники */

function currentPath() {
  return location.hash.slice(1) || '/';
}

function sectionIndex() {
  return nav.SECTIONS.indexOf(nav.sectionOf(currentPath()));
}

function width() {
  return viewport.clientWidth || window.innerWidth || 1;
}

/** Ширина одной вкладки — шаг, которым пузырёк ходит по панели. */
function tabWidth() {
  return (navBar.clientWidth || width()) / nav.SECTIONS.length;
}

/**
 * Идущая доводка НЕ запрещает новый жест.
 *
 * Раньше здесь стоял флаг «занято», и пока пружина доигрывала, касание просто
 * игнорировалось: при быстром листании каждый второй жест пропадал, а на экране
 * лишь дёргалась текущая страница. Анимация обязана прерываться.
 */
function allowed() {
  return nav.isVisible()
    && (lifted || sectionIndex() >= 0)
    && !document.querySelector('.sheet-backdrop');
}

/* ------------------------------------------------------------------ отрисовка */

/**
 * Применить долю пути.
 *
 * Всё рисование собрано здесь и вызывается из одного requestAnimationFrame:
 * touchmove сыплется чаще, чем кадры, и запись стилей на каждое событие давала
 * рваное движение — браузер пересчитывал раскладку по несколько раз за кадр.
 */
function paint() {
  frame = 0;
  track.style.transform = `translate3d(${-fraction * width()}px, 0, 0)`;
  nav.at(origin + fraction);
}

function schedule() {
  if (!frame) frame = requestAnimationFrame(paint);
}

/**
 * Переосновывание: доехавшая соседняя панель становится показанной.
 *
 * Без него жест упирался в одну соседнюю панель: протащив пузырёк дальше, дорожка
 * уезжала на две ширины, а нарисована ровно одна — за ней была пустота. Теперь
 * пройденный раздел «защёлкивается» прямо во время жеста, и вести можно через всю
 * панель, сколько угодно вкладок подряд.
 */
function rebase(step) {
  const leaving = activePane();
  swapPanes();

  leaving.innerHTML = '';
  leaving.hidden = true;
  leaving.classList.remove('peek-next', 'peek-prev');

  const shown = activePane();
  shown.classList.remove('peek-next', 'peek-prev');
  shown.style.top = '0px';   // приехавший экран показывается с начала

  origin += step;
  peekPath = null;
}

/** raw — сколько разделов прошёл палец от начала жеста. */
function set(raw) {
  const last = nav.SECTIONS.length - 1;
  let total = base + raw;

  // За крайними разделами тянем с сопротивлением: видно, что дальше некуда.
  if (total < 0) total *= RESIST;
  else if (total > last) total = last + (total - last) * RESIST;

  fraction = total - origin;

  while (fraction > 1 && origin < last) {
    rebase(1);
    fraction = total - origin;
  }
  while (fraction < -1 && origin > 0) {
    rebase(-1);
    fraction = total - origin;
  }

  // Дальше одной панели не пускаем: соседняя нарисована ровно одна.
  fraction = Math.max(-1, Math.min(1, fraction));
  schedule();
}

/* ------------------------------------------------------------------ соседняя панель */

async function preparePeek() {
  const step = fraction > 0 ? 1 : -1;
  const path = nav.SECTIONS[origin + step];
  if (!path || path === peekPath) return;

  const pane = idlePane();
  pane.hidden = false;
  pane.classList.remove('peek-next', 'peek-prev');
  pane.classList.add(step > 0 ? 'peek-next' : 'peek-prev');
  peekPath = path;

  // Заглушка на случай, когда данных ещё нет в кэше: экран из кэша рисуется
  // синхронно и затрёт её в том же кадре, а при первом заходе в раздел без неё
  // под палец выехала бы пустота.
  pane.innerHTML = '<div class="center-screen"><div class="spinner"></div></div>';

  // Сорванный запрос не должен ломать жест — панель просто вернётся назад.
  await paintInto(path, pane).catch(() => {});
}

function clearPeek() {
  const pane = idlePane();
  pane.hidden = true;
  pane.innerHTML = '';
  pane.classList.remove('peek-next', 'peek-prev');
  peekPath = null;
}

/* ------------------------------------------------------------------ режим жеста */

function lift() {
  if (lifted) {
    /*
      Перехватываем собственную доводку. Панели уже подняты и стоят где-то между
      разделами — продолжаем ровно с этой точки, а не с начала: иначе страница
      прыгнула бы под пальцем в момент касания.
    */
    settleId += 1;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    base = origin + fraction;
    return;
  }

  lifted = true;
  fraction = 0;
  scrollWas = window.scrollY;
  homeIndex = origin;
  base = origin;

  viewport.style.height = `${window.innerHeight}px`;
  viewport.classList.add('swiping');
  activePane().style.top = `${-scrollWas}px`;

  nav.hold(true);
}

function drop() {
  lifted = false;

  viewport.classList.remove('swiping');
  viewport.style.height = '';
  track.style.transform = '';

  for (const pane of [activePane(), idlePane()]) {
    pane.style.top = '';
    pane.classList.remove('peek-next', 'peek-prev');
  }

  nav.hold(false);
}

/**
 * Доводка пружиной.
 *
 * Раньше здесь был CSS-переход фиксированной длительности — и именно он давал
 * ощущение жёсткого магнита: переход всегда начинается с НУЛЕВОЙ скорости, что бы
 * ни делал палец. Быстро бросил — движение спотыкается на стыке; медленно отпустил —
 * экран внезапно дёргается. Длительность зависела от расстояния, но не от скорости,
 * а важна как раз она.
 *
 * Пружина продолжает движение с той скоростью, с которой отпустили. Затухание
 * критическое: приходит к цели быстро и БЕЗ перелёта — качание вокруг вкладки
 * выглядело бы игрушечно.
 */
/*
  Жёсткость подобрана так, чтобы доводка не ощущалась ожиданием: при критическом
  затухании путь занимает примерно 4/sqrt(k) секунды — около 250 мс на полном
  экране и заметно меньше на остатке. Мягче было бы красиво, но чувствовалось бы
  как задержка; жёстче — как щелчок.
*/
const STIFFNESS = 260;
const DAMPING = 2 * Math.sqrt(STIFFNESS);   // критическое затухание, без перелёта

function settleTo(target, done) {
  if (frame) cancelAnimationFrame(frame);
  frame = 0;

  const id = ++settleId;

  // Пузырёк ведём покадрово вместе с панелями: свой CSS-переход ему тут только мешал бы.
  nav.hold(true);

  let speed = velocity * 1000;   // доля пути в секунду
  let last = performance.now();

  const tick = (now) => {
    // Жест перехватил доводку — этот кадр уже никого не касается.
    if (id !== settleId) return;

    // Ограничение шага: после сворачивания приложения между кадрами проходят
    // секунды, и пружина без него улетела бы за цель одним прыжком.
    const dt = Math.min(0.032, Math.max(0.001, (now - last) / 1000));
    last = now;

    speed += (-STIFFNESS * (fraction - target) - DAMPING * speed) * dt;
    fraction += speed * dt;

    const settled = Math.abs(fraction - target) < 0.002 && Math.abs(speed) < 0.02;
    if (settled) fraction = target;

    track.style.transform = `translate3d(${-fraction * width()}px, 0, 0)`;
    nav.at(origin + fraction);

    if (settled) {
      frame = 0;
      done();
      return;
    }
    frame = requestAnimationFrame(tick);
  };

  frame = requestAnimationFrame(tick);
}

/* ------------------------------------------------------------------ жест */

function begin(event, kind) {
  tracking = false;
  sliding = false;
  decided = false;
  velocity = 0;

  /*
    fraction здесь НЕ обнуляется.

    Касание может прийти посреди доводки — панели в этот момент физически стоят
    между разделами, и обнулить сдвиг значит соврать самим себе: точка отсчёта
    уехала бы к текущему разделу, и сколько ни веди дальше, страница возвращалась
    бы на место. Снаружи это выглядело как залипание на одном разделе при быстром
    листании. Сдвиг сбрасывает lift(), и только когда жест действительно начат
    с нуля.
  */

  if (event.touches.length !== 1 || !allowed()) return;

  source = kind;
  startX = event.touches[0].clientX;
  startY = event.touches[0].clientY;
  lastX = startX;
  lastAt = performance.now();
  tracking = true;
}

function onMove(event) {
  if (!tracking || event.touches.length !== 1) return;

  const x = event.touches[0].clientX;
  const dx = x - startX;
  const dy = event.touches[0].clientY - startY;

  if (!decided) {
    if (source === 'nav') {
      /*
        По панели ждём именно СДВИГА, а не просто касания. Раньше жест объявлялся
        начатым прямо в touchstart — и обычное нажатие по вкладке уходило в режим
        свайпа, а его click потом глушился как «протащили». Порог маленький: тут
        не с чем конкурировать, панель по вертикали не прокручивается.
      */
      if (Math.abs(dx) < 6) return;
      decided = true;
      sliding = true;
    } else {
      // На странице спорим с вертикальной прокруткой — нужен и порог, и угол.
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;

      decided = true;
      sliding = Math.abs(dx) > Math.abs(dy) * ANGLE;

      if (!sliding) {
        tracking = false;
        return;
      }
    }
    lift();
  }

  event.preventDefault();

  // Знак и масштаб зависят от того, что именно тянут: страницу или пузырёк.
  const next = source === 'nav' ? dx / tabWidth() : -dx / width();

  set(next);
  preparePeek();

  // Скорость — сглаженная по последним замерам, в долях пути за миллисекунду.
  const now = performance.now();
  if (now > lastAt) {
    const step = source === 'nav' ? (x - lastX) / tabWidth() : -(x - lastX) / width();
    const instant = step / (now - lastAt);

    velocity = velocity * (1 - SMOOTH) + instant * SMOOTH;
    lastX = x;
    lastAt = now;
  }
}

function onEnd() {
  if (!tracking) return;
  tracking = false;

  if (!sliding) return;
  sliding = false;

  if (source === 'nav') suppressClick = true;

  // Замерший перед отпусканием палец инерции не даёт: это не бросок, а остановка.
  if (performance.now() - lastAt > STALE_MS) velocity = 0;

  // Куда движение доехало бы само, если отпустить и дать инерции затухнуть.
  const projected = fraction + velocity * PROJECTION;

  const step = projected > 0 ? 1 : -1;
  const ahead = nav.SECTIONS[origin + step];

  // Доводим до соседнего раздела, только если он и правда нарисован.
  const takeIt = ahead && peekPath === ahead && Math.abs(projected) > COMMIT;
  finish(takeIt ? step : 0);
}

/**
 * Доводка и фиксация результата.
 *
 * offset — куда встаём относительно показанной панели: 0 значит «остаёмся», ±1 —
 * «переезжаем на соседний». Ноль здесь НЕ равен «ничего не произошло»: жест мог
 * по дороге переосноваться на другой раздел, и тогда адрес всё равно надо привести
 * в соответствие с тем, что человек видит.
 */
function finish(offset) {
  if (offset) haptic('light');

  const landing = origin + offset;

  settleTo(offset, () => {
    if (offset) {
      // Панели меняются ролями: содержимое не переносится, обработчики живы.
      const leaving = activePane();
      swapPanes();

      leaving.innerHTML = '';
      leaving.hidden = true;
      leaving.classList.remove('peek-next', 'peek-prev');
      origin = landing;
    }

    clearPeek();
    drop();

    const path = nav.SECTIONS[landing];

    // Прокрутку возвращаем, только если пришли ровно туда, откуда начинали.
    window.scrollTo(0, landing === homeIndex ? scrollWas : 0);

    // Адрес меняем последним: экран уже нарисован, повторный запрос не нужен.
    // Сравниваем с адресом, а не с началом жеста: доводку могли прервать, и
    // раздел под пальцем успел разойтись с тем, что записано в истории.
    if (path !== nav.sectionOf(currentPath())) assume(path);
  });
}

/* ------------------------------------------------------------------ подключение */

export function start() {
  /*
    Раздел под пальцем ведём сами, а не вычитываем из адреса на каждый жест.

    После свайпа адрес обновляется через hashchange — то есть в следующей задаче.
    Быстрое листание успевает начать новый жест раньше, и чтение адреса вернуло бы
    ПРЕДЫДУЩИЙ раздел: соседним считался бы тот, что уже показан, и переход никуда
    бы не вёл. Поэтому адрес здесь — не источник правды, а лишь способ узнать
    о переходах, сделанных мимо нас: нажатием по вкладке, кнопкой «назад», ссылкой.
  */
  origin = Math.max(0, sectionIndex());

  window.addEventListener('hashchange', () => {
    if (lifted) return;   // во время жеста адрес ведём мы сами
    const index = sectionIndex();
    if (index >= 0) origin = index;
  });

  // Слушаем документ, а не панель экрана: она короче окна, и жест, начатый ниже
  // содержимого, до неё бы не дошёл.
  document.addEventListener('touchstart', (e) => {
    if (navBar.contains(e.target)) return;   // по панели — свой обработчик ниже

    // Элементы со своим жестом отписываются от листания разделов. Сейчас это
    // ручка перетаскивания упражнения: она тянется вертикально, и хотя решение
    // «вбок или вниз» принимается по направлению, старт всё равно взводил бы
    // отслеживание — а любой дрожащий палец давал бы подёргивание страницы вбок.
    if (e.target.closest?.('[data-swipe="off"]')) return;

    begin(e, 'page');
  }, { passive: true });

  navBar.addEventListener('touchstart', (e) => begin(e, 'nav'), { passive: true });

  // passive: false — здесь нужен preventDefault, иначе экран поедет и вбок, и вниз.
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('touchend', onEnd, { passive: true });
  document.addEventListener('touchcancel', onEnd, { passive: true });

  // Протащили пузырёк — это не нажатие по вкладке. Гасим в фазе перехвата,
  // до собственного обработчика панели.
  navBar.addEventListener('click', (e) => {
    if (!suppressClick) return;
    suppressClick = false;
    e.stopPropagation();
    e.preventDefault();
  }, true);
}
