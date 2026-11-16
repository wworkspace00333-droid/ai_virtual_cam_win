"""Russian UI strings. Keys are the English source text used with vcstudio.i18n.tr()."""
# ruff: noqa: E501
RU: dict[str, str] = {
    # ---- navigation / page titles
    "Dashboard": "Обзор",
    "Input": "Вход",
    "Preview": "Превью",
    "AI Engine": "ИИ-движок",
    "Detection": "Распознавание",
    "ROI Editor": "Редактор зон",
    "Replacement": "Замена",
    "Profiles": "Профили",
    "Virtual Camera": "Виртуальная камера",
    "Performance": "Производительность",
    "Logs": "Журнал",
    "Settings": "Настройки",

    # ---- common words, units, states
    "ms": "мс", " ms": " мс", "s": "с", " s": " с", "B": "Б", "KB": "КБ", "MB": "МБ", " MB": " МБ", "GB": "ГБ", "TB": "ТБ", " px": " пкс",
    " px σ": " пкс σ", " fps": " кадр/с", " FPS": " кадр/с", "fps": "кадр/с",
    "Yes": "Да", "none": "нет", "— none —": "— нет —", "on": "вкл", "off": "выкл.", "active": "активный",
    "available": "доступен", "unavailable": "недоступен", "missing": "файл не найден", "image": "изображение",
    "video": "видео", "detected": "обнаружен", "Enabled": "Включено", "Name": "Название", "Mode": "Режим",
    "State": "Состояние", "Status": "Статус", "Model": "Модель", "Size": "Размер", "Class": "Класс",
    "Classes": "Классы", "Confidence": "Уверенность", "Age": "Возраст", "ID": "ID", "Time": "Время",
    "Level": "Уровень", "Component": "Компонент", "Message": "Сообщение", "Source": "Источник",
    "Device": "Устройство", "Format": "Формат", "Frame rate": "Частота кадров", "Resolution": "Разрешение",
    "Refresh": "Обновить", "Start": "Старт", "Stop": "Стоп", "Pause": "Пауза", "Restart": "Перезапуск",
    "Remove": "Удалить", "Delete": "Удалить", "Duplicate": "Дублировать", "Details": "Подробности",
    "Parameters": "Параметры", "Properties": "Свойства", "License": "Лицензия", "Team": "Команда",
    "Unique ID": "Уникальный ID", "Frame": "Кадр", "Frames": "Кадры",
    "Dropped": "Потеряно", "Latency": "Задержка", "Reconnects": "Переподключения", "Last frame": "Последний кадр",
    "Errors": "Ошибки", "Error": "Ошибка", "Idle": "Ожидание", "Installed": "Установлено",
    "Not installed": "Не установлено", "Not found": "Не найдено", "Visible": "Видна", "Not visible": "Не видна",
    "Other": "Другое", "Default": "По умолчанию", "Auto": "Авто", "Manual": "Вручную", "Select": "Выбор",
    "Rectangle": "Прямоугольник", "Polygon": "Многоугольник", "Region": "Зона", "rectangle": "прямоугольник",
    "Playing": "Воспроизведение", "Paused": "Пауза", "Ended": "Закончилось",
    # pipeline / capture / AI states (lower case and Title case, as they are shown)
    "stopped": "остановлено", "Stopped": "Остановлено", "starting": "запуск", "Starting": "Запуск",
    "running": "работает", "Running": "Работает", "frozen": "кадр застыл", "Frozen": "Кадр застыл",
    "no signal": "нет сигнала", "No Signal": "Нет сигнала", "reconnecting": "переподключение",
    "Reconnecting": "Переподключение", "error": "ошибка", "paused": "пауза", "passthrough": "прямой проход",
    "Passthrough": "Прямой проход", "loading": "загрузка", "Loading": "Загрузка", "degraded": "с ошибками",
    "Degraded": "С ошибками", "disabled": "выключено", "Disabled": "Выключено", "connected": "подключена",
    "not installed": "не установлена", "normal": "норма", "warning": "предупреждение", "critical": "критично",
    "coasting": "по прогнозу", "tracked": "отслеживается", "lost": "потерян", "new": "новый",
    "manual": "вручную", "auto": "авто", "hybrid": "гибрид", "rect": "прямоугольник", "polygon": "многоугольник",
    "objects": "объекты", "not determined": "ещё не запрошено", "authorized": "разрешено", "denied": "запрещено",
    "restricted": "ограничено", "Not Installed": "Не установлено", "Activated Enabled": "Активировано и включено",
    "Activated Waiting For User": "Ждёт подтверждения", "manual (hotkey/button)": "вручную (клавиша/кнопка)",
    "manual (button)": "вручную (кнопка)", "Install": "Установка", "Uninstall": "Удалить",
    "Loopback": "Loopback-тест",

    # ---- main window / status bar
    "Capture: {state}": "Захват: {state}",
    "Output: {state} · {fps} fps · camera {camera}": "Выход: {state} · {fps} кадр/с · камера {camera}",
    "CPU {cpu}% · App {mb} MB": "CPU {cpu}% · приложение {mb} МБ",
    "Profile: {name}": "Профиль: {name}",
    "Cannot start": "Не удаётся запустить",
    "Start processing": "Запустить обработку",
    "Stop processing": "Остановить обработку",
    "Toggle AI": "Вкл/выкл ИИ",
    "Toggle replacement": "Вкл/выкл замену",
    "Emergency passthrough": "Аварийный прямой проход",
    "Switch to next profile": "Следующий профиль",
    "Show / hide preview": "Показать / скрыть превью",

    # ---- video views
    "No signal": "Нет сигнала",
    "Capture stopped": "Захват остановлен",
    "Connecting…": "Подключение…",
    "Frozen input: identical frames": "Вход застыл: кадры одинаковые",
    "Reconnecting…": "Переподключение…",
    "Capture error (see Input page)": "Ошибка захвата (см. страницу «Вход»)",
    "The input is a completely black frame. If it comes from OBS, its scene is empty: add a source in OBS (Sources > + > Video Capture Device, Display Capture or Media Source).":
        "На входе полностью чёрный кадр. Если он из OBS, значит сцена пустая: добавьте источник в OBS (Sources > + > Video Capture Device, Display Capture или Media Source).",
    "ORIGINAL": "ОРИГИНАЛ",
    "PROCESSED  →  AI Virtual Camera": "ОБРАБОТАННОЕ  →  камера",
    "REPLACEMENT PREVIEW": "ПРЕВЬЮ ЗАМЕНЫ",
    ": start capture to see live video": ": запустите захват, чтобы увидеть видео",

    # ---- dashboard
    "Live status of input, AI, output and system resources.": "Текущее состояние входа, ИИ, выхода и ресурсов системы.",
    "Output": "Выход", "Output FPS": "Кадров/с на выходе", "AI inference": "Работа ИИ", "CPU": "Процессор",
    "Memory": "Память", "GPU": "Видеоядро", "Detections": "Обнаружения", "Active ROIs": "Активные зоны",
    "Camera extension": "Расширение камеры", "Profile": "Профиль",
    "{n} profile(s)": "профилей: {n}",
    "{n} defined in profile": "всего в профиле: {n}",
    "See Virtual Camera page. Other camera extensions: {others}": "См. страницу «Виртуальная камера». Другие расширения камер: {others}",
    "Not capturing": "Захват не идёт",
    "Start processing on the Preview page": "Запустите обработку на странице «Превью»",
    "No pipeline running": "Обработка не запущена",
    "Camera connected": "Камера подключена",
    "Camera not installed": "Камера не установлена",
    "Camera output off": "Вывод в камеру выключен",
    "Camera error": "Ошибка камеры",
    "sent {n}": "отправлено {n}",
    "latency {ms} ms": "задержка {ms} мс",
    "starts with capture": "запускается вместе с захватом",
    "tracked · {d} detections · {i} IDs": "отслеживаются · обнаружений {d} · ID {i}",
    "after class filters": "после фильтра классов",
    "This app: {cpu}% · {n} threads": "Это приложение: {cpu}% · потоков {n}",
    "footprint · system {p}% of {total}": "занято приложением · система {p}% из {total}",
    "n/a": "н/д",
    "IOAccelerator statistics unavailable": "Статистика IOAccelerator недоступна",
    "GPU counters unavailable (no hardware GPU)": "Счётчики видеокарты недоступны (нет аппаратной видеокарты)",
    "Checking…": "Проверка…",
    "Unknown": "Неизвестно",
    "Visible to other apps. Details: Virtual Camera page.": "Видна другим приложениям. Подробности — на странице «Виртуальная камера».",
    "See Virtual Camera page.": "См. страницу «Виртуальная камера».",
    "Camera waiting for video": "Камера ждёт видео",
    "Reinstall camera": "Переустановить камеру",
    "This PC": "Этот компьютер",
    "no graphics card found: AI and effects run on the processor (slower; Low Resource mode is suggested)":
        "видеокарта не найдена: ИИ и эффекты работают на процессоре (медленнее; рекомендуется режим «Экономный»)",
    "Graphics card": "Видеокарта",
    "AI runs on": "ИИ работает на",
    "Virtual camera installed": "Виртуальная камера установлена",
    "Virtual camera not installed: install it on the Virtual Camera page (administrator rights once).":
        "Виртуальная камера не установлена: установите её на странице «Виртуальная камера» (один раз нужны права администратора).",
    "OBS Studio {v}": "OBS Studio {v}",
    "plugin": "плагин",
    "plugin not installed": "плагин не установлен",
    "OBS Studio not found (optional)": "OBS Studio не найден (не обязательно)",
    "Cameras": "Камеры",
    "Video source": "Источник видео",
    "Where the program takes the video from. You can change it on the Input page.":
        "Откуда программа берёт видео. Это можно изменить на странице «Вход».",
    "OBS Studio (direct frame transfer through the plugin)": "OBS Studio (прямая передача кадров через плагин)",
    "A camera:": "Камера:",
    "Test pattern (to try the program without a camera)": "Тестовый сигнал (попробовать программу без камеры)",
    "Performance mode": "Режим производительности",
    "Measuring how fast this PC runs the AI (a few seconds)…": "Измеряю, как быстро этот компьютер работает с ИИ (несколько секунд)…",
    "Could not measure ({e}); choose a mode yourself.": "Не удалось измерить ({e}); выберите режим сами.",
    "{model} on {backend}: {ms} ms per frame. Suggested mode:": "{model} на {backend}: {ms} мс на кадр. Рекомендуемый режим:",
    "The camera output stays at 60 fps in every mode; the mode sets how often the AI looks at the picture and which model it uses.":
        "Выход камеры — 60 кадров/с в любом режиме; режим задаёт, как часто ИИ смотрит на картинку и какую модель использует.",
    "Ready": "Готово",
    "Next: draw regions on the ROI Editor page and choose replacements on the Replacement page. In other apps (Zoom, Teams, Discord, OBS) pick the camera “{cam}”.":
        "Дальше: нарисуйте зоны на странице «Редактор зон» и выберите замены на странице «Замена». В других приложениях (Zoom, Teams, Discord, OBS) выберите камеру «{cam}».",
    "Next >": "Далее >",
    "< Back": "< Назад",
    "Finish": "Готово",
    "Skip setup": "Пропустить настройку",
    "Show window": "Показать окно",
    "Hide window": "Скрыть окно",
    "Quit": "Выход",
    "Still running in the tray: the camera keeps working. Right-click the icon to quit.":
        "Программа работает в трее: камера продолжает работать. Чтобы выйти, щёлкните значок правой кнопкой.",
    "Windows and tray": "Windows и трей",
    "Start with Windows (in the tray)": "Запускать вместе с Windows (в трее)",
    "Closing the window keeps the app running in the tray": "При закрытии окна программа продолжает работать в трее",
    "Run the setup again": "Запустить настройку заново",
    "Save diagnostics…": "Сохранить диагностику…",
    "Save diagnostics": "Сохранить диагностику",
    "Starts with Windows: {cmd}": "Запускается вместе с Windows: {cmd}",
    "Does not start with Windows.": "Не запускается вместе с Windows.",
    "Could not change: {e}": "Не удалось изменить: {e}",
    "Setup saved. A changed language applies after a restart.": "Настройка сохранена. Новый язык применится после перезапуска.",
    "Saved {path} ({n} files, {kb} KB). Personal data (user name, computer name, home folder) is replaced. Nothing is sent anywhere.":
        "Сохранено: {path} ({n} файлов, {kb} КБ). Личные данные (имя пользователя, имя компьютера, домашняя папка) заменены. Ничего никуда не отправляется.",
    "Could not save diagnostics: {e}": "Не удалось сохранить диагностику: {e}",
    "Any object (by text)": "Любой предмет (по описанию)",
    "Describe what to find, separated by commas — for example: red cup, cat, laptop. While on, this replaces the 80 built-in classes; regions select objects by these words. Needs a GPU for real time (seconds per frame on a CPU).":
        "Опишите, что искать, через запятую — например: red cup, cat, laptop. Пока включено, это заменяет 80 встроенных классов; зоны выбирают предметы по этим словам. Для реального времени нужна видеокарта (на процессоре — секунды на кадр).",
    "Find objects by description": "Искать предметы по описанию",
    "red cup, cat, laptop": "red cup, cat, laptop",
    "The “any object” models are not installed yet.": "Модели поиска «любых предметов» ещё не установлены.",
    "Download models (about {mb} MB)": "Скачать модели (около {mb} МБ)",
    "OWLv2 (Google) and SAM 2.1 (Meta), Apache-2.0, from huggingface.co. Each file is checked against its SHA-256 "
    "before use. Nothing else is downloaded or sent.":
        "OWLv2 (Google) и SAM 2.1 (Meta), лицензия Apache-2.0, с huggingface.co. Каждый файл сверяется с SHA-256 "
        "перед использованием. Больше ничего не скачивается и не отправляется.",
    "Download failed: {e}. Nothing incomplete was kept; try again later.":
        "Скачать не удалось: {e}. Недокачанные файлы удалены; попробуйте позже.",
    "Download cancelled. Nothing incomplete was kept.": "Загрузка отменена. Недокачанные файлы удалены.",
    "Running: {model} on {backend}, {ms} ms per frame.": "Работает: {model} на {backend}, {ms} мс на кадр.",
    "Starts with capture.": "Запустится вместе с захватом.",
    "Type at least one description.": "Введите хотя бы одно описание.",
    "Background": "Фон",
    "Blur or replace everything behind people in the whole frame (person segmentation, PP-HumanSeg). Region replacements are drawn on top.":
        "Размыть или заменить всё за людьми во всём кадре (сегментация людей, PP-HumanSeg). Замены по зонам рисуются поверх.",
    "Off": "Выключен",
    "Blur": "Размытие",
    "Image from the library": "Картинка из библиотеки",
    "Image": "Картинка",
    "(choose an image)": "(выберите картинку)",
    "The person segmentation model is not installed (scripts/fetch_models.py).": "Модель сегментации людей не установлена (scripts/fetch_models.py).",
    "Choose an image: add one to the asset library above first.": "Выберите картинку: сначала добавьте её в библиотеку выше.",
    "Segmentation: {state}": "Сегментация: {state}",
    "running ({ms} ms, {backend})": "работает ({ms} мс, {backend})",
    "Reinstall": "Переустановка",
    "Removes both camera devices and registers them again (administrator rights). Use it when other apps stopped seeing the camera.":
        "Удаляет обе камеры и регистрирует их заново (нужны права администратора). Помогает, если другие приложения перестали видеть камеру.",
    "Check in other apps": "Проверка в других приложениях",
    "Open browser test page": "Открыть тестовую страницу в браузере",
    "Open the Windows Camera app": "Открыть приложение «Камера» Windows",
    "<b>Windows Camera app</b>: open it and switch cameras until “{mf}” appears.<br><b>Browser</b> (Chrome, Edge — and browser calls in Meet, Teams, Zoom): “Open browser test page” below.<br><b>Zoom</b>: Settings → Video → Camera → “{mf}” or “{ds}”.<br><b>Microsoft Teams</b>: Settings → Devices → Camera → “{mf}”.<br><b>Discord</b>: User Settings → Voice &amp; Video → Camera → “{ds}”.<br><b>OBS Studio</b>: Sources → + → Video Capture Device → “{ds}”.<br>Windows 11 shows the Media Foundation camera as “{mf}” to every app; the DirectShow camera “{ds}” (if installed) can be used by several apps at once. Only one app at a time can use the Media Foundation camera.":
        "<b>Приложение «Камера» Windows</b>: откройте и переключайте камеры, пока не появится «{mf}».<br><b>Браузер</b> (Chrome, Edge — и звонки в браузере: Meet, Teams, Zoom): кнопка «Открыть тестовую страницу» ниже.<br><b>Zoom</b>: Настройки → Видео → Камера → «{mf}» или «{ds}».<br><b>Microsoft Teams</b>: Настройки → Устройства → Камера → «{mf}».<br><b>Discord</b>: Настройки пользователя → Голос и видео → Камера → «{ds}».<br><b>OBS Studio</b>: Источники → + → Устройство захвата видео → «{ds}».<br>Windows 11 показывает Media Foundation-камеру всем приложениям как «{mf}»; DirectShow-камерой «{ds}» (если установлена) могут пользоваться несколько приложений сразу. Media Foundation-камерой одновременно может пользоваться только одно приложение.",
    "waiting": "ожидание",
    "camera {name}: waiting for the first frame": "камера {name}: ждёт первый кадр",
    "System-wide GPU · in use {mem}": "Видеоядро всей системы · занято {mem}",

    # ---- input page
    "Choose the video source and capture format. The app never captures from its own output camera ({camera}).":
        "Выберите источник видео и формат захвата. Приложение никогда не захватывает собственную выходную камеру ({camera}).",
    "Camera device (AVFoundation): OBS Virtual Camera": "Камера (AVFoundation): OBS Virtual Camera",
    "Direct OBS frame transfer (vcstudio-obs plugin)": "Прямая передача кадров из OBS (плагин vcstudio-obs)",
    "Test pattern (diagnostics)": "Тестовая картинка (диагностика)",
    "Screen or window (Windows Graphics Capture)": "Экран или окно (Windows Graphics Capture)",
    "Video file": "Видеофайл",
    "Low disk space: {mb} MB free": "Мало места на диске: свободно {mb} МБ",
    "From the start": "С начала",
    "Scenes": "Сцены",
    "Privacy off": "Приватность выкл.",
    "Auto-framing": "Автокадрирование",
    "Crop to the people in view and follow them smoothly (zoom up to 2×); back to the full picture when nobody is "
    "there.":
        "Кадрирует по людям в кадре и плавно следует за ними (приближение до 2×); без людей возвращает полный кадр.",
    "Hide faces": "Скрыть лица",
    "Hide faces and screens": "Скрыть лица и экраны",
    "Hide faces, screens and background": "Скрыть лица, экраны и фон",
    "Hide screens": "Скрыть экраны",
    "Privacy mode on / off": "Режим приватности вкл. / выкл.",
    "Privacy mode (Ctrl+Alt+H): hides faces; screens, laptops, phones and books; and the background — whatever the "
    "level includes. Switching it off restores the scene as it was.":
        "Режим приватности (Ctrl+Alt+H): скрывает лица; экраны, ноутбуки, телефоны и книги; и фон — в зависимости "
        "от уровня. При выключении сцена возвращается к прежнему виду.",
    "A different asset for each object (in turn)": "Каждому объекту — свой ресурс (по очереди)",
    "On an Automatic or Hybrid region: the 1st object gets the asset above, the 2nd the first checked extra, and so "
    "on, wrapping round. An object keeps its asset while it is tracked.":
        "В автоматической или гибридной зоне: 1-й объект получает ресурс выше, 2-й — первый отмеченный "
        "дополнительный и так далее по кругу. Объект сохраняет свой ресурс, пока его отслеживают.",
    "More assets": "Ещё ресурсы",
    "Silhouettes come from SAM 2.1 on the detector's boxes (no segmentation model installed); on a PC without a "
    "graphics card that takes seconds per frame.":
        "Силуэты строит SAM 2.1 по рамкам детектора (модели сегментации нет); на компьютере без видеокарты это "
        "занимает секунды на кадр.",
    "A scene is a complete look — regions, replacements, background, AI settings and, if you want, its own input. "
    "Switching is instant and whole: the camera never shows half of one scene and half of another. Create, rename or "
    "export scenes on the Profiles page.":
        "Сцена — это готовый вид: зоны, замены, фон, настройки ИИ и, если нужно, свой источник видео. Переключение "
        "мгновенное и целиком: камера никогда не показывает половину одной сцены и половину другой. Создавать, "
        "переименовывать и экспортировать сцены можно на странице «Профили».",
    "Hotkey": "Клавиша",
    "Same input as now": "Тот же вход, что сейчас",
    "Use the current input": "Запомнить текущий вход",
    "Same as now": "Как сейчас",
    "Switch to this scene": "Переключить на эту сцену",
    "Active scene": "Активная сцена",
    "No picture yet": "Пока нет картинки",
    "Active scene: {name}. Hotkeys work in other apps too when “System-wide” is on (Settings).":
        "Активная сцена: {name}. Клавиши работают и в других программах, если включено «Во всей системе» "
        "(«Настройки»).",
    **{f"Scene {n}": f"Сцена {n}" for n in range(1, 10)},
    "AI resources": "Ресурсы ИИ",
    "When the PC cannot keep up, the camera keeps its frame rate: the scheduler slows the least important module "
    "first (background segmentation, then detection) and restores them when there is headroom again.":
        "Когда компьютер не успевает, камера сохраняет частоту кадров: планировщик сначала замедляет наименее важный "
        "модуль (сегментацию фона, затем распознавание) и возвращает их, когда снова появляется запас.",
    "Module": "Модуль",
    "Rate": "Темп",
    "Cost": "Затраты",
    "Reason": "Причина",
    "standby": "ожидание",
    "Background segmentation": "Сегментация фона",
    "Detection (objects, faces)": "Распознавание (объекты, лица)",
    "no video": "нет видео",
    "keeping up": "успевает",
    "overloaded": "перегрузка",
    "recovering": "восстановление",
    "Load: {load} · output {out} / input {inp} fps · steps down {d}, up {u}":
        "Нагрузка: {load} · выход {out} / вход {inp} к/с · замедлений {d}, восстановлений {u}",
    "Change on the Input page": "Изменить на странице «Ввод»",
    "Start at": "Начать с",
    "Playback and every loop start here.": "Отсюда начинается воспроизведение и каждый повтор.",
    "Choose a video file…": "Выберите видеофайл…",
    "Choose a video file": "Выберите видеофайл",
    "Choose…": "Выбрать…",
    "Video": "Видео",
    "{w}×{h}, {fps} fps, {s} s, {codec}": "{w}×{h}, {fps} к/с, {s} с, {codec}",
    "<b>Need “OBS Virtual Camera” instead?</b> In OBS Studio add Sources → + → Video Capture Device → “{ds}”, "
    "right-click it → Transform → Fit to screen, then click <b>Start Virtual Camera</b>. Apps that select “OBS "
    "Virtual Camera” now get this app's processed video. Do not use OBS Virtual Camera as this app's input at the "
    "same time (it would feed back into itself).":
        "<b>Нужна именно «OBS Virtual Camera»?</b> В OBS Studio добавьте Источники → + → Устройство захвата видео → "
        "«{ds}», щёлкните по нему правой кнопкой → Преобразовать → Растянуть на весь экран, затем нажмите "
        "<b>Запустить виртуальную камеру</b>. Программы, выбравшие «OBS Virtual Camera», получат обработанное "
        "видео. Не используйте при этом OBS Virtual Camera как вход этой программы (видео пойдёт по кругу).",
    "Screen {n} ({w}×{h})": "Экран {n} ({w}×{h})",
    "Window: {title}": "Окно: {title}",
    "not open now": "сейчас не открыто",
    "Windows Graphics Capture shows GPU-drawn windows (browsers, games in a window) and a window even when others "
    "cover it. Where it is unavailable the app falls back to Desktop Duplication, then to GDI. Windows may draw a "
    "border around the captured area.":
        "Windows Graphics Capture видит окна, которые рисует видеокарта (браузеры, игры в окне), и захватывает окно, "
        "даже когда его закрывают другие. Где он недоступен, приложение переходит на Desktop Duplication, затем на "
        "GDI. Windows может рисовать рамку вокруг захватываемой области.",
    "macOS will ask for camera permission when capture starts.": "macOS спросит разрешение на камеру при запуске захвата.",
    "Denied. Enable it in System Settings → Privacy & Security → Camera, then restart the app.":
        "Запрещено. Включите в Системных настройках → Конфиденциальность и безопасность → Камера, затем перезапустите приложение.",
    "Restricted by a device-management policy.": "Ограничено политикой управления устройством.",
    "Granted.": "Разрешено.",
    "Reconnect automatically": "Переподключаться автоматически",
    "Method": "Способ",
    "No-signal timeout": "Таймаут «нет сигнала»",
    "Frozen-frame timeout": "Таймаут застывшего кадра",
    "Refresh devices": "Обновить устройства",
    "Start capture": "Начать захват",
    "Stop capture": "Остановить захват",
    "Install OBS plugin": "Установить плагин OBS",
    "Reinstall OBS plugin": "Переустановить плагин OBS",
    "Input monitor": "Монитор входа",
    "Input statistics": "Статистика входа",
    "Measured FPS": "Измеренная частота",
    "All video devices": "Все видеоустройства",
    "Model ": "Модель ",
    "Usable as input": "Можно использовать как вход",
    "OBS is sharing {w}×{h} @ {fps} fps (OBS pid {pid}).": "OBS передаёт {w}×{h} @ {fps} кадр/с (pid OBS {pid}).",
    "Plugin not installed in OBS": "Плагин не установлен в OBS",
    "Click Install, restart OBS, then Tools > AI Virtual Camera Studio: share frames.":
        "Нажмите «Установить», перезапустите OBS, затем Tools > AI Virtual Camera Studio: share frames.",
    "Plugin installed.": "Плагин установлен.",
    "OBS plugin": "Плагин OBS",
    "this app's output, unusable": "выход этого приложения, нельзя использовать",
    "No: this is {camera} (feedback loop)": "Нет: это {camera} (петля обратной связи)",
    "Yes (OBS)": "Да (OBS)",
    "Camera permission: {state}.": "Доступ к камере: {state}.",
    "{fmt} · nominal {fps} fps": "{fmt} · номинально {fps} кадр/с",
    "1 s sliding window": "скользящее окно 1 с",
    "frame timestamp → app": "метка кадра → приложение",
    "uptime {s} s": "работает {s} с",
    "late-frame discards; {n} superseded in slot": "отброшено опоздавших; {n} заменено более новыми",
    "{ms} ms ago": "{ms} мс назад",
    "identical for {s} s": "одинаковые уже {s} с",
    "No usable video device selected.": "Не выбрано подходящее видеоустройство.",
    "OBS is not sharing frames (install the vcstudio-obs plugin and enable Tools > AI Virtual Camera Studio: share frames)":
        "OBS не передаёт кадры (установите плагин vcstudio-obs и включите Tools > AI Virtual Camera Studio: share frames)",
    "OBS stopped sharing frames": "OBS перестал передавать кадры",
    "OBS stopped sharing": "OBS перестал передавать кадры",
    "no recent frames": "нет свежих кадров",
    "incompatible plugin protocol version": "несовместимая версия протокола плагина",
    "shared memory has an unexpected size": "неожиданный размер общей памяти",
    "Plugin not built: run scripts/build_obs_plugin.sh": "Плагин не собран: запустите scripts/build_obs_plugin.sh",

    # ---- preview page
    "Left: the captured input. Right: exactly what the virtual camera receives. Both previews show the same frame.":
        "Слева: захваченный вход. Справа: ровно то, что получает виртуальная камера. Оба превью показывают один и тот же кадр.",
    "Original | processed, both taken from the same pipeline pair, so they show the same moment.":
        "Оригинал | обработанное: оба взяты из одной пары кадров, поэтому показывают один момент.",
    "Pause output": "Пауза вывода",
    "Replacement on": "Замена включена",
    "Replacement off": "Замена выключена",
    "Input settings…": "Настройки входа…",
    "Not running": "Не запущено",
    "Preview conversion failed: {e}": "Не удалось показать превью: {e}",
    "PAUSED: clients see a frozen frame": "ПАУЗА: приложения видят застывший кадр",
    "PASSTHROUGH ({reason})": "ПРЯМОЙ ПРОХОД ({reason})",
    "in {a} fps · out {b} fps": "вход {a} кадр/с · выход {b} кадр/с",
    "camera {name} connected (sent {s}, dropped {d})": "камера {name} подключена (отправлено {s}, потеряно {d})",
    "camera {name} not available: output visible here only": "камера {name} недоступна: результат виден только здесь",
    "camera output disabled": "вывод в камеру выключен",
    "camera error: {e}": "ошибка камеры: {e}",
    "Pipeline {state} · latency {lat} ms (p95 {p95}) · processing {proc} ms ({backend} render {render} ms, GPU {gpu} ms) · {regions} region(s) · skipped inputs {skipped}":
        "Обработка: {state} · задержка {lat} мс (p95 {p95}) · обработка {proc} мс (рендер {backend} {render} мс, GPU {gpu} мс) · зон: {regions} · пропущено входных кадров: {skipped}",

    # ---- AI engine page
    "On-device object detection with Core ML through Apple Vision (CPU / GPU / Neural Engine). Frames never leave this Mac.":
        "Распознавание объектов на устройстве: Core ML через Apple Vision (CPU / GPU / Neural Engine). Кадры никогда не покидают этот Mac.",
    "Engine status": "Состояние движка",
    "Configuration (active profile)": "Настройки (активный профиль)",
    "Enable AI detection": "Включить распознавание ИИ",
    "Apple Vision face detection (face rectangles only; no identity recognition)":
        "Обнаружение лиц Apple Vision (только рамки лиц, без распознавания личности)",
    "Detector model": "Модель детектора",
    "Compute units": "Вычислительные блоки",
    "CPU + GPU + Neural Engine": "CPU + GPU + Neural Engine",
    "CPU + Neural Engine": "CPU + Neural Engine",
    "CPU + GPU": "CPU + GPU",
    "CPU only": "Только CPU",
    "Installed models": "Установленные модели",
    "Add models with: .venv-convert/bin/python scripts/convert_models.py <name>":
        "Добавить модели: .venv-convert/bin/python scripts/convert_models.py <имя>",
    "Benchmark (this Mac)": "Бенчмарк (этот Mac)",
    "Run benchmark": "Запустить бенчмарк",
    "Uses the current input frame, or a built-in sample image when not capturing.":
        "Использует текущий кадр входа, а без захвата встроенное тестовое изображение.",
    "Median ms": "Медиана, мс", "p95 ms": "p95, мс", "Load s": "Загрузка, с",
    "Inference rate": "Частота распознавания", "Inference time": "Время распознавания",
    "Capture → result": "Захват → результат", "Face detection": "Обнаружение лиц", "Tracked objects": "Отслеживаемые объекты",
    "frame arrival to detections": "от прихода кадра до результата",
    "Metal: {name} · GPU working set {gb} GB · Core ML: {coreml}": "Metal: {name} · рабочий объём GPU {gb} ГБ · Core ML: {coreml}",
    "loaded in {s} s": "загружена за {s} с",
    "{model} sustains p95 {p95} ms (budget {budget} ms)": "{model} укладывается: p95 {p95} мс (бюджет {budget} мс)",
    "{model} p95 {p95} ms > {budget} ms budget: stepping down": "{model}: p95 {p95} мс > бюджета {budget} мс, переход на модель меньше",
    "Auto (largest that keeps up on this Mac; smallest in Low Resource mode)": "Авто (самая крупная, что успевает на этом Mac; в экономном режиме самая лёгкая)",
    "max sustainable": "максимум, который держится",
    "target {fps} fps": "цель {fps} кадр/с",
    "{n} inferences": "распознаваний: {n}",
    "{ids} IDs · {rec} recovered · {ms} ms": "ID: {ids} · восстановлено: {rec} · {ms} мс",
    "{n} consecutive": "подряд: {n}",
    "Running on {src} ({w}×{h}), {n} iterations each…": "Идёт на {src} ({w}×{h}), по {n} прогонов…",
    "Done: {n} configurations, saved to the benchmark history.": "Готово: конфигураций {n}, сохранено в историю бенчмарков.",
    "Benchmark failed: {e}": "Бенчмарк не удался: {e}",
    "Accuracy check (labelled photos)": "Проверка точности (размеченные фото)",
    "Check accuracy": "Проверить точность",
    "Runs every installed model on 16 COCO photos with known correct boxes (53 classes) and compares. Columns use the confidence threshold of the active profile.":
        "Прогоняет каждую установленную модель по 16 фото COCO с заранее известными правильными рамками (53 класса) и сравнивает. Столбцы считаются при пороге уверенности активного профиля.",
    "Correct boxes": "Верных рамок",
    "Objects found": "Найдено объектов",
    "Best threshold (F1)": "Лучший порог (F1)",
    "Checking… (a few seconds per model)": "Проверка… (несколько секунд на модель)",
    "Done: {n} models checked on {i} photos ({b} correct boxes).": "Готово: проверено моделей {n} на {i} фото (правильных рамок {b}).",
    "Accuracy check failed: {e}": "Проверка точности не удалась: {e}",
    "Threshold used for the columns: {c}.": "Порог для столбцов: {c}.",
    "Larger model input / highest sustainable inference rate, measured at runtime.":
        "Крупнее модель / максимальная устойчивая частота, измеряется во время работы.",
    "bundled": "в комплекте", "user": "пользовательская", "converted": "сконвертирована",

    # ---- detection page
    "Thresholds, rate and class filters for the active profile. Changes apply immediately.":
        "Пороги, частота и фильтр классов для активного профиля. Изменения применяются сразу.",
    "Confidence threshold": "Порог уверенности",
    "NMS IoU threshold": "Порог IoU для NMS",
    "Detection frequency": "Частота распознавания",
    "From performance mode": "Из режима производительности",
    "Fixed rate": "Фиксированная",
    "Max objects": "Максимум объектов",
    "Show detection boxes on the original preview": "Показывать рамки на оригинальном превью",
    "Detect only the checked classes": "Распознавать только отмеченные классы",
    "Detect all classes except the checked ones": "Распознавать все классы, кроме отмеченных",
    "Search classes…": "Поиск классов…",
    "Uncheck all": "Снять все",
    "Tracking (ByteTrack)": "Трекинг (ByteTrack)",
    "Track objects (stable IDs, smoothing, occlusion recovery, per-frame interpolation)":
        "Отслеживать объекты (стабильные ID, сглаживание, восстановление после перекрытия, интерполяция между кадрами)",
    "Low-score threshold": "Нижний порог",
    "Smoothing": "Сглаживание",
    "Lost-object recovery window": "Окно восстановления потерянных",
    "Keep showing lost objects for": "Показывать потерянные объекты ещё",
    "Show a new object after N detections in a row": "Показывать новый объект после N обнаружений подряд",
    "Higher hides more one-off false detections but shows new objects slightly later.":
        "Больше — меньше случайных ложных рамок, но новые объекты появляются чуть позже.",
    "Class-aware matching (an ID never changes class)": "С учётом класса (ID никогда не меняет класс)",
    "The confidence threshold above is ByteTrack's high threshold. Detections between the low and high thresholds can only continue existing tracks.":
        "Порог уверенности выше — это верхний порог ByteTrack. Обнаружения между нижним и верхним порогом могут только продолжать уже существующие треки.",
    "Live detections": "Обнаружения в реальном времени",
    "Box (x0, y0, x1, y1)": "Рамка (x0, y0, x1, y1)",
    "Current performance mode: {mode} → AI target {tgt}": "Текущий режим производительности: {mode} → цель ИИ {tgt}",
    "as fast as sustainable": "максимально быстро, пока держится",
    "AI state: {state}": "Состояние ИИ: {state}",
    "Tracked {a} · lost (recoverable) {l} · IDs created {c} · recoveries {r} · update {ms} ms":
        "Отслеживается {a} · потеряно (можно восстановить) {l} · создано ID {c} · восстановлений {r} · обновление {ms} мс",
    "{t} tracked objects · {d} detections ≥ threshold": "отслеживаемых объектов: {t} · обнаружений выше порога: {d}",
    "{n} shown of {raw} detected · frame #{seq}": "показано {n} из {raw} · кадр №{seq}",
    "Vision; requires face detection on AI Engine page": "Vision; нужно включить обнаружение лиц на странице «ИИ-движок»",
    # class groups
    "People": "Люди", "Vehicles": "Транспорт", "Animals": "Животные", "Fruit & food": "Фрукты и еда",
    "Furniture": "Мебель", "Electronics": "Электроника", "Kitchen": "Кухня", "Street": "Улица",
    "Accessories": "Аксессуары", "Sports": "Спорт", "Household": "Быт",
    # COCO-80 classes + face
    "person": "человек", "face": "лицо", "bicycle": "велосипед", "car": "автомобиль", "motorcycle": "мотоцикл",
    "airplane": "самолёт", "bus": "автобус", "train": "поезд", "truck": "грузовик", "boat": "лодка",
    "traffic light": "светофор", "fire hydrant": "пожарный гидрант", "stop sign": "знак «стоп»",
    "parking meter": "паркомат", "bench": "скамейка", "bird": "птица", "cat": "кошка", "dog": "собака",
    "horse": "лошадь", "sheep": "овца", "cow": "корова", "elephant": "слон", "bear": "медведь", "zebra": "зебра",
    "giraffe": "жираф", "backpack": "рюкзак", "umbrella": "зонт", "handbag": "сумка", "tie": "галстук",
    "suitcase": "чемодан", "frisbee": "фрисби", "skis": "лыжи", "snowboard": "сноуборд", "sports ball": "мяч",
    "kite": "воздушный змей", "baseball bat": "бейсбольная бита", "baseball glove": "бейсбольная перчатка",
    "skateboard": "скейтборд", "surfboard": "доска для серфинга", "tennis racket": "теннисная ракетка",
    "bottle": "бутылка", "wine glass": "бокал", "cup": "чашка", "fork": "вилка", "knife": "нож", "spoon": "ложка",
    "bowl": "миска", "banana": "банан", "apple": "яблоко", "sandwich": "сэндвич", "orange": "апельсин",
    "broccoli": "брокколи", "carrot": "морковь", "hot dog": "хот-дог", "pizza": "пицца", "donut": "пончик",
    "cake": "торт", "chair": "стул", "couch": "диван", "potted plant": "растение в горшке", "bed": "кровать",
    "dining table": "обеденный стол", "toilet": "унитаз", "tv": "телевизор", "laptop": "ноутбук", "mouse": "мышь",
    "remote": "пульт", "keyboard": "клавиатура", "cell phone": "телефон", "microwave": "микроволновка",
    "oven": "духовка", "toaster": "тостер", "sink": "раковина", "refrigerator": "холодильник", "book": "книга",
    "clock": "часы", "vase": "ваза", "scissors": "ножницы", "teddy bear": "плюшевый мишка", "hair drier": "фен",
    "toothbrush": "зубная щётка",

    # ---- ROI editor
    "Manual (drawn area)": "Вручную (нарисованная зона)",
    "Automatic AI (tracked objects)": "Автоматически ИИ (отслеживаемые объекты)",
    "Hybrid (objects inside area)": "Гибрид (объекты внутри зоны)",
    "dashed = live regions resolved from tracked objects": "пунктир = живые зоны по отслеживаемым объектам",
    "Regions (top = highest priority)": "Зоны (сверху — высший приоритет)",
    "+ Automatic AI region": "+ Зона ИИ",
    "Priority": "Приоритет",
    "Shape": "Форма",
    "Left, top": "Слева, сверху",
    "Width, height": "Ширина, высота",
    "Rotation": "Поворот",
    "Vertices (x, y px; one per line)": "Вершины (x, y в пикселях; по одной на строку)",
    "Apply vertices": "Применить вершины",
    "AI selection (Automatic / Hybrid)": "Выбор ИИ (автоматический / гибрид)",
    "Comma-separated detector classes; empty means any class": "Классы детектора через запятую; пусто — любой класс",
    "any class (e.g. person, car)": "любой класс (например, person, car)",
    "Min confidence": "Мин. уверенность",
    "Padding": "Отступ",
    "Inside rule": "Правило «внутри»",
    "Overlap ≥ threshold": "Перекрытие ≥ порога",
    "Overlap": "Перекрытие",
    "Clip regions to the area": "Обрезать зоны по области",
    "Keep while object is briefly lost": "Сохранять, пока объект ненадолго потерян",
    "tracked object boxes": "рамки отслеживаемых объектов",
    "polygon ({n} points)": "многоугольник ({n} точек)",
    "Vertices not applied: {e}": "Вершины не применены: {e}",
    "'{c}' is not a class of the detector.": "«{c}» — не класс детектора.",
    "'{c}' is filtered out on the Detection page.": "«{c}» отфильтрован на странице «Распознавание».",
    "{n} live object region(s)": "живых зон по объектам: {n}",
    "AI is disabled in this profile: this region will stay empty.": "ИИ выключен в этом профиле: эта зона останется пустой.",
    "Face regions need Apple Vision face detection (AI Engine page).": "Для зон с лицами нужно обнаружение лиц Apple Vision (страница «ИИ-движок»).",
    "Drag on the video to draw a rectangle": "Протяните по видео, чтобы нарисовать прямоугольник",
    "Drag to draw a rectangle": "Протяните, чтобы нарисовать прямоугольник",
    "Click to add points · double-click/Enter closes · Esc cancels": "Клик — добавить точку · двойной клик/Enter — замкнуть · Esc — отмена",
    "Click points; double-click / Enter / click first point to close": "Ставьте точки; двойной клик / Enter / клик по первой точке — замкнуть",
    "Drag to move · handles resize/rotate · Delete removes · arrows nudge": "Перетаскивание — сдвиг · ручки — размер/поворот · Delete — удалить · стрелки — точный сдвиг",
    "Select, move, resize (Shift keeps aspect), rotate (Shift snaps 15°)": "Выбор, сдвиг, размер (Shift — сохранить пропорции), поворот (Shift — шаг 15°)",
    "Swap priority with the neighbour above (+1) or below (-1).": "Поменять приоритет с соседом выше (+1) или ниже (-1).",
    "No ROIs in this profile yet. Draw one on the ROI Editor page.": "В этом профиле ещё нет зон. Нарисуйте зону на странице «Редактор зон».",

    # ---- replacement
    "Each ROI can replace its region, replace a tracked object through its segmentation mask, or overlay an image or a video.":
        "Каждая зона может заменить свою область, заменить отслеживаемый объект по маске сегментации или наложить изображение либо видео.",
    "Replacement for ROI": "Замена для зоны",
    "ROI": "Зона",
    "Asset": "Материал",
    "Assets": "Материалы",
    "Region replacement": "Замена области",
    "Object replacement (segmentation mask)": "Замена объекта (маска сегментации)",
    "Overlay": "Наложение",
    "Effect (blur / pixelate / colour)": "Эффект (размытие / пикселизация / цвет)",
    "Gaussian blur": "Размытие по Гауссу",
    "Pixelate": "Пикселизация",
    "Colour correction": "Цветокоррекция",
    "Shared (all regions in sync)": "Общая (все зоны синхронно)",
    "Per ROI": "Для каждой зоны",
    "Per object (restarts for each new object)": "Для каждого объекта (заново для нового объекта)",
    "Fit (contain)": "Вписать",
    "Fill (cover)": "Заполнить",
    "Stretch": "Растянуть",
    "Original size": "Исходный размер",
    "Fit": "Масштабирование",
    "Scale": "Масштаб",
    "Offset x, y (× region)": "Сдвиг x, y (× зоны)",
    "Opacity": "Непрозрачность",
    "Crop x0, y0, x1, y1": "Обрезка x0, y0, x1, y1",
    "Edge feather": "Растушёвка краёв",
    "Background fill": "Заливка фона",
    "Mask threshold": "Порог маски",
    "Refine edges (remove specks, close holes)": "Улучшить края (убрать точки, закрыть дыры)",
    "Without mask": "Без маски",
    "Skip the object (no fake silhouette)": "Пропустить объект (без поддельного силуэта)",
    "Use its box (explicitly rectangular)": "Использовать рамку (явно прямоугольную)",
    "Centre inside area": "По центру зоны",
    "Effect": "Эффект",
    "Blur strength": "Сила размытия",
    "Pixel size": "Размер пикселя",
    "Brightness": "Яркость",
    "Contrast": "Контраст",
    "Saturation": "Насыщенность",
    "Playback": "Воспроизведение",
    "Loop": "Повтор",
    "At end:": "В конце:",
    "Hold last frame": "Держать последний кадр",
    "Hide": "Скрыть",
    "Start offset": "Начать с",
    "Speed": "Скорость",
    "Frame-rate conversion": "Преобразование частоты кадров",
    "Nearest frame (drop/repeat)": "Ближайший кадр (пропуск/повтор)",
    "Blend neighbouring frames": "Смешивать соседние кадры",
    "Synchronisation": "Синхронизация",
    "Decode size (long side)": "Размер декодирования (длинная сторона)",
    "unlimited": "без ограничения",
    "Transport": "Управление",
    "Pause / Play": "Пауза / Пуск",
    "Not playing (the ROI has no active region right now)": "Не воспроизводится (у зоны сейчас нет активной области)",
    "Asset library (this profile)": "Библиотека материалов (этот профиль)",
    "Add images / videos…": "Добавить изображения / видео…",
    "Add assets": "Добавить материалы",
    "Remove asset": "Удалить материал",
    "Used by: {names}. Remove anyway?": "Используется в: {names}. Всё равно удалить?",
    "Choose an asset.": "Выберите материал.",
    "Live replacement preview": "Превью замены",
    "Rendered here at ≤{fps} fps with the active compositor (Metal GPU, or CPU fallback; see Settings). The full 60 fps output runs from the Preview page.":
        "Отрисовывается здесь с частотой до {fps} кадр/с активным композитором (Metal GPU или запасной CPU, см. «Настройки»). Полный вывод 60 кадр/с запускается на странице «Превью».",
    "Start capture to preview replacements": "Запустите захват, чтобы увидеть замену",
    "Re-enable failed ROIs": "Снова включить сбойные зоны",
    "image {w}×{h}, stored as {file}": "изображение {w}×{h}, сохранено как {file}",
    "video {w}×{h}, {fps} fps, {d} s, codec {codec}, decoder {dec}": "видео {w}×{h}, {fps} кадр/с, {d} с, кодек {codec}, декодер {dec}",
    "Images: PNG / JPG / WEBP (alpha). Videos: MP4 / MOV (hardware decode); other containers via FFmpeg if installed. Files are copied into the app's assets folder.":
        "Изображения: PNG / JPG / WEBP (с прозрачностью). Видео: MP4 / MOV (аппаратное декодирование); другие форматы через FFmpeg, если он установлен. Файлы копируются в папку материалов приложения.",
    "Render failed: {e}": "Ошибка отрисовки: {e}",
    "failed: {names}": "сбой: {names}",
    "{backend} compositor": "Композитор {backend}",
    "render {ms} ms · {drawn} region(s) drawn · {skipped} skipped (no mask) · {active} active region(s) · {players} video player(s)":
        "рендер {ms} мс · отрисовано зон: {drawn} · пропущено (нет маски): {skipped} · активных зон: {active} · видеоплееров: {players}",
    "(+{n} more)": "(ещё {n})",
    "{pos} / {dur} s · decoder {dec} · buffer {buf} · decoded {decd} · late {late} · loops {loops}":
        "{pos} / {dur} с · декодер {dec} · буфер {buf} · декодировано {decd} · опоздало {late} · повторов {loops}",
    "error: {e}": "ошибка: {e}",
    "Object replacement needs an Automatic or Hybrid ROI (it follows a tracked object). Change the ROI mode on the ROI Editor page.":
        "Замене объекта нужна автоматическая или гибридная зона (она следует за объектом). Смените режим зоны на странице «Редактор зон».",
    "No segmentation model installed: run scripts/convert_models.py yolo11n-seg.": "Модель сегментации не установлена: запустите scripts/convert_models.py yolo11n-seg.",
    "While enabled, the AI engine runs the segmentation model (yolo11n-seg) instead of the plain detector.":
        "Пока включено, ИИ-движок использует модель сегментации (yolo11n-seg) вместо обычного детектора.",
    "No segmentation model installed. Object (silhouette) replacement needs one: add a segmentation model with \"Import model\" on the AI Engine page.":
        "Модель сегментации не установлена. Для замены объекта по силуэту она нужна: добавьте модель сегментации кнопкой «Импорт модели» на странице «ИИ-движок».",
    "The accuracy check needs the labelled test photos, which are not installed with the app. Run it from the source tree (scripts/eval_accuracy.py).":
        "Для проверки точности нужны размеченные тестовые фото, они не устанавливаются вместе с программой. Запустите её из исходников (scripts/eval_accuracy.py).",
    "While enabled, the AI engine runs the segmentation model instead of the plain detector.":
        "Пока включено, ИИ-движок использует модель сегментации вместо обычного детектора.",
    "On object regions the effect follows the segmentation silhouette when masks are available (object replacement elsewhere enables segmentation); otherwise it covers the box.":
        "На зонах объектов эффект следует силуэту, если есть маски (их включает замена объекта в другой зоне); иначе закрывает рамку.",
    "VideoToolbox hardware": "Аппаратный VideoToolbox",
    "FFmpeg (software)": "FFmpeg (программный)",
    "Media (": "Медиафайлы (",

    # ---- profiles
    "A profile holds models, detection classes, thresholds, ROIs, replacement assets, mask/playback settings, performance mode and output settings. Switching takes effect immediately, no restart needed.":
        "Профиль хранит модели, классы, пороги, зоны, материалы замены, настройки масок и воспроизведения, режим производительности и настройки вывода. Переключение действует сразу, без перезапуска.",
    "Activate": "Сделать активным",
    "New…": "Новый…",
    "Rename…": "Переименовать…",
    "Import…": "Импорт…",
    "Export…": "Экспорт…",
    "New profile": "Новый профиль",
    "Rename profile": "Переименовать профиль",
    "Name:": "Название:",
    "New name:": "Новое название:",
    "Delete profile": "Удалить профиль",
    "Delete profile “{n}”? This cannot be undone.": "Удалить профиль «{n}»? Это нельзя отменить.",
    "Import profile": "Импорт профиля",
    "Export profile": "Экспорт профиля",
    "created {c} · modified {m} · {n} ROI(s) · mode {mode}": "создан {c} · изменён {m} · зон: {n} · режим {mode}",
    "Profile with assets (*.vcsprofile);;Profile settings only (*.json)": "Профиль с материалами (*.vcsprofile);;Только настройки профиля (*.json)",
    "Profiles (*.vcsprofile *.json)": "Профили (*.vcsprofile *.json)",

    # ---- virtual camera
    "Where processed video goes. “{camera}” is this app's own CoreMediaIO camera extension; OBS Virtual Camera works without an Apple Developer account.":
        "Куда отправляется обработанное видео. «{camera}» — собственное расширение камеры CoreMediaIO; OBS Virtual Camera работает без аккаунта Apple Developer.",
    "Output camera": "Камера для вывода",
    "Send processed video to": "Отправлять обработанное видео в",
    "AI Virtual Camera (this app's camera extension)": "AI Virtual Camera (расширение камеры этого приложения)",
    "OBS Virtual Camera (no Apple Developer account needed)": "OBS Virtual Camera (аккаунт Apple Developer не нужен)",
    "Other apps select “AI Virtual Camera”. Needs the signed camera extension installed.":
        "В других приложениях выбирайте «AI Virtual Camera». Нужно установленное подписанное расширение камеры.",
    "Other apps select “OBS Virtual Camera”. Keep OBS's own Start Virtual Camera off, and take input with Direct OBS frame transfer (the vcstudio-obs plugin), not from OBS Virtual Camera.": "В других приложениях выбирайте «OBS Virtual Camera». В самом OBS не нажимайте «Запустить виртуальную камеру», а вход берите через прямую передачу кадров (плагин vcstudio-obs), а не из OBS Virtual Camera.",
    "Custom: {dev} / {sink}": "Своя: {dev} / {sink}",
    "Set in settings.json (output.camera_device / output.camera_sink).": "Задаётся в settings.json (output.camera_device / output.camera_sink).",
    "Switch the input to Direct OBS frame transfer on the Input page.": "Переключите вход на прямую передачу кадров из OBS на странице «Вход».",
    "Input 'OBS Virtual Camera' is also the output camera: this would feed back into itself.":
        "Вход «OBS Virtual Camera» одновременно и выходная камера: видео замкнётся само на себя.",
    "Host app": "Приложение-хост",
    "Extension": "Расширение",
    "Camera device": "Камера",
    "Install / update": "Установить / обновить",
    "Open approval settings": "Открыть настройки подтверждения",
    "Verify in other apps": "Проверка в других приложениях",
    "Send a moving, numbered test pattern to the selected output camera, then select that camera in FaceTime, QuickTime Player (File > New Movie Recording), Photo Booth or Zoom. The loopback test captures the camera like a client app and measures delivered frames and latency.":
        "Отправьте движущуюся нумерованную тестовую картинку в выбранную камеру, затем выберите эту камеру в FaceTime, QuickTime Player (Файл > Новая видеозапись), Photo Booth или Zoom. Loopback-тест захватывает камеру как обычное приложение и измеряет доставленные кадры и задержку.",
    "Start test pattern": "Запустить тестовую картинку",
    "Stop test pattern": "Остановить тестовую картинку",
    "Run loopback test": "Запустить loopback-тест",
    "Running loopback test (6 s)…": "Идёт loopback-тест (6 с)…",
    "Installed camera extensions (systemextensionsctl)": "Установленные расширения камер (systemextensionsctl)",
    "Test pattern": "Тестовая картинка",
    "start capture to feed the camera": "запустите захват, чтобы передавать видео в камеру",
    "sent {s} · dropped {d}": "отправлено {s} · потеряно {d}",
    "team {id}": "команда {id}",
    "Remove “{camera}” from this Mac?": "Удалить «{camera}» с этого Mac?",
    "Working… macOS may ask you to approve the extension.": "Выполняется… macOS может попросить подтвердить расширение.",
    "exit {rc}.": "код {rc}.",
    "Approve it in System Settings > General > Login Items & Extensions > Camera Extensions.":
        "Подтвердите в Системных настройках > Основные > Объекты входа и расширения > Расширения камер.",
    "Restart the Mac to finish.": "Перезагрузите Mac, чтобы завершить.",
    "PASS": "УСПЕХ", "FAIL": "ОШИБКА",
    "{got} of {sent} frames received in order={order}, latency median {med} ms / p95 {p95} ms, client resolution {res}":
        "получено {got} из {sent} кадров, порядок верный={order}, задержка медиана {med} мс / p95 {p95} мс, разрешение у клиента {res}",
    "Error: {e}": "Ошибка: {e}",
    "AI Virtual Camera is installed and visible to other apps.": "AI Virtual Camera установлена и видна другим приложениям.",
    "Waiting for approval: System Settings > General > Login Items & Extensions > Camera Extensions > enable “AI Virtual Camera”.":
        "Ждёт подтверждения: Системные настройки > Основные > Объекты входа и расширения > Расширения камер > включите «AI Virtual Camera».",
    "Not installed. Build and install the signed app with scripts/build_camera.sh (needs Xcode and an Apple Developer team; see docs/CAMERA_SETUP.md).":
        "Не установлена. Соберите и установите подписанное приложение через scripts/build_camera.sh (нужны Xcode и команда Apple Developer; см. docs/CAMERA_SETUP.md). Без аккаунта выберите выше «OBS Virtual Camera».",
    "Host app found but the camera extension is not activated yet: click Install.":
        "Приложение-хост найдено, но расширение камеры ещё не активировано: нажмите «Установить».",
    "the camera device is not installed or not visible (install and approve the camera extension)":
        "камера не установлена или не видна (установите и подтвердите расширение камеры)",
    "the camera device has no sink stream (wrong or outdated extension)": "у камеры нет входного потока (не то или устаревшее расширение)",
    "could not open the sink buffer queue": "не удалось открыть очередь буферов камеры",
    "could not start the sink stream": "не удалось запустить входной поток камеры",

    # ---- performance
    "Operating mode and live resource usage. Mode FPS values are targets; measured results on this Mac are in docs/BENCHMARK.md (scripts/benchmark.py).":
        "Режим работы и текущее использование ресурсов. Частоты в режимах — это цели; измеренные результаты на этом Mac в docs/BENCHMARK.md (scripts/benchmark.py).",
    "Operating mode": "Режим работы",
    "Balanced": "Сбалансированный",
    "Low Resource": "Экономный",
    "Maximum Quality": "Максимальное качество",
    "AI inference target 30 FPS, output 60 FPS.": "Цель ИИ 30 кадр/с, вывод 60 кадр/с.",
    "AI inference target 20 FPS, output 60 FPS.": "Цель ИИ 20 кадр/с, вывод 60 кадр/с.",
    "AI inference target 10–15 FPS, output 60 FPS.": "Цель ИИ 10–15 кадр/с, вывод 60 кадр/с.",
    "Live resources": "Ресурсы в реальном времени",
    "System CPU": "Процессор системы",
    "App CPU (per core sum)": "Процессор приложения (сумма по ядрам)",
    "System memory": "Память системы",
    "GPU utilisation (system)": "Загрузка видеоядра (система)",
    "Pipeline timings (measured live)": "Тайминги обработки (измеряются вживую)",
    "Capture → output latency": "Задержка захват → выход",
    "Per-frame processing": "Обработка кадра",
    "Pipeline not running.": "Обработка не запущена.",
    "Pipeline not running. Watchdog: {p} pipeline / {a} AI restarts, memory {m}.":
        "Обработка не запущена. Сторож: перезапусков обработки {p} / ИИ {a}, память: {m}.",
    "{state} · {backend} render {render} ms (GPU {gpu} ms) · latency p95 {p95} ms · skipped inputs {skipped} · render errors {errors} · AI {aifps} fps ({model}) · watchdog: {pr} pipeline / {ar} AI restarts, memory {mem} ({mb} MB)":
        "{state} · рендер {backend} {render} мс (GPU {gpu} мс) · задержка p95 {p95} мс · пропущено входных {skipped} · ошибок рендера {errors} · ИИ {aifps} кадр/с ({model}) · сторож: перезапусков обработки {pr} / ИИ {ar}, память {mem} ({mb} МБ)",
    "LOAD SHED": "НАГРУЗКА СНИЖЕНА",
    "App footprint {fp} (RSS {rss}) · threads {n} · memory pressure {pr} · GPU memory in use {gm}":
        "Память приложения {fp} (RSS {rss}) · потоков {n} · давление памяти: {pr} · память GPU {gm}",

    # ---- logs
    "Live application log. Files: {path}": "Журнал приложения в реальном времени. Файлы: {path}",
    "Filter text…": "Фильтр по тексту…",
    "Clear view": "Очистить",
    "Open log folder": "Открыть папку журнала",
    "Recorded events (SQLite)": "Записанные события (SQLite)",

    # ---- settings
    "Stored locally in {path}": "Хранится локально в {path}",
    "Input defaults": "Вход по умолчанию",
    "Input method": "Способ получения видео",
    "OBS Virtual Camera (AVFoundation)": "OBS Virtual Camera (AVFoundation)",
    "Output defaults": "Вывод по умолчанию",
    "Pixel format": "Формат пикселей",
    "Compositor": "Композитор",
    "Automatic (Metal GPU if available)": "Автоматически (Metal GPU, если доступен)",
    "Metal GPU": "Metal GPU",
    "CPU (NumPy/OpenCV fallback)": "CPU (запасной вариант NumPy/OpenCV)",
    "Log level": "Уровень журнала",
    "Language": "Язык",
    "Automatic (macOS language)": "Автоматически (язык macOS)",
    "The new language is used after you restart the app.": "Новый язык включится после перезапуска приложения.",
    "Memory warning": "Предупреждение о памяти",
    "Automatic: {mb} MB for this Mac's {gb} GB": "Автоматически: {mb} МБ для {gb} ГБ этого Mac",
    "Manual threshold": "Свой порог",
    "App memory above this logs a warning; above twice this the app sheds load (Low Resource mode, closes video players, clears caches).":
        "Если приложение занимает больше, в журнал пишется предупреждение; при вдвое большем значении приложение снижает нагрузку (экономный режим, закрывает видеоплееры, чистит кэши).",
    "Save settings": "Сохранить настройки",
    "Hotkeys": "Горячие клавиши",
    "With “System-wide” on, shortcuts work even when another app is in front (Carbon hotkeys; no Accessibility permission needed). On macOS, “Ctrl” in these fields is the ⌘ key. A shortcut another app already owns is reported and falls back to an in-app shortcut.":
        "С включённым «Во всей системе» сочетания работают, даже когда активно другое приложение (Carbon hotkeys; разрешение «Универсальный доступ» не нужно). В macOS «Ctrl» в этих полях означает клавишу ⌘. Если сочетание занято другим приложением, это будет показано, и оно сработает только внутри приложения.",
    "System-wide (global) hotkeys": "Горячие клавиши во всей системе",
    "Apply hotkeys": "Применить сочетания",
    "Reset to defaults": "Сбросить по умолчанию",
    "The same shortcut is assigned twice: ": "Одно и то же сочетание назначено дважды: ",
    "Settings saved": "Настройки сохранены",
    "Exclude": "Исключить",
    "Max FPS": "Макс. кадр/с",
    "Show detections": "Показывать обнаружения",
    "Virtual camera": "Виртуальная камера",
    "Hide face": "Скрыть лицо",
    "Pixelates every detected face in the camera output (adds a region on the Region Editor page; click again to remove it).":
        "Пикселизирует каждое найденное лицо на выходе камеры (добавляет зону в «Редакторе зон»; повторное нажатие убирает её).",
    "Logo": "Логотип",
    "Puts a picture (PNG with transparency works best) in the top-right corner of the camera output. Move or resize "
    "it on the Region Editor page; click again to remove it.":
        "Ставит картинку (лучше всего PNG с прозрачностью) в правый верхний угол выхода камеры. Переместить или изменить "
        "размер можно в «Редакторе зон»; повторное нажатие убирает её.",
    "Choose a logo": "Выберите логотип",
    "Images": "Изображения",
    "Emergency passthrough is on: the camera gets the original video.":
        "Включён аварийный прямой проход: камера получает исходное видео.",
    "Replacement is off: the camera gets the original video.": "Замена выключена: камера получает исходное видео.",
    "No regions yet, so the camera gets the original video. Click “Hide face”, or create a region on the Region Editor page.":
        "Зон пока нет, поэтому камера получает исходное видео без изменений. Нажмите «Скрыть лицо» или создайте зону на странице «Редактор зон».",
    "Start capture when the app opens": "Запускать захват при открытии приложения",
    "Card for a game…": "Карточка для игры…",
    "Card for a game": "Карточка для игры",
    "Put a photo into the frame a browser game or an emulator draws over the camera.": "Вставить фото в рамку, которую браузерная игра или эмулятор рисует поверх камеры.",
    "A photo goes into the camera picture exactly where the game draws its frame. Keep the game open on screen with this camera selected (OBS Virtual Camera), then find the frame automatically or draw it yourself.": "Фото вставляется в картинку камеры точно туда, где игра рисует свою рамку. Откройте игру на экране с этой камерой (OBS Virtual Camera), затем найдите рамку автоматически или обведите её сами.",
    "Cards": "Карточки",
    "Add photos…": "Добавить фото…",
    "Find the frame automatically": "Найти рамку автоматически",
    "Draw the frame myself": "Обвести рамку вручную",
    "Show the card": "Показывать карточку",
    "Mirror the card": "Отразить карточку",
    "Turn on if the card looks mirrored in the game.": "Включите, если в игре карточка выглядит отражённой.",
    "Open the game with the camera on, then click “Find the frame automatically”.": "Откройте игру с включённой камерой и нажмите «Найти рамку автоматически».",
    "No frame yet.": "Рамка ещё не задана.",
    "Frame set. Add or choose a card photo.": "Рамка задана. Добавьте или выберите фото карточки.",
    "The card is in the frame.": "Карточка вставлена в рамку.",
    "Cannot start: {e}": "Не удалось запустить: {e}",
    "Looking at the screen… (the app hides for about 4 seconds)": "Смотрю на экран… (приложение скроется примерно на 4 секунды)",
    "The game's camera picture was not found on screen ({e}). Make sure the game is visible on the main display, shows this camera, and that Screen Recording is allowed for this app (System Settings > Privacy & Security).": "Картинка камеры в игре не найдена на экране ({e}). Проверьте, что игра видна на основном мониторе, показывает эту камеру, и что этому приложению разрешена запись экрана (Системные настройки > Конфиденциальность и безопасность).",
    " The game shows the camera mirrored; if the card looks mirrored there, tick “Mirror the card”.": " Игра показывает камеру зеркально; если карточка там выглядит отражённой, включите «Отразить карточку».",
    "Found the camera picture in the game, but not a frame in it. Draw the frame on the picture below.": "Картинка камеры в игре найдена, а рамка в ней — нет. Обведите рамку на картинке ниже.",
    "Frame found and the card placed. If it is not the right frame, draw it below.": "Рамка найдена, карточка вставлена. Если рамка не та, обведите нужную ниже.",
    "First the app has to see how the game shows the camera: click “Find the frame automatically”, then draw the frame on the picture if it is wrong.": "Сначала приложению нужно увидеть, как игра показывает камеру: нажмите «Найти рамку автоматически», а затем, если рамка не та, обведите её на картинке.",
    "Drag a rectangle over the game's frame in the picture below.": "Обведите рамку игры прямоугольником на картинке ниже.",
    "Game card: next": "Карточка: следующая",
    "Game card: previous": "Карточка: предыдущая",
    "Game card: show / hide": "Карточка: показать / скрыть",
    "A photo goes into the camera picture exactly where the game draws its frame. Keep the game open on screen with this camera selected (OBS Virtual Camera), then find the frames automatically or draw them yourself.": "Фото вставляется в картинку камеры точно туда, где игра рисует свою рамку. Откройте игру на экране с этой камерой (OBS Virtual Camera), затем найдите рамки автоматически или обведите их сами.",
    "Game": "Игра",
    "Save frames as…": "Сохранить рамки как…",
    "drop photos here or paste with ⌘V": "перетащите фото сюда или вставьте ⌘V",
    "Remove from list": "Убрать из списка",
    "New frame": "Новая рамка",
    "Delete frame": "Удалить рамку",
    "Find frames automatically": "Найти рамки автоматически",
    "Open the game with the camera on, then click “Find frames automatically”.": "Откройте игру с включённой камерой и нажмите «Найти рамки автоматически».",
    "— not saved —": "— не сохранено —",
    "Frame {n}": "Рамка {n}",
    "Hotkeys (work inside the game too): next card {n} · previous {p} · show/hide {t}. They act on the selected frame; change them in Settings > Hotkeys.": "Горячие клавиши (работают и в игре): следующая карточка {n} · предыдущая {p} · показать/скрыть {t}. Действуют на выбранную рамку; изменить — Настройки > Горячие клавиши.",
    "{name}: not an image": "{name}: это не изображение",
    "Added {n} card(s).": "Добавлено карточек: {n}.",
    "The clipboard has no picture.": "В буфере обмена нет картинки.",
    "Frame {n}: draw it on the picture below, or find frames automatically.": "Рамка {n}: обведите её на картинке ниже или найдите рамки автоматически.",
    "Game name": "Название игры",
    "Frames saved for “{name}”.": "Рамки сохранены для «{name}».",
    "Frames of “{name}” applied.": "Применены рамки «{name}».",
    "Frames found: {n}. Cards placed. If a frame is wrong, select it and draw it below.": "Найдено рамок: {n}. Карточки вставлены. Если рамка не та, выберите её и обведите ниже.",
    "First the app has to see how the game shows the camera: click “Find frames automatically”, then draw the frame on the picture if it is wrong.": "Сначала приложению нужно увидеть, как игра показывает камеру: нажмите «Найти рамки автоматически», а затем, если рамка не та, обведите её на картинке.",
    "Drag a rectangle over the game's frame in the picture below (frame {n}).": "Обведите рамку игры прямоугольником на картинке ниже (рамка {n}).",
    "Frame {n} set.": "Рамка {n} задана.",
    "Card frame": "Рамка",
    "AI Virtual Camera Studio is already running (process {pid}). Two copies would fight over the output camera, so this one will close. Switch to the open window in the Dock.": "AI Virtual Camera Studio уже запущено (процесс {pid}). Две копии мешали бы друг другу в выходной камере, поэтому эта закроется. Переключитесь на открытое окно через Dock.",
    "Type": "Тип",
    "Threshold": "Порог",
    "Import model…": "Импортировать модель…",
    "Remove imported model": "Удалить импортированную модель",
    "Core ML .mlpackage / .mlmodel: a YOLO detector exported with NMS (Ultralytics format=coreml nms=True) or a YOLO segmentation model. It is checked and timed before it is installed.": "Core ML .mlpackage / .mlmodel: детектор YOLO, экспортированный с NMS (Ultralytics format=coreml nms=True), или модель сегментации YOLO. Перед установкой модель проверяется и замеряется.",
    "imported": "импортирована",
    "detector": "детектор",
    "segmentation": "сегментация",
    "Checking {name}: compiling, test run and timing…": "Проверяю {name}: компиляция, пробный прогон и замер скорости…",
    "Not imported: {e}": "Не импортировано: {e}",
    "Installed {name}.": "Установлена модель {name}.",
    "Detection now uses it.": "Распознавание теперь использует её.",
    "Select an imported model in the table first.": "Сначала выберите импортированную модель в таблице.",
    "Delete {name} from this Mac? Built-in models are not affected.": "Удалить {name} с этого Mac? Встроенные модели не затрагиваются.",
    "Removed {name}.": "Модель {name} удалена.",
    "Import model": "Импорт модели",
    "object detector": "детектор объектов",
    "segmentation (object masks)": "сегментация (маски объектов)",
    "{file}: {task}, input {size}×{size}, {n} classes. On this Mac: {med} ms per frame (p95 {p95} ms). Test run on the sample photo found {dets} object(s){labels}.": "{file}: {task}, вход {size}×{size}, классов: {n}. На этом Mac: {med} мс на кадр (p95 {p95} мс). Пробный прогон на образце нашёл объектов: {dets}{labels}.",
    "Slower than 33 ms: detection will run below 30 times a second on this Mac.": "Медленнее 33 мс: распознавание будет работать реже 30 раз в секунду на этом Mac.",
    "Use this model now in the active profile": "Сразу использовать эту модель в активном профиле",
    "Classes (rename to map them to names you use in regions):": "Классы (переименуйте, чтобы сопоставить с названиями, которые вы используете в зонах):",
    "Model's name": "Название в модели",
    "Name in the app": "Название в приложении",
    "Two classes would get the same name: {names}": "Два класса получат одинаковое название: {names}",
    "{model} could not be used ({err}); using the automatic choice": "Модель {model} не удалось использовать ({err}); работает автоматический выбор",
    "choose a .mlpackage or .mlmodel file (Core ML)": "выберите файл .mlpackage или .mlmodel (Core ML)",
    "Install the model": "Установить",
    "Cancel": "Отмена",
    "Check the whole chain": "Проверить всю цепочку",
    "One click: the app pauses its own processing, sends a 1080p60 test scene and then your OBS video through AI and Metal into the selected camera, receives it like QuickTime or Zoom would, and shows frame rate, dropped frames, latency and whether the processing is visible. About 30 seconds.": "Одна кнопка: приложение приостанавливает свою обработку, пропускает тестовую сцену 1080p60, а затем ваше видео из OBS через ИИ и Metal в выбранную камеру, принимает его так же, как QuickTime или Zoom, и показывает частоту кадров, потери, задержку и видна ли обработка. Около 30 секунд.",
    "Open reports folder": "Открыть папку отчётов",
    "Test scene 1080p60": "Тестовая сцена 1080p60",
    "Your OBS video": "Ваше видео из OBS",
    "Checking: {what} ({s} s)…": "Проверка: {what} ({s} с)…",
    "no report": "нет отчёта",
    "FAIL: {e}": "ОШИБКА: {e}",
    "{verdict}: {fps} fps at the client, {res}, processing visible in {rep}% of frames, input→camera {lat} ms (p95 {p95}), dropped by the camera queue {d} of {s}, render errors {err}, AI {model} {aifps}×/s": "{verdict}: у клиента {fps} кадр/с, {res}, обработка видна в {rep}% кадров, вход→камера {lat} мс (p95 {p95}), отброшено очередью камеры {d} из {s}, ошибок отрисовки {err}, ИИ {model} {aifps} раз/с",
    "the check did not write a report": "проверка не записала отчёт",
    "Your OBS video was not checked: OBS is not sharing frames (open OBS with the vcstudio-obs plugin).": "Ваше видео из OBS не проверено: OBS не передаёт кадры (откройте OBS с плагином vcstudio-obs).",
    "Reports: {path}": "Отчёты: {path}",
    "Bundle ID": "Идентификатор пакета",
    "Press shortcut": "Нажмите сочетание клавиш",
    "Track objects": "Отслеживать объекты",
    "Game card {n}": "Карточка {n}",
    # ---- Windows build (vcstudio/platform: text(), DEVICE_METHODS, COMPOSITORS) ----
    "Camera device (Media Foundation): webcams": "Камера (Media Foundation): веб-камеры",
    "Plugin not built: run scripts/build_obs_plugin_win.ps1": "Плагин не собран: запустите scripts/build_obs_plugin_win.ps1",
    "OBS is running and has the plugin loaded: close OBS, then install again.":
        "OBS запущен и держит плагин загруженным: закройте OBS и установите снова.",
    "Camera device (DirectShow): OBS Virtual Camera": "Камера (DirectShow): OBS Virtual Camera",
    "Automatic (Direct3D 11 GPU if available)": "Автоматически (видеокарта Direct3D 11, если доступна)",
    "Direct3D 11 GPU": "Видеокарта Direct3D 11",
    "Windows asks apps for camera access in Settings → Privacy & security → Camera.":
        "Доступ приложений к камере настраивается в «Параметры → Конфиденциальность и защита → Камера».",
    "Denied. Turn on “Camera access” and “Let desktop apps access your camera” in Settings → Privacy & security → "
    "Camera, then restart capture.":
        "Запрещено. Включите «Доступ к камере» и «Разрешить классическим приложениям доступ к камере» в «Параметры → "
        "Конфиденциальность и защита → Камера», затем перезапустите захват.",
    "Automatic (Windows display language)": "Автоматически (язык интерфейса Windows)",
    "With “System-wide” on, shortcuts work even when another app is in front (Windows RegisterHotKey; no "
    "administrator rights needed). A shortcut another app already owns is reported and falls back to an in-app "
    "shortcut.":
        "С включённым «Во всей системе» сочетания работают, даже когда впереди другое приложение (Windows "
        "RegisterHotKey; права администратора не нужны). Если сочетание уже занято другим приложением, это будет "
        "показано, и оно будет работать только внутри приложения.",
    "Automatic: {mb} MB for this PC's {gb} GB": "Автоматически: {mb} МБ для {gb} ГБ этого компьютера",
    "drop photos here or paste with Ctrl+V": "перетащите фото сюда или вставьте Ctrl+V",
    "The game's camera picture was not found on screen ({e}). Make sure the game is visible on the main display "
    "(not minimised or covered by another window) and shows this camera.":
        "Картинка камеры в игре не найдена на экране ({e}). Проверьте, что игра видна на основном мониторе (не "
        "свёрнута и не закрыта другим окном) и показывает эту камеру.",
    "Operating mode and live resource usage. Mode FPS values are targets; measure this PC with "
    "scripts/benchmark.py.":
        "Режим работы и текущая загрузка. Значения FPS режимов — целевые; замер на этом компьютере — "
        "scripts/benchmark.py.",
    "AI Virtual Camera Studio is already running (process {pid}). Two copies would fight over the output camera, so "
    "this one will close. Switch to the open window in the taskbar.":
        "AI Virtual Camera Studio уже запущена (процесс {pid}). Две копии мешали бы друг другу на выходной камере, "
        "поэтому эта закроется. Переключитесь на открытое окно на панели задач.",
    "Where processed video goes. “{camera}” is this app's own Windows camera: a DirectShow camera for Windows 10/11 "
    "and a Media Foundation camera for Windows 11, registered by the installer.":
        "Куда идёт обработанное видео. «{camera}» — собственная камера приложения для Windows: камера DirectShow для "
        "Windows 10/11 и камера Media Foundation для Windows 11, их регистрирует установщик.",
    "One click: the app pauses its own processing, sends a 1080p60 test scene and then your OBS video through AI and "
    "the compositor into the selected camera, receives it like Zoom or the Camera app would, and shows frame rate, "
    "dropped frames, latency and whether the processing is visible. About 30 seconds.":
        "Одним нажатием: приложение приостанавливает свою обработку, отправляет тестовую сцену 1080p60, затем ваше "
        "видео из OBS через ИИ и компоновщик в выбранную камеру, принимает его как Zoom или приложение «Камера» и "
        "показывает частоту кадров, потери, задержку и видна ли обработка. Около 30 секунд.",
    "Send a moving, numbered test pattern to the selected output camera, then select that camera in the Camera app, "
    "Zoom, Microsoft Teams, Google Chrome or Discord. The loopback test captures the camera like a client app and "
    "measures delivered frames and latency.":
        "Отправьте движущийся нумерованный тестовый шаблон в выбранную камеру и выберите её в приложении «Камера», "
        "Zoom, Microsoft Teams, Google Chrome или Discord. Петлевой тест захватывает камеру как клиентское "
        "приложение и измеряет доставленные кадры и задержку.",
    "Working… Windows asks for administrator rights to register the camera.":
        "Выполняется… Windows запросит права администратора для регистрации камеры.",
    "Remove “{camera}” from this PC?": "Удалить «{camera}» с этого компьютера?",
    "Allow camera access in Settings > Privacy & security > Camera.":
        "Разрешите доступ к камере в «Параметры > Конфиденциальность и защита > Камера».",
    "Restart Windows to finish.": "Перезагрузите Windows, чтобы завершить.",
    "On-device object detection with ONNX Runtime (DirectML on the graphics card, or CPU). Frames never leave this PC.":
        "Распознавание объектов на устройстве через ONNX Runtime (DirectML на видеокарте или CPU). Кадры не "
        "покидают этот компьютер.",
    "YuNet face detection (face rectangles only; no identity recognition)":
        "Обнаружение лиц YuNet (только рамки лиц, без распознавания личности)",
    "ONNX .onnx: a YOLO detector or YOLO segmentation model exported by Ultralytics (format=onnx). It is checked and "
    "timed before it is installed.":
        "ONNX .onnx: детектор YOLO или модель сегментации YOLO, экспортированные Ultralytics (format=onnx). Перед "
        "установкой модель проверяется и замеряется.",
    "Benchmark (this PC)": "Замер скорости (этот компьютер)",
    "ONNX (*.onnx)": "Модели ONNX (*.onnx)",
    "Rendered here at ≤{fps} fps with the active compositor (Direct3D 11 GPU, or CPU fallback; see Settings). The full "
    "60 fps output runs from the Preview page.":
        "Здесь отрисовывается с частотой до {fps} кадр/с активным компоновщиком (видеокарта Direct3D 11 или запасной "
        "CPU; см. «Настройки»). Полный вывод 60 кадр/с запускается со страницы «Превью».",
    "Auto (largest that keeps up on this PC; smallest in Low Resource mode)":
        "Авто (самая крупная, что успевает на этом компьютере; в экономном режиме самая лёгкая)",
    "Delete {name} from this PC? Built-in models are not affected.":
        "Удалить {name} с этого компьютера? Встроенные модели не затрагиваются.",
    "{file}: {task}, input {size}×{size}, {n} classes. On this PC: {med} ms per frame (p95 {p95} ms). Test run on the "
    "sample photo found {dets} object(s){labels}.":
        "{file}: {task}, вход {size}×{size}, классов: {n}. На этом компьютере: {med} мс на кадр (p95 {p95} мс). "
        "Пробный прогон на образце нашёл объектов: {dets}{labels}.",
    "Slower than 33 ms: detection will run below 30 times a second on this PC.":
        "Медленнее 33 мс: распознавание будет идти реже 30 раз в секунду на этом компьютере.",
    "Face regions need YuNet face detection (AI Engine page).":
        "Зонам лиц нужно обнаружение лиц YuNet (страница «ИИ-движок»).",
    "Automatic (GPU if available, else CPU)": "Автоматически (видеокарта, если есть, иначе CPU)",
    "GPU (DirectML)": "Видеокарта (DirectML)",
    "GPU: {gpus} · ONNX Runtime {ver}: {provs}": "Видеокарта: {gpus} · ONNX Runtime {ver}: {provs}",
    "no hardware GPU (CPU inference)": "аппаратной видеокарты нет (распознавание на CPU)",
    "The Windows 11 camera could not start (is camera access allowed in Settings > Privacy & security > Camera?), "
    "so the DirectShow camera was installed instead. Allow camera access and click Install again to switch.":
        "Камера Windows 11 не запустилась (разрешён ли доступ к камере в «Параметры > Конфиденциальность и защита > "
        "Камера»?), поэтому установлена камера DirectShow. Разрешите доступ к камере и нажмите «Установить» ещё раз, "
        "чтобы переключиться.",
    "Setup tool": "Установщик камеры",
    "Open camera privacy settings": "Открыть параметры доступа к камере",
    "Camera components": "Компоненты камеры",
    "Registered camera components": "Зарегистрированные компоненты камеры",
    "File": "Файл",
    "File exists": "Файл на месте",
    "DirectShow camera (Windows 10/11)": "Камера DirectShow (Windows 10/11)",
    "Media Foundation camera (Windows 11)": "Камера Media Foundation (Windows 11)",
    "AI Virtual Camera (this app's Windows camera)": "AI Virtual Camera (камера этого приложения для Windows)",
    "Other apps select “AI Virtual Camera”. Needs the camera components installed (the Windows installer registers "
    "them).":
        "В других приложениях выберите «AI Virtual Camera». Нужны установленные компоненты камеры (их регистрирует "
        "установщик Windows).",
    "Not installed. The AI Virtual Camera components (native/win) are built and registered by the Windows "
    "installer; they are not present in this build yet.":
        "Не установлена. Компоненты AI Virtual Camera (native/win) собирает и регистрирует установщик Windows; в "
        "этой сборке их пока нет.",
    "Not installed: the camera components are here but not registered. Click Install (administrator rights needed).":
        "Не установлена: компоненты камеры есть, но не зарегистрированы. Нажмите «Установить» (нужны права администратора).",
    # camera name (Windows)
    "Camera name": "Имя камеры",
    "Name other apps show": "Имя, которое видят приложения",
    "Rename": "Переименовать",
    "Applies to both cameras (DirectShow and Media Foundation) and needs administrator rights once. Windows 11 adds "
    "“(Windows Virtual Camera)” to the Media Foundation camera's name. Apps that already have the camera open show "
    "the new name after they reopen it.":
        "Применяется к обеим камерам (DirectShow и Media Foundation), один раз нужны права администратора. Windows 11 "
        "добавляет к имени камеры Media Foundation «(Windows Virtual Camera)». Приложения, в которых камера уже "
        "открыта, покажут новое имя после повторного открытия.",
    "The name must be 1-63 characters, without quotation marks or control characters.":
        "Имя: от 1 до 63 символов, без кавычек и управляющих символов.",
    "Camera renamed to “{name}”.": "Камера переименована в «{name}».",
    "Rename failed (exit {rc}).": "Переименовать не удалось (код {rc}).",
}
