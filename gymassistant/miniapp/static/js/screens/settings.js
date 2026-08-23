/**
 * Уведомления.
 *
 * Единственный экран, который вообще про них знает: шлёт напоминания бот
 * (workers/notifier.py), решает — services/notifications.py, а здесь человек
 * говорит, во сколько тренируется и что ему писать.
 *
 * Кнопки «Сохранить» нет намеренно. Каждый тумблер здесь — законченное решение,
 * и подтверждать его вторым нажатием незачем; поле времени сохраняется по
 * change, то есть когда пикер закрыт и число выбрано.
 */
import { api, cached } from './../api.js';
import { haptic } from './../tg.js';
import { escape, on, onAll, q, render, same } from './../ui.js';

/**
 * Варианты «напомнить за».
 *
 * Список, а не степпер: значений мало, и все они — привычные людям круглые
 * промежутки. Степпер с шагом в пять минут предлагал бы выбирать между 2:55
 * и 3:00, чего никто не хочет.
 */
const LEADS = [
  { value: 0, label: 'В самое время' },
  { value: 30, label: 'За 30 минут' },
  { value: 60, label: 'За час' },
  { value: 120, label: 'За 2 часа' },
  { value: 180, label: 'За 3 часа' },
  { value: 240, label: 'За 4 часа' },
];

export async function notificationsScreen() {
  // Мгновенная отрисовка из последнего ответа, свежий — следом. См. home.js.
  const known = cached('api/notifications');
  if (known) paint(known);

  const data = await api.notifications.get();
  if (!known || !same(known, data)) paint(data);
}

function paint(data) {
  const s = data.settings;

  render(`
    <h1>Уведомления</h1>

    <div class="switch-card">
      <label class="switch">
        <span class="grow">
          <span class="lead">Напоминания</span><br>
          <span class="hint">Кроме таймера отдыха</span>
        </span>
        <input type="checkbox" id="enabled" ${s.enabled ? 'checked' : ''}>
        <span class="track"></span>
      </label>
    </div>

    <div id="body" class="${s.enabled ? '' : 'off'}">
      <div class="section-title">Время тренировки</div>

      <div class="field">
        <label for="train-at">Обычно начинаю</label>
        <input type="time" id="train-at" value="${escape(s.train_at)}">
        ${data.usual ? `<div class="hint mt-1">По истории — около ${escape(data.usual)}</div>` : ''}
      </div>

      <div class="field">
        <label for="lead">Напомнить</label>
        <select id="lead">
          ${LEADS.map((l) => `
            <option value="${l.value}" ${l.value === s.lead_minutes ? 'selected' : ''}>${l.label}</option>
          `).join('')}
        </select>
        <div class="hint mt-1" id="remind-at">${remindAt(s)}</div>
      </div>

      <div class="section-title">О чём писать</div>

      ${toggle('day_reminder', s.day_reminder, 'День тренировки')}
      ${toggle('unfinished', s.unfinished, 'Забытая тренировка')}
      ${toggle('weekly', s.weekly, 'Серия и итог недели')}
      ${toggle('missed', s.missed, 'Пропуски и возвращение')}

      <p class="hint mt-4">
        Тихие часы — с ${escape(data.rules.quiet_from)} до ${escape(data.rules.quiet_to)}.
      </p>
    </div>
  `);

  wire(data);
}

function toggle(id, checked, title) {
  return `
    <label class="switch compact row-switch">
      <span class="grow"><span class="lead">${escape(title)}</span></span>
      <input type="checkbox" data-pref="${id}" ${checked ? 'checked' : ''}>
      <span class="track"></span>
    </label>
  `;
}

/**
 * «Придёт в 07:00» — единственное место, где видно результат обеих настроек сразу.
 *
 * Считает сервер (`remind_at`), а не клиент: вычитание минут выглядит безобидно
 * ровно до тех пор, пока не упрётся в полночь, и второй ответ на этот вопрос
 * приложению не нужен.
 */
function remindAt(settings) {
  return `Придёт в ${settings.lead_minutes === 0 ? settings.train_at : settings.remind_at}`;
}

function wire(data) {
  const body = q('#body');
  const hint = q('#remind-at');

  // Ответ PATCH — источник правды: сервер отдаёт настройки целиком, включая
  // пересчитанное время напоминания. Клиент их не досчитывает.
  const save = async (patch) => {
    haptic('light');
    const fresh = await api.notifications.update(patch);
    data.settings = fresh.settings;
    if (hint) hint.textContent = remindAt(fresh.settings);
    return fresh.settings;
  };

  on('#enabled', 'change', async (e) => {
    const enabled = e.target.checked;
    // Гасим остальное сразу, не дожидаясь сервера: тумблер уже переключился,
    // и экран не должен полсекунды противоречить сам себе.
    if (body) body.classList.toggle('off', !enabled);
    await save({ enabled });
  });

  on('#train-at', 'change', (e) => {
    // Пустое поле — человек стёр время в пикере и не выбрал новое. Слать нечего.
    if (e.target.value) save({ train_at: e.target.value });
  });

  on('#lead', 'change', (e) => save({ lead_minutes: Number(e.target.value) }));

  onAll('[data-pref]', 'change', (node) => save({ [node.dataset.pref]: node.checked }));
}
