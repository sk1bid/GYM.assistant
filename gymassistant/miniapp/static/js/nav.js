/**
 * Нижняя панель навигации.
 *
 * Появилась потому, что между разделами ходили только через главный экран: из профиля
 * в расписание надо было вернуться назад и ткнуть в ряд-ссылку. Плюс эргономика — верх
 * экрана одной рукой не достаётся, а переключение разделов идёт постоянно.
 *
 * Разметка лежит в index.html, здесь только состояние: какой раздел подсвечен и
 * показывать ли панель вообще.
 */
import { haptic } from './tg.js';

const node = document.getElementById('nav');

/**
 * Разделы верхнего уровня. Порядок совпадает с порядком кнопок в разметке.
 *
 * Программ здесь нет: их собирают раз в несколько месяцев, а вкладка стояла наравне
 * с тем, чем пользуются каждую тренировку. Переехали в профиль — туда же, где живёт
 * всё остальное «про меня, а не про сегодня».
 */
const SECTIONS = ['/', '/schedule', '/profile'];

/**
 * Куда ведёт кнопка для текущего адреса.
 *
 * Вложенные экраны подсвечивают свой корень: со страницы дня горит «Расписание»,
 * с истории, рекордов и программ — «Профиль». Иначе на большинстве экранов панель
 * выглядела бы погасшей и переставала объяснять, где пользователь находится.
 */
export function sectionOf(path) {
  if (path === '/') return '/';
  if (path.startsWith('/schedule') || path.startsWith('/day/')) return '/schedule';
  if (path.startsWith('/profile') || path.startsWith('/history')
      || path.startsWith('/records') || path.startsWith('/progress/')
      || path.startsWith('/notifications')
      || path.startsWith('/programs') || path.startsWith('/program/')) return '/profile';
  return null;
}

/**
 * Панель прячется на экране тренировки: там внизу живёт MainButton Telegram,
 * и две полосы друг над другом отняли бы у подхода треть экрана.
 */
function visibleOn(path) {
  return !path.startsWith('/workout');
}

export function isVisible() {
  return !node.classList.contains('hidden');
}

/**
 * Режим ведения: пузырёк идёт строго за пальцем, без сглаживания.
 *
 * Сглаживание во время жеста — это отставание: палец уже здесь, а пузырёк ещё
 * догоняет. Именно оно читается как «рвано». Включаем его только на доводке.
 */
export function hold(on) {
  node.classList.toggle('dragging', on);
}

/** Позиция пузырька в номерах разделов; 1.5 — ровно между вторым и третьим. */
export function at(position) {
  node.style.setProperty('--nav-i', position);
}

export function sync(path, go) {
  const show = visibleOn(path);

  node.classList.toggle('hidden', !show);
  node.classList.remove('dragging');
  document.body.classList.toggle('with-nav', show);

  if (!show) return;

  const active = sectionOf(path);
  node.style.setProperty('--nav-i', Math.max(0, SECTIONS.indexOf(active)));

  node.querySelectorAll('[data-nav]').forEach((button) => {
    button.classList.toggle('on', button.dataset.nav === active);
  });

  // Обработчики вешаем один раз: разметка статична и живёт всё время работы приложения.
  if (node.dataset.bound) return;
  node.dataset.bound = '1';

  node.querySelectorAll('[data-nav]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.nav;
      const from = SECTIONS.indexOf(sectionOf(location.hash.slice(1) || '/'));
      const to = SECTIONS.indexOf(target);

      haptic('light');

      // Вкладки — соседи, а не уровни вложенности: движение идёт в сторону хода,
      // как на полке. Тап по своей же вкладке с вложенного экрана — это подъём
      // наверх, поэтому там обратное движение, а не боковое.
      go(target, { direction: to === from ? 'shallow' : (to > from ? 'right' : 'left') });
    });
  });
}

export { SECTIONS };
