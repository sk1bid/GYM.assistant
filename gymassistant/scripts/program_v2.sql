-- Программа «Гипертрофия» (id 6): правка состава после первой недели.
--
-- Что делает и почему:
--   * возвращает БРУСЬЯ (12 подходов истории, растут) и ПРЕСС В ТРЕНАЖЁРЕ
--     (180 подходов) — оба выпали при переходе на верх/низ;
--   * убирает четвёрки: по истории делается 3 подхода лесенкой вниз, а план
--     в 4 подхода алгоритм прогрессии читает как провал и предлагает деload;
--   * ставит повторения по факту, а не по верху диапазона — по той же причине
--     (махи 17.5×12 при плане 15 = «недобор больше двух» = сброс веса);
--   * уносит болгарские из четверга в воскресенье, первым номером;
--   * меняет сгибания ног в четверге на разгибания (связка) — так и делалось.
--
-- Ничего из удаляемого не несёт реальных подходов: проверено, у 97 и 98 только
-- нулевые skipped-строки, у остальных подходов нет вовсе.
--
-- Запуск:
--   KUBECONFIG=~/.kube/config kubectl exec -n gym-prod -i deploy/postgres -- \
--     sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -f -' < program_v2.sql
--
-- Прогнать вхолостую: заменить COMMIT в конце на ROLLBACK.

\set ON_ERROR_STOP on

BEGIN;

-- ── 0. Страховка по последовательности id ────────────────────────────────────
-- Нашлось на прогоне вхолостую: если строки программы заводились с явными id
-- (как в наливке тестового контура), sequence остаётся позади и первый же
-- INSERT падает на дубликате ключа. На проде она в порядке (112, is_called),
-- строка идемпотентна и ничего не делает.
SELECT setval('exercise_id_seq', (SELECT max(id) FROM exercise));

-- ── 1. Убираем лишнее ────────────────────────────────────────────────────────
-- 89  жим гантелей лёжа   — новое движение без истории, его место занимают брусья
-- 94  разгибания на блоке — трицепс закрывают брусья и разгибания из-за головы
-- 97  болгарские (чт)     — переезжают в воскресенье
-- 98  сгибания ног (чт)   — потянута связка, заменены разгибаниями
-- 104 шраги               — трапеции никогда не тренировались, время дороже
-- 107 выпады              — тот же односторонний паттерн, что и болгарские
-- 112 подъём ног в висе   — заменён прессом в тренажёре, там история и прогрессия
DELETE FROM exercise WHERE id IN (89, 94, 97, 98, 104, 107, 112);

-- ── 2. Добавляем ─────────────────────────────────────────────────────────────
-- Имя пишем явно, а не копируем с карточки: на карточке 7 до выкатки слияния
-- ещё старое написание «Пресс в тренажере», а в дне должно стоять человеческое.
-- weight_step = 1 у блочных тренажёров: кнопки ходят по одному блоку, а число
-- остаётся тем же, чем было в истории (13 — это 13, а не 13×5 килограммов).

INSERT INTO exercise (name, description, base_sets, base_reps, training_day_id,
                      position, circle_training, admin_exercise_id, equipment,
                      weight_step, created, updated)
SELECT 'Брусья', a.description, 3, 10, 37, 0, false, a.id, 'bodyweight', NULL, now(), now()
FROM admin_exercises a WHERE a.id = 8
  AND NOT EXISTS (SELECT 1 FROM exercise x WHERE x.training_day_id = 37 AND x.admin_exercise_id = 8);

INSERT INTO exercise (name, description, base_sets, base_reps, training_day_id,
                      position, circle_training, admin_exercise_id, equipment,
                      weight_step, created, updated)
SELECT 'Разгибания ног в тренажёре', a.description, 3, 15, 39, 2, false, a.id, 'machine', 1, now(), now()
FROM admin_exercises a WHERE a.id = 27
  AND NOT EXISTS (SELECT 1 FROM exercise x WHERE x.training_day_id = 39 AND x.admin_exercise_id = 27);

INSERT INTO exercise (name, description, base_sets, base_reps, training_day_id,
                      position, circle_training, admin_exercise_id, equipment,
                      weight_step, created, updated)
