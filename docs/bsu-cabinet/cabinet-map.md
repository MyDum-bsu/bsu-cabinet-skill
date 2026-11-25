# Карта кабинета teacher-cabinet.bsu.by

Снято 2026-11-19 живой сессией (headless Chrome + auto-SMS).
Версия приложения: 6.76. JS: `docs/bsu-cabinet/site-js/`.
Правила выбора данных: [карточки занятий](lesson-card-guide.md),
[индивидуальный план](individual-plan-guide.md).

## Вход

- `GET /Login` → поля: `input[0]` (логин), `input[type=password]`, кнопка «Вход».
- POST /Login → диалог с экранной клавиатурой 0–9 (DIV'ы внутри `.ui-dialog`,
  кликабельны только mouse-координатами — overlay блокирует locator.click).
- Маркер диалога: «Введите число из sms». Код = 4 цифры, поле `.sms-input` (4 окна).
- После цифр — кнопка «Подтвердить» → POST `/Sms/<code>` → редирект `/PersonalCabinet`.
- SMS: «Vash kod vxoda v CabinetBSU: NNNN»; номер отправителя меняется.
- Сессия в профиле Chrome НЕ переживает закрытие контекста (каждый запуск —
  новый логин; зато auto-SMS делает это бесплатным).
- Модалка при входе: «Предыдущий сеанс … отключён» — закрывается Escape/Закрыть.
- Часть функций помечена «доступно только из внутренней сети БГУ»
  (рейтинговая система ППС); карточки и ИП снаружи работают.

## Навигация

Меню «Преподаватель» (клик раскрывает):
- Индивидуальный план → `/Teacher/PersonalPlan` (IdEmpStaff=<staffId>)
- Карточка №1,2 учёта проведённых занятий → `/Teacher/CardOne[/<staffId>]#<Key>`
- Расписание занятий / экзаменов (ShowDeptList)

Важно: CardOne пуст, пока не заполнен ИП — «Перед заполнением карточки
необходимо заполнить индивидуальный план». Порядок: сначала ИП, потом карточки.

## API (всё — POST JSON через `SendModel(action, model, cb)`)

SendModel: `$.ajax POST '/'+action`, `contentType: application/json`,
ответ 'NotAuthorize' → релогин. Ошибка → Ajax_Error.

### Индивидуальный план (/Teacher/PersonalPlan)

Статусы плана (StatusPlan): 0 заполнение, 1 на проверке, 2 утверждён/отчёт,
3 отклонён, 4 отчёт на проверке, 5 отчёт утверждён, 6 отчёт отклонён.
Редактирование строк — только при 0/3.

Табы: tbl-0 = «Учебная работа (осенний сем.)», tbl-10 = весенний,
tbl-20 = «в течение года», tbl-2..5 = методработа/НИР/советы/идеологич.,
tbl-30 = итого.

- Читать сводку «Учебная работа (итого)»: UI вызывает `POST /Teacher/PersonalPlanTable4/0`
  и подставляет ответ в `.res_4`. Это чтение, не сохранение. Для сетевого фильтра
  read-only используй точный путь с `/0`; ошибка загрузки этой вкладки может
  показываться общим диалогом «Данные не сохранены».
- Справочник факультет→спец./дисц.: `Teacher/PersonalPlanSelectFaculty`
  `{Faculty: '125'}` → `{Title: <option> специальностей, obj: <option> дисциплин}`.
  ФПМИ = 125. Специальности/дисциплины — id из option value.
- Добавить строку: `Teacher/PersonalPlanMainAdd`
  `{Faculty, Disp, Spec, Semestr(0|1), FacultyText, DispText, SpecText}`
  → `{obj: {Key, ...}}`.
