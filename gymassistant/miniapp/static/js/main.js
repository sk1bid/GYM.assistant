/**
 * Сборка приложения: какие экраны по каким адресам.
 *
 * Экраны бота с их уровнями меню (level 0–7 в MenuCallBack) превратились в обычные
 * адреса. Ветвление «пришли из расписания или из настроек программы», ради которого
 * в боте был префикс shd/ в каждом action, здесь не нужно вовсе: назад ведёт история
 * браузера.
 */
import { api } from './api.js';
import { route, start } from './router.js';
import * as rest from './rest.js';
import * as swipe from './swipe.js';
import { ready } from './tg.js';

import { homeScreen } from './screens/home.js';
import { workoutScreen } from './screens/workout.js';
import { dayScreen, scheduleScreen } from './screens/schedule.js';
import { catalogScreen, categoryScreen, exerciseScreen, myExercisesScreen } from './screens/catalog.js';
import { programScreen, programsScreen } from './screens/programs.js';
import { historyScreen, profileScreen, progressScreen, recordsScreen, sessionScreen } from './screens/profile.js';
import { notificationsScreen } from './screens/settings.js';

ready();

route('/', homeScreen);

// Тренировка: с дня — начинается, без дня — продолжается уже идущая.
route('/workout', workoutScreen);
route('/workout/:dayId', workoutScreen);

route('/schedule', scheduleScreen);
route('/day/:id', dayScreen);

route('/programs', programsScreen);
route('/program/:id', programScreen);

route('/catalog/:dayId', catalogScreen);
route('/catalog/:dayId/:categoryId', categoryScreen);
route('/day/:dayId/exercise/:id', exerciseScreen);

// С днём — пришли посреди сборки дня, и созданное упражнение сразу в него ляжет;
// без дня — просто правим свой список.
route('/my-exercises', myExercisesScreen);
route('/my-exercises/:dayId', myExercisesScreen);

route('/profile', profileScreen);
route('/history', historyScreen);
route('/history/:id', sessionScreen);
route('/records', recordsScreen);
route('/notifications', notificationsScreen);
route('/progress/:id', progressScreen);

// Возврат к тому, на чём остановился пользователь. Тренировка и отдых живут на
// сервере (в БД), а не во вкладке, поэтому закрытое приложение их не обрывает.
// Раз состояние есть — открывать надо сразу экран тренировки, а не главное меню:
// заставлять жать «Продолжить» при каждом входе неправильно.
//
// Делаем это только на холодном старте. «Холодный» — это НЕ наш внутренний маршрут:
// пустой хэш (обычный браузер) либо launch-параметры Telegram (#tgWebAppData=...,
// их телефон подставляет во фрагмент при каждом запуске). Наши же адреса всегда
// вида #/... — если пользователь пришёл по такой ссылке, его выбор важнее возврата.
const explicitRoute = location.hash.startsWith('#/') && location.hash !== '#/';
if (!explicitRoute) {
  try {
    const { session_id } = await api.training.state();
    if (session_id) {
      // Навигируем ТОЛЬКО через location.hash, а не History API. Мобильный Telegram
      // перехватывает history.pushState/replaceState под свою кнопку «назад», и на
      // телефоне они не меняют location.hash — роутер тогда видел стартовый
      // #tgWebAppData… и уходил в меню (на десктопе History API работает, потому
      // там всё и открывалось верно). Обычная навигация приложения идёт через
      // location.hash и на телефоне исправна — значит и восстановление делаем так же.
      //
      // Сперва replace на главную (заменяем стартовый вход, чтобы не плодить запись
      // с launch-параметрами), затем переход на тренировку — обычным присваиванием
      // hash, дающим новую запись, чтобы системная «назад» уводила в меню.
      location.replace('#/');
      location.hash = '#/workout';
    }
  } catch { /* сервер недоступен — откроемся как обычно, на главной */ }
}

// Отдых мог начаться в прошлый заход и всё это время идти на сервере.
rest.refresh();

// Свайп между разделами. Подключается здесь, а не в роутере: роутер про жесты
// ничего знать не должен, а swipe.js уже зависит от него ради go().
swipe.start();

start();
