/**
 * Мелочи для сборки DOM.
 *
 * Фреймворка нет намеренно: экранов десяток, состояние живёт на сервере, а сборка
 * без npm — это ещё и отсутствие пайплайна, который надо чинить через полгода.
 */
import { haptic } from './tg.js';

/**
 * Две панели экрана.
 *
 * Одна показана, вторая ждёт. Во время свайпа соседний раздел рисуется в ждущую
 * и выезжает из-под пальца, а на переходе панели просто МЕНЯЮТСЯ РОЛЯМИ.
 * Содержимое при этом никуда не переносится — а значит, обработчики, навешанные
 * при отрисовке, остаются на своих узлах. Перенос innerHTML их бы потерял.
 */
const panes = [
  document.getElementById('screen-a'),
  document.getElementById('screen-b'),
];

let shown = 0;

/** Куда пишет render(). Обычно — показанная панель, во время свайпа — ждущая. */
let root = panes[0];

export function activePane() {
  return panes[shown];
}

export function idlePane() {
  return panes[1 - shown];
}

/**
 * Нарисовать что-то в указанную панель.
 *
 * Цель держится на всё время асинхронного вызова: экраны навешивают обработчики
 * уже после await, и on()/onAll() должны искать узлы там же, где рисовали.
 */
export async function withPane(pane, draw) {
  const previous = root;
  root = pane;
  try {
    await draw();
  } finally {
    root = previous;
  }
}

/** Ждущая панель становится показанной: свайп доведён до конца. */
export function swapPanes() {
  panes[shown].hidden = true;
  shown = 1 - shown;
  panes[shown].hidden = false;
  root = panes[shown];
}