- Сохранить строку: `Teacher/PersonalPlanMainSave`
  `{Key, Curs, FormEducation(0=очная,1=заочная), CountLine, CountGroups,
   CountGroupsSub, CountStudent_lecture, CountStudent_practical,
   CountStudent_laboratory, CountStudentAlien,
   Clock_lecture_a, Clock_lecture_b,        # лекции ауд/ДОТ
   Clock_practical_a, Clock_practical_b,   # практ./семин. ауд/ДОТ
   Clock_laboratory_a, Clock_laboratory_b, # лаб. ауд/ДОТ
   Clock_ucr_a, Clock_ucr_b,               # УСР ауд/ДОТ
   Collocvium, Help_now, Help_personal, Help_group,  # консультации: коллоквиум, текущие, индивид., групповые
   Check_control,                          # проверка контр. работ
   Exam, Credit,                           # экзамен, зачёт
   Description}`                            # примечания
- Удалить строку: `Teacher/PersonalPlanSaveTable` `{Table, Key, Oper:'del'}`.
- Годовые виды (tbl-20, «в течение года»): `Teacher/PersonalPlanMainSaveAll`
  `{Key, Faculty, FacultyText, FormEducation, Spec, SpecText, Curs,
   CountLine, CountGroups, CountGroupsSub, CountStudent, CountStudentAlien,
   Value, ValueResult, Description}`;
  быстрое значение: `PersonalPlanMainSaveAll_Plan {Key, Value}`.
- Отправить на проверку: `Send('Teacher/PersonalPlanChangeStatus', st)`
  — НЕ вызывать без подтверждения владельца.
- Excel-выгрузка плана: `Send('Teacher/PersonalPlanExcel', 0)`.

### Карточка занятий (/Teacher/CardOne/<staffId>#<Key>)

- Дерево `.main-tree div[vl]` — ключи строк ИП (SelKey). Табы семестров.
- Таблицы `#tbl-<status>`: 0 — лекции (тема/ДО/часы/дата/время/выполнено/
  примечание), 1 — лабораторные, 2 — УСР, 4 — практические/семинарские;
  1, 2, 4, 5, 8 имеют поле группы. 3 — консультация (statusHelp);
  6 — проверка работ (statusCheck, check, normTime, groupCount);
  7 — экзамен/зачёт (statusExamA/B, student, groupCount).
- Сохранить таблицу целиком: `Teacher/CardOneSave`
  `{Key: SelKey, Status: status, Items: [{
    title,            # тема
    group,            # отдельная числовая группа; UI разворачивает '1;2;5-7'
                      # при добавлении (ListGroups), буква подгруппы отбрасывается
    groupCount, dt,   # дата datepicker dd-mm-yy, например '02-09-2026'
    time,             # '18:10-19:35'
    hours, ready, dl, # часы, выполнено(bool), дистанц.(bool)
    statusHelp, statusCheck, check,
    statusExamA, statusExamB, student, normTime, note }]}`
- Источники опций statusHelp/statusCheck/statusExamA/B — селекты диалога
  CardEdit в HTML страницы (снять при первом заполнении).
- PDF карточки: `Teacher/CardOnePdf {Key, Status:0}`.
- Дублирование прошлогодней карточки: `Teacher/CardOneForCopy`
  `{idYear, IdDept, Key, Status}` — список годов/кафедр в `.copy-list`.
- Примечание к ключу: `Teacher/CardOneSaveRemark {Key, Remark}`.

### Поля формы логина/диалогов (id)

ИП-редакт: fo, Curs, CountLine, CountGroups, CountGroupsSub,
CountStudent_{lecture,practical,laboratory}, CountStudentAlien,
Clock_{lecture,practical,laboratory,ucr}_{a,b}, Collocvium,
Help_{now,personal,group}, Check_control, Exam, Credit, Description.
Карточка-диалог: l_title, l_hour, l_dt(datepicker dd-mm-yy), l_time,
l_status, l_group, l_groupCount, l_note, l_ready, l_dl, l_statusHelp,
l_statusCheck, l_check, l_normTime, l_statusExamA, l_statusExamB, l_student.

## Границы

- Любой SendModel с 'Save'/'Add'/'ChangeStatus' — реальная запись в кабинет.
  По умолчанию работаем dry-run; запись — только по явному подтверждению владельца,
  ChangeStatus (отправка на проверку) — отдельное подтверждение.