SELECT 'Болгарские приседания', a.description, 3, 10, 42, 0, false, a.id, 'dumbbell', 2.5, now(), now()
FROM admin_exercises a WHERE a.id = 23
  AND NOT EXISTS (SELECT 1 FROM exercise x WHERE x.training_day_id = 42 AND x.admin_exercise_id = 23);

INSERT INTO exercise (name, description, base_sets, base_reps, training_day_id,
                      position, circle_training, admin_exercise_id, equipment,
                      weight_step, created, updated)
SELECT 'Пресс в тренажёре', a.description, 3, 15, 42, 5, false, a.id, 'machine', 1, now(), now()
FROM admin_exercises a WHERE a.id = 7
  AND NOT EXISTS (SELECT 1 FROM exercise x WHERE x.training_day_id = 42 AND x.admin_exercise_id = 7);

-- ── 3. Правим планы у оставшихся ─────────────────────────────────────────────
-- Вторник
UPDATE exercise SET base_sets = 3, base_reps = 8,  position = 1, updated = now() WHERE id = 90;  -- тяга штанги
UPDATE exercise SET base_sets = 3, base_reps = 10, position = 2, updated = now() WHERE id = 91;  -- жим гантелей сидя
UPDATE exercise SET base_sets = 3, base_reps = 12, position = 3, updated = now() WHERE id = 92;  -- тяга верхнего блока
UPDATE exercise SET base_sets = 3, base_reps = 10, position = 4, updated = now() WHERE id = 93;  -- бицепс

-- Четверг
UPDATE exercise SET base_sets = 4, base_reps = 10, position = 0, updated = now() WHERE id = 95;  -- жим ногами, 150 рабочий
UPDATE exercise SET base_sets = 3, base_reps = 10, position = 1, updated = now() WHERE id = 96;  -- румынская
UPDATE exercise SET base_sets = 3, base_reps = 15, position = 3, updated = now() WHERE id = 99;  -- носки: было 4×15

-- Суббота
-- weight_step = 5 на жиме в наклоне: гантель 37.5 в зале потеряна, и прибавка
-- должна вести с 35 сразу на 40, а не на несуществующий вес.
UPDATE exercise SET base_sets = 3, base_reps = 12, position = 0, weight_step = 5, updated = now() WHERE id = 100;
UPDATE exercise SET base_sets = 3, base_reps = 10, position = 1, updated = now() WHERE id = 101; -- подтягивания
UPDATE exercise SET base_sets = 3, base_reps = 12, position = 2, updated = now() WHERE id = 102; -- тяга горизонтального блока
UPDATE exercise SET base_sets = 3, base_reps = 12, position = 3, updated = now() WHERE id = 103; -- махи: было 4×15
UPDATE exercise SET base_sets = 3, base_reps = 10, position = 4, updated = now() WHERE id = 105; -- молотки: было 3×12
UPDATE exercise SET base_sets = 3, base_reps = 12, position = 5, updated = now() WHERE id = 106; -- разгибания из-за головы

-- Воскресенье
UPDATE exercise SET base_sets = 3, base_reps = 15, position = 1, weight_step = 1, updated = now() WHERE id = 108; -- разгибания ног
UPDATE exercise SET base_sets = 3, base_reps = 15, position = 2, weight_step = 1, updated = now() WHERE id = 109; -- сгибания ног
UPDATE exercise SET base_sets = 3, base_reps = 15, position = 3, updated = now() WHERE id = 110; -- носки
UPDATE exercise SET base_sets = 3, base_reps = 15, position = 4, updated = now() WHERE id = 111; -- махи в наклоне

-- ── 4. Проверка ──────────────────────────────────────────────────────────────
\echo '=== ЧТО ПОЛУЧИЛОСЬ ==='
SELECT d.day_of_week, e.position AS pos, e.name,
       e.base_sets || '×' || e.base_reps AS plan, e.equipment, e.weight_step AS step
FROM exercise e JOIN training_day d ON d.id = e.training_day_id
WHERE d.training_program_id = 6 ORDER BY d.id, e.position;

\echo '=== ПОДХОДОВ В НЕДЕЛЮ ==='
SELECT d.day_of_week, count(*) AS упражнений, sum(e.base_sets) AS подходов
FROM exercise e JOIN training_day d ON d.id = e.training_day_id
WHERE d.training_program_id = 6 GROUP BY d.id, d.day_of_week ORDER BY d.id;

COMMIT;