/** Экранирование: имена упражнений пользователь вводит сам. */
export function escape(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/**
 * Направление следующего перехода — его ставит роутер перед сменой экрана.
 *
 * Именно «намерение», а не свойство рендера: экран тренировки перерисовывает себя
 * после каждого записанного подхода, и если анимировать любой render(), приложение
 * будет дёргаться весь подход. Флаг одноразовый — перерисовки внутри экрана его
 * не видят и проходят без анимации.
 */
let pendingTransition = null;

export function setTransition(direction) {
  pendingTransition = direction;
}

/**
 * Моторика переходов.
 *
 * Кривая — «emphasized decelerate» из Material 3: резкий старт и долгий мягкий
 * выход. Именно длинный хвост читается как плавность; симметричная ease-in-out
 * на коротких дистанциях выглядит вяло.
 *
 * Возврат короче остальных: движение назад должно ощущаться дешевле, чем вперёд.
 */
const EASE_ENTER = 'cubic-bezier(.05, .7, .1, 1)';

const MOTION = {
  right:   { transform: 'translate3d(18px, 0, 0)',  duration: 280 },
  left:    { transform: 'translate3d(-18px, 0, 0)', duration: 280 },
  deep:    { transform: 'scale(.98)',               duration: 260 },
  shallow: { transform: 'scale(1.02)',              duration: 220 },
};

const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');

export function render(html) {
  const pane = root;
  pane.innerHTML = html;

  const direction = pendingTransition;
  pendingTransition = null;

  // Наверх прокручиваем только при СМЕНЕ экрана. Перерисовка на месте — свежие
  // данные поверх показанных из кэша, записанный подход — не должна утаскивать
  // пользователя от того места, куда он смотрел.
  if (direction) {
    window.scrollTo(0, 0);
    enter(pane, direction);
  }

  return pane;
}

/**
 * Анимация появления экрана.
 *
 * Через Web Animations API, а не переключением CSS-класса. Классовый вариант
 * требовал перезапуска анимации, а перезапуск — чтения offsetWidth, то есть
 * ПРИНУДИТЕЛЬНОГО синхронного пересчёта раскладки ровно после замены всего DOM.
 * Это самый дорогой момент кадра, и первые кадры анимации регулярно терялись —
 * получался рывок вместо движения. WAAPI обходится без этого и отдаёт анимацию
 * композитору.
 */
function enter(pane, direction) {
  const motion = MOTION[direction];
  if (!motion || !pane.animate || reducedMotion?.matches) return;

  pane.animate(
    [
      { opacity: 0, transform: motion.transform },
      { opacity: 1, transform: 'none' },
    ],
    { duration: motion.duration, easing: EASE_ENTER, fill: 'backwards' },
  );
}

/**
 * Спиннер.
 *
 * Намеренно мимо render(): он не должен съедать направление перехода — оно
 * принадлежит тому кадру, который принесёт содержимое. Иначе экран въезжал бы
 * дважды: сперва пустой с крутилкой, потом с данными.
 */
export function loading() {
  root.innerHTML = '<div class="center-screen"><div class="spinner"></div></div>';
}

export function errorScreen(message, retry) {
  render(`
    <div class="empty">
      <p>${escape(message)}</p>
      <button class="btn secondary small" id="retry">Ещё раз</button>
    </div>
  `);
  if (retry) on('#retry', 'click', retry);
}

/**
 * Узел в текущей панели.
 *
 * Именно в текущей, а не в документе: панелей две, и во время свайпа одинаковые
 * id живут в обеих. `document.getElementById` вернул бы ту, которая раньше
 * в разметке, — то есть иногда чужую.
 */
export function q(selector) {
  return root.querySelector(selector);
}

/** Обработчик на первый совпавший элемент. */
export function on(selector, event, handler) {
  const node = root.querySelector(selector);
  if (node) node.addEventListener(event, handler);
  return node;
}

/** Обработчик на все совпавшие; в handler приезжает сам элемент. */
export function onAll(selector, event, handler) {
  root.querySelectorAll(selector).forEach((node) => {
    node.addEventListener(event, (e) => handler(node, e));
  });
}

/** Кнопки, которые что-то меняют на сервере: блокируем на время запроса. */
export function onAction(selector, handler) {
  onAll(selector, 'click', async (node, event) => {
    if (node.disabled) return;
    node.disabled = true;
    haptic('light');
    try {
      await handler(node, event);
    } finally {
      node.disabled = false;
    }
  });
}

export function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${n} ${few}`;
  return `${n} ${many}`;
}

export function clock(seconds) {
  const safe = Math.max(0, Math.round(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

/** Вес: 60 вместо 60.0, но 62.5 остаётся 62.5. */
export function weight(value) {
  return Number(value).toFixed(1).replace(/\.0$/, '');
}

export function volume(kg) {
  return kg >= 1000 ? `${(kg / 1000).toFixed(1)} т` : `${Math.round(kg)} кг`;
}

export function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Одинаковы ли два ответа сервера.
 *
 * Сравнение по сериализации: данные тут — простые деревья из JSON, ключи в них
 * идут в одном и том же порядке (их собирает один и тот же код на сервере),
 * поэтому строковое сравнение и корректно, и заметно дешевле обхода.
 */
export function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Дата без времени и года — для подписей на осях графика, где места мало. */
export function shortDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

/*
  Физика доводки шторки — та же, что ведёт свайп разделов (см. swipe.js).
  Критически затухающая пружина продолжает движение со скоростью пальца и приходит
  к цели без перелёта; фиксированный CSS-переход стартовал бы с нулевой скорости и
  на стыке с броском ощущался бы жёстким магнитом. PROJECTION — прогноз точки
  остановки по инерции (формула Apple, decelerationRate 0.998).
*/
const STIFFNESS = 260;
const DAMPING = 2 * Math.sqrt(STIFFNESS);
const PROJECTION = 0.998 / (1 - 0.998);

/**
 * Нижняя шторка — для форм, которые не заслуживают отдельного экрана.
 *
 * Закрывается тремя способами: тапом мимо, программно (close) и стягиванием вниз.
 * У стягивания и отскока — общая пружина, у программного close анимации нет: он
 * вызывается прямо перед переходом на другой экран, где шторке уже нечего доводить.
 */
export function sheet(html) {
  const backdrop = document.createElement('div');
  backdrop.className = 'sheet-backdrop';
  backdrop.innerHTML = `<div class="sheet">${html}</div>`;
  document.body.appendChild(backdrop);

  const panel = backdrop.querySelector('.sheet');

  // Базовая непрозрачность затемнения берётся из темы (--scrim: .32 светлая /
  // .6 тёмная), чтобы стягивание гасило именно её, а не захардкоженное значение.
  const scrim = getComputedStyle(backdrop).backgroundColor.match(/[\d.]+/g) || [];
  const rgb = scrim.slice(0, 3).join(',') || '0,0,0';
  const dimBase = scrim.length === 4 ? +scrim[3] : 0.32;
  const dim = (k) => {
    backdrop.style.backgroundColor = `rgba(${rgb},${(dimBase * Math.max(0, k)).toFixed(3)})`;
  };

  let gone = false;
  const remove = () => { if (!gone) { gone = true; backdrop.remove(); } };

  // Состояние жеста: смещение вниз, скорость (px/мс) и замеры для неё.
  let dragging = false;
  let startY = 0, dragY = 0, lastY = 0, lastAt = 0, vel = 0;
  let raf = 0;

  // Пружина по вертикали в пикселях: к target с начальной скоростью v0 (px/с).
  const springTo = (from, target, v0, done) => {
    cancelAnimationFrame(raf);
    const h = panel.offsetHeight || window.innerHeight;
    let y = from;
    let speed = v0;
    let last = performance.now();
    const tick = (now) => {
      const dt = Math.min(0.032, Math.max(0.001, (now - last) / 1000));
      last = now;
      speed += (-STIFFNESS * (y - target) - DAMPING * speed) * dt;
      y += speed * dt;
      const settled = Math.abs(y - target) < 0.5 && Math.abs(speed) < 5;
      if (settled) y = target;
      dragY = Math.max(0, y);
      panel.style.transform = `translateY(${dragY}px)`;
      dim(1 - dragY / h);
      if (settled) { done && done(); return; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  };

  const dismiss = (v0 = 0) => {
    const h = panel.offsetHeight || window.innerHeight;
    backdrop.style.pointerEvents = 'none';
    springTo(dragY, h * 1.15, Math.max(v0, 600), remove);
  };

  panel.addEventListener('touchstart', (e) => {
    // Тянем шторку только от её верха: ниже палец должен прокручивать содержимое.
    if (panel.scrollTop > 0 || e.touches.length !== 1) return;
    dragging = true;
    cancelAnimationFrame(raf);
    startY = lastY = e.touches[0].clientY;
    lastAt = performance.now();
    vel = 0;
  }, { passive: true });

  panel.addEventListener('touchmove', (e) => {
    if (!dragging) return;
    const y = e.touches[0].clientY;
    dragY = y - startY;
    if (dragY < 0) dragY *= 0.2;         // вверх тянуть некуда — сопротивление
    if (dragY > 0) e.preventDefault();   // иначе жест уйдёт в прокрутку страницы

    panel.style.transform = `translateY(${Math.max(0, dragY)}px)`;
    const h = panel.offsetHeight || window.innerHeight;
    dim(1 - Math.max(0, dragY) / h);

    const now = performance.now();
    if (now > lastAt) {
      vel = (y - lastY) / (now - lastAt);
      lastY = y; lastAt = now;
    }
  }, { passive: false });

  const release = () => {
    if (!dragging) return;
    dragging = false;
    const h = panel.offsetHeight || window.innerHeight;
    if (performance.now() - lastAt > 90) vel = 0;   // палец замер — это не бросок
    const projected = dragY + vel * PROJECTION;      // куда долетело бы по инерции
    if (projected > h * 0.34) dismiss(vel * 1000);
    else springTo(dragY, 0, vel * 1000);
  };
  panel.addEventListener('touchend', release);
  panel.addEventListener('touchcancel', release);

  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) dismiss();   // тап мимо шторки — с той же доводкой
  });

  return { node: backdrop, close: remove };
}
