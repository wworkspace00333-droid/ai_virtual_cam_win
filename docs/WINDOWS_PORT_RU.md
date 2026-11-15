# Порт AI Virtual Camera Studio на Windows: аудит и план

Документ ведётся по ходу порта. Статусы в матрице обновляются по мере этапов.
Обозначения: ✅ сделано и проверено · 🟡 сделано, не проверено на реальном железе · ⏳ в работе · ⬜ не начато.

## 1. Как устроен текущий проект (macOS)

Python 3.12 + PySide6 (≈12,8 тыс. строк), нативная часть на Swift/C (≈1,5 тыс. строк), тесты (≈3,5 тыс. строк, pytest).

Потоки (см. `docs/ARCHITECTURE.md`):

```
UI (Qt) ◄── превью ≤30 fps
Захват (AVFoundation / OBS shm) ──LatestSlot──► Конвейер ──► компоновщик (Metal|CPU) ──► виртуальная камера (CMIO sink)
                                     └───────────► ИИ-поток (Core ML YOLO, Vision лица, сегментация) ──► ByteTrack
```

Ключевые принципы, которые сохраняются в порте:
- **Слоты «последний кадр»**, никаких FIFO-очередей; ИИ никогда не задерживает вывод (трекер предсказывает рамки на момент кадра).
- **Изоляция отказов**: ошибка ИИ, слоя замены или входа не останавливает вывод; аварийный passthrough; сторожевой таймер.
- **Планирование на CPU, пиксели на GPU**: `Compositor.plan()` строит `DrawOp`, бэкенд их исполняет. CPU-бэкенд — эталон, GPU-бэкенд обязан совпадать с ним (тесты паритета).
- Всё локально, сетевого кода нет.

Точка привязки к платформе — объект `Frame`: он держит либо `CVPixelBuffer` (macOS, без копий), либо массив NumPy BGRA. Через `pixel_buffer_for()` и `frame.bgra()` весь остальной код работает с кадром, не зная, откуда он.

## 2. Матрица миграции

| Компонент macOS | Где в коде | Замена на Windows | Статус |
|---|---|---|---|
| AVFoundation захват | `core/video_capture.py` → `platform/win/capture.py` | OpenCV `CAP_DSHOW` (по умолчанию: OBS Virtual Camera — фильтр DirectShow) или `CAP_MSMF`; устройство ищется по сохранённому id/имени при каждом старте; поток чтения, BGRA-буферы из пула, который не перезаписывает кадр, пока его кто-то читает | 🟡 код и тесты готовы; на этом сервере OBS Virtual Camera не открывается ни OpenCV, ни FFmpeg (см. риски), MSMF отсутствует (Server без Media Foundation) — проверить на Windows 10/11 |
| AVFoundation список устройств | `core/devices.py` → `platform/win/devices.py` | DirectShow (`ICreateDevEnum`, форматы из `IAMStreamConfig`) и Media Foundation (`MFEnumDeviceSources`, типы потока 0), порядок = индексы OpenCV | ✅ (проверено: OBS Virtual Camera найдена через DirectShow) |
| Разрешение камеры (TCC) | `request_camera_access` | `platform/win/privacy.py`: переключатели `ConsentStore\webcam` (устройство, приложения, классические приложения) и групповая политика; кнопка открывает `ms-settings:privacy-webcam` | ✅ |
| CoreMediaIO Camera Extension | `native/CameraExtension/*.swift` | **Своя камера**: (1) DirectShow source filter DLL — Windows 10/11; (2) Media Foundation virtual camera (`MFCreateVirtualCamera`) — Windows 11. Общий транспорт — разделяемая память | ⬜ |
| CMIO sink client | `native/VCSNative/CameraSink.swift`, `camera/output.py` | Писатель разделяемой памяти (Python `mmap` + именованные объекты Win32), протокол `vcam_shm.h` | ⬜ |
| Управление расширением | `camera/camera_manager.py` | Статус по реестру COM (`platform/win/vcam_status.py`, CLSID фильтра DirectShow и MF-источника) и списку устройств; установка/удаление — запуск `vcam-setup.exe` через UAC (`ShellExecuteEx runas`) | 🟡 статус и запуск готовы; сам `vcam-setup.exe` — этап 8 |
| CoreVideo `CVPixelBuffer` + пул | `core/pixelbuffer.py`, `core/frame.py` | На Windows кадр — непрерывный NumPy BGRA (пул буферов); GPU-путь — текстуры D3D11 | ⬜ |
| IOSurface (передача между процессами без копий) | расширение камеры | Разделяемая память (1 копия на кадр); вариант 2 — shared NT handle текстуры D3D11 для MF-источника | ⬜ |
| Metal компоновщик | `native/VCSNative/Compositor.swift`, `native/metal.py` → `native/win/d3d11/vcs_d3d11.cpp`, `platform/win/d3d11.py` | Compute-шейдеры Direct3D 11 (FL 11_0): кадр в `R32_UINT`, ассеты/маски с билинейной выборкой, раздельный гауссов фильтр с groupshared-тайлами; общий с Metal код построения слоёв; выбор видеокарты с наибольшей видеопамятью, иначе WARP; на WARP размытие отдаётся OpenCV (точный паритет, в 3 раза быстрее); сборка `scripts/build_native_win.ps1` (статический CRT) | ✅ 26 тестов паритета с CPU (композиции, маски, эффекты, шейдерное размытие у края кадра, порядок 8 слоёв, видео, откат на CPU при сбое, параллельные вызовы); на WARP: 5 слоёв 1080p 64 мс против 308 мс CPU; 🟡 скорость на аппаратной видеокарте не замерена |
| Metal масштабирование (letterbox, превью) | `Scaler.swift` | `cv2.resize` (CPU) | ✅ (на CPU, отдельный GPU-путь не нужен для 640×640 и превью) |
| Core ML + Vision (YOLO11 детекция) | `ai/coreml_engine.py`, `ai/model_manager.py` → `ai/onnx_engine.py`, `ai/backend.py` | ONNX Runtime: CUDA → DirectML (лучшая аппаратная видеокарта) → CPU; модели `.onnx` + `.json`; NMS в NumPy; потоки CPU — половина физических ядер (ИИ не мешает захвату) | ✅ совпадение с эталоном PyTorch на bus.jpg; mAP50 на coco16 как у Core ML; 🟡 DirectML/CUDA не проверены (нет GPU) |
| Core ML сегментация YOLO11-seg | `ai/segmentation.py` | Тот же постпроцессинг (общий код), вход через ONNX Runtime | ✅ маски силуэтов на bus.jpg |
| Vision лица | `VisionFaceDetector` | YuNet (OpenCV Zoo, MIT, в репозитории) через `cv2.FaceDetectorYN` на уменьшенной копии кадра; сбой модели лиц не останавливает распознавание объектов | ✅ 2 лица на bus.jpg, ~25–34 мс на CPU |
| Импорт моделей `.mlpackage` | `ai/model_import.py` | Импорт `.onnx`: один вход-изображение, выходы YOLO raw / end-to-end / YOLO-seg, имена классов из метаданных Ultralytics, пробный прогон и замер | ✅ |
| Конвертация моделей | `scripts/convert_models.py --format onnx` | Ultralytics `format=onnx`, FP32, opset 17, без NMS внутри; загрузка YuNet с проверкой SHA-256 | ✅ 5 моделей |
| Автовыбор модели | `ai/engine.py` | Быстрый отказ от явно медленной модели (медиана 5 замеров > 3× бюджета) вместо 40 замеров — на CPU переход yolo11m → yolo11s занимает ~3 с вместо ~16 с | ✅ |
| ImageIO декодирование картинок | `replacement/assets.py`, `ai/testimages.py` → `platform/win/imaging.py` | OpenCV из памяти (пути с кириллицей), PNG/JPG/WEBP/BMP/TIFF, 16 бит → 8 бит; Pillow для GIF и прочего (HEIC — с `pillow-heif`); премультипликация альфы | ✅ |
| AVAssetReader/VideoToolbox | `replacement/video.py` → `platform/win/video_decode.py` | PyAV (FFmpeg в комплекте): MP4/MOV/M4V/MKV/WEBM/AVI, FourCC как на macOS, кадр «на экране» в момент старта (как AVAssetReader), альфа ProRes 4444, поворот; программное декодирование | ✅ все тесты видео (перемотка с точностью до кадра, 60 fps из 30 fps с зацикливанием, пауза/скорость/смещение, синхронизация по объектам); 🟡 аппаратное D3D11VA — не включено |
| OBS плагин (POSIX shm) | `native/obs-plugin/*` | Один исходник для macOS и Windows: `Local\vcs-obs-frames` (`CreateFileMapping` + `SEC_RESERVE`, фиксируется только нужный объём), тот же заголовок и seqlock; сборка `scripts/build_obs_plugin_win.ps1` (MSVC, библиотеки импорта из DLL установленного OBS) | ✅ проверено вживую с OBS 33: плагин загружается, 1200×752 @ 30 кадров/с доходят до приложения, переподключение после перезапуска OBS за 1,2 с |
| libVCSShm (чтение shm) | `native/shm-reader` | Не нужен на Windows: `platform/win/obs_shm.py` (ctypes `OpenFileMapping` + NumPy) | ✅ |
| Чтение JSON с BOM | `core/jsonio.py` | `utf-8-sig`: настройки, сохранённые Блокнотом/PowerShell 5, больше не считаются повреждёнными (раньше сбрасывались) | ✅ |
| Carbon глобальные клавиши | `native/Hotkeys.swift`, `native/hotkeys.py` | `platform/win/hotkeys.py`: `RegisterHotKey` в своём потоке с циклом сообщений; действие доставляется в GUI-поток сигналом Qt | ✅ (проверено: регистрация, конфликт, реальное нажатие через `keybd_event`) |
| `screencapture` (карточка для игры) | `game/cardframe.py` → `platform/win/screenshot.py` | GDI BitBlt + CAPTUREBLT основного монитора, физические пиксели при DPI-awareness | ✅ вживую: окно с сеткой ArUco на экране → 72 маркера, область камеры совпала с окном до пикселя |
| `fcntl.flock` (один экземпляр) | `core/single_instance.py` | `msvcrt.locking` на байте за концом `app.lock` (снимается ОС при падении; pid читается) | ✅ |
| `~/Library/Application Support` | `core/paths.py` | `%LOCALAPPDATA%\AIVirtualCameraStudio` (модели и ассеты большие — не Roaming) | ✅ |
| sysctl / ioreg / libproc / systemextensionsctl | `core/system_info.py`, `core/performance.py` → `platform/win/sysinfo.py`, `platform/win/gpu_monitor.py` | psutil, реестр (CPU), DXGI (видеокарты), PDH-счётчики `GPU Engine`/`GPU Adapter Memory`, Private Bytes как «footprint» | ✅ (счётчики GPU 🟡: на сервере без GPU честно отдают «нет данных») |
| PyObjC prewarm / autorelease pools | `core/pyobjc_prewarm.py`, конвейер, ИИ, видео | `vcstudio.platform.autorelease_pool()` — на Windows `nullcontext` | ✅ |
| `defaults read AppleLanguages` | `i18n.py` | `GetUserDefaultUILanguage` | ✅ |
| Часы `CMClockGetHostTimeClock` | `core/frame.py` | `time.perf_counter` (QPC; совпадает с часами OBS `os_gettime_ns`) | ✅ |
| Кодировка файлов (неявная cp1251/cp1252 на Windows) | 10 мест в `vcstudio/` | явный `encoding="utf-8"` (иначе профили и модели с кириллицей ломаются) | ✅ |
| PySide6 UI | `ui/*` | Остаётся PySide6. Тексты, зависящие от ОС, — `vcstudio.platform.text()` (macOS-строки не изменены); методы ввода и компоновщики — из `platform`; страница камеры показывает компоненты Windows | ✅ 12 страниц открываются; 🟡 тексты страницы «ИИ-движок» и диалога импорта моделей (Core ML) меняются на этапе 5 |
| Настройки с другой ОС | `core/config.py` | Допустимые значения берутся из `platform`; `avfoundation`/`metal` из macOS-настроек автоматически заменяются на `mediafoundation`/`auto` | ✅ |
| ByteTrack, ROI, профили, SQLite, конфиг, watchdog, композиционная математика, карточка для игры (алгоритм) | `ai/tracking.py`, `roi/*`, `core/*`, `replacement/compositor.py` (CPU), `game/cardframe.py` | Без изменений | ⬜ (проверить тестами) |
| PyInstaller `.app` + DMG, подпись Apple | `packaging/*`, `scripts/build_app.sh` | PyInstaller onedir + Inno Setup; установщик регистрирует камеру; подпись Authenticode — по желанию (SmartScreen) | ⬜ |

## 3. Модули, которые переиспользуются без изменений

`ai/tracking.py`, `ai/detection.py`, `ai/labels.py`, `ai/evaluation.py`, `roi/geometry.py`, `roi/model.py`, `roi/resolver.py`, `replacement/spec.py`, `replacement/service.py`, CPU-часть `replacement/compositor.py` (планирование, `CpuBackend`, маски), `VideoPlayer`/`PlayerPool` в `replacement/video.py` (тайминг воспроизведения), `core/config.py` (кроме допустимых значений), `core/database.py`, `core/frame_queue.py`, `core/jsonio.py`, `core/logging_setup.py`, `core/profiles.py`, `core/profile_bundle.py`, `core/watchdog.py`, `core/capture_supervisor.py`, `core/video_pipeline.py` (кроме autorelease и имени нативного буфера), `core/bench_source.py`, `game/cardframe.py` (кроме `screenshot()`), `i18n_ru.py`, почти весь `ui/*`.

## 4. Модули, которые переписываются или получают Windows-реализацию

| Модуль | Что делается |
|---|---|
| `core/frame.py` | `host_time_s` → `time.perf_counter` (QPC) на Windows; `bgra()` для массивов уже работает |
| `core/pixelbuffer.py` | Windows: «буфер» = непрерывный NumPy BGRA из пула |
| `core/video_capture.py` + `core/devices.py` | новый `MediaFoundationSource` / список устройств DirectShow |
| `core/obs_shm_source.py` | Windows-читатель через `mmap` |
| `core/paths.py`, `core/resources.py`, `core/single_instance.py`, `core/system_info.py`, `core/performance.py`, `core/pyobjc_prewarm.py`, `i18n.py` | ветки под Windows |
| `ai/coreml_engine.py`, `ai/model_manager.py`, `ai/model_import.py`, `ai/segmentation.py` (вход модели) | ONNX Runtime + YuNet; общий интерфейс детектора сохраняется (`detect(frame, conf, iou) -> (dets, ms)`) |
| `replacement/assets.py`, `replacement/video.py` (декодер) | OpenCV/Pillow, PyAV |
| `replacement/compositor.py` | `D3D11Backend` рядом с `MetalBackend`, выбор `auto|d3d11|cpu` |
| `native/metal.py`, `native/hotkeys.py` | Windows-реализации |
| `camera/output.py`, `camera/camera_manager.py`, `camera/test_sender.py` | shm-писатель, регистрация камеры |
| `app/selftest.py`, `app/soak.py` | платформенные проверки |
| `ui/pages/camera_page.py`, `ui/pages/input_page.py`, `ui/pages/settings_page.py`, `ui/game_card_dialog.py`, `ui/video_view.py` | тексты и действия под Windows |
| Новое: `native/win/*` | `vcam_shm.h`, DirectShow-фильтр, MF-источник, `vcam-setup.exe`, `vcs_d3d11.dll`, OBS-плагин для Windows, CMake |

Платформенный код собирается в `vcstudio/platform/` (`mac` и `win`), общий код выбирает реализацию через один модуль. macOS-реализация не удаляется.

## 5. Стратегия виртуальной камеры

Драйвер ядра (AVStream) **не используется**: требует подписи драйвера через Microsoft (EV-сертификат, attestation) — непрактично и рискованно.

```
Приложение (Python)
  └─ кадр BGRA 1920×1080 ──► разделяемая память «AIVirtualCamera.Frames»
                              (заголовок + 3 слота, seqlock, heartbeat, как в OBS-протоколе)
        ├─► vcam_dshow.dll  (DirectShow source filter, COM, regsvr32)    Windows 10/11
        │      загружается в процесс клиента (Zoom, Discord, OBS, браузеры с DirectShow)
        └─► vcam_mf.dll     (IMFMediaSource, COM; регистрируется MFCreateVirtualCamera)  Windows 11 22000+
               загружается службой Frame Server; видна всем приложениям, включая «Камеру» и новые Teams
```

- **Форматы**: приложение пишет BGRA; DLL отдают клиенту NV12, YUY2 или RGB32 по согласованию (конвертация в DLL, SSE2). Режимы 1920×1080 @ 60/30, 1280×720 @ 60/30.
- **Метки времени**: из заголовка shm (QPC приложения), пересчёт в reference clock клиента; при пропуске кадров — повтор последнего.
- **Нет приложения / нет кадров > 1 с**: карточка «AI Virtual Camera Studio не запущена» (как в macOS-расширении), устройство не пропадает у клиента.
- **Доступ из службы**: Frame Server работает под LocalService в сессии 0. Объект разделяемой памяти создаётся приложением с DACL, разрешающим чтение LocalService; MF-источник открывает его через префикс `Session\<id активной консольной сессии>\`. Это главный технический риск этапа 8 — проверяется первым.
- **Защита от петли**: «AI Virtual Camera» исключается из списка входов (как сейчас `is_own_output`).
- **Регистрация**: установщик (права администратора) — `regsvr32 vcam_dshow.dll` и `vcam-setup.exe --install` (MF, `MFVirtualCameraLifetime_System`, `AccessAllUsers`); удаление — обратные команды. Без регистрации приложение показывает честный статус «камера не установлена».
- **Проверка готовности** (не по факту компиляции): камера видна в списке устройств DirectShow/MF, и тест-петля захватывает с неё кадры с известным маркером (порт `--camera-loopback-test`).

## 6. Совместимость с железом

| Подсистема | Порядок выбора | Фоллбэк |
|---|---|---|
| ИИ | CUDA (если установлен пакет `onnxruntime-gpu` и есть NVIDIA) → DirectML (любая DX12-видеокарта: NVIDIA/AMD/Intel) → CPU | CPU всегда; автоматический выбор размера модели по замеренному p95 (существующая логика `QUALITY_BUDGET_MS`) |
| Компоновка | D3D11 (feature level 11_0+, любой адаптер) | CPU (NumPy/OpenCV) — эталон |
| Декодирование видео | PyAV + D3D11VA | PyAV программно → ffmpeg subprocess |
| Захват | MSMF | DirectShow |

Дистрибутив по умолчанию ставит `onnxruntime-directml` (один пакет на всех производителей). `onnxruntime-gpu` (CUDA) и `onnxruntime-directml` нельзя ставить одновременно — CUDA-сборка будет отдельным вариантом. Информация о видеокартах берётся через DXGI; выбранные бэкенды показываются на страницах «ИИ» и «Производительность».

## 7. План этапов

| # | Этап | Что считается готовым | Проверяется здесь (Windows Server, без камеры и GPU) | Проверяется у вас |
|---|---|---|---|---|
| 1 ✅ | Аудит и архитектура | этот документ | — | ознакомиться |
| 2 ✅ | Структура платформ, окружение | `vcstudio/platform`, пути, один экземпляр, заглушки PyObjC, requirements-windows; приложение импортируется на Windows | ✅ юнит-тесты: 167 прошли (было 124), 48 падений — подсистемы этапов 4–6 | — |
| 3 ✅ | GUI и конфигурация | окно стартует, все 12 страниц, тексты Windows (EN/RU), глобальные клавиши, статус доступа к камере и компонентов камеры | ✅ 173 теста прошли (8 новых для Windows); скриншоты всех страниц (RU) | внешний вид на вашем мониторе |
| 4 ✅ | Захват и OBS | MSMF/DirectShow источник, список устройств, OBS-плагин Windows + читатель общей памяти, переподключение | ✅ 181 тест; вживую: OBS 33 → плагин → приложение 30 кадров/с, перезапуск OBS; 🟡 захват из OBS Virtual Camera не проверен (сервер) | OBS Virtual Camera и веб-камера через DirectShow/MSMF |
| 5 ✅ | ИИ и трекинг | ONNX Runtime + YuNet, конвертация моделей, импорт `.onnx`, эталонная проверка на `bus.jpg` и coco16 | ✅ 209 тестов; точность совпадает с macOS; трекинг со стабильными ID; на этом CPU (2 ядра): yolo11n ~55–75 мс, yolo11m ~490 мс | скорость с DirectML/CUDA |
| 6 ✅ | ROI и замена | картинки, видео (PyAV), маски, сегментация — существующие тесты зелёные на Windows | ✅ 254 теста; вживую: две зоны (PNG с альфой и видео) на 1080p через конвейер; ⚠ CPU-компоновка 1080p здесь ~120 мс/кадр (2 ядра) → этап 7 | — |
| 7 ✅ | D3D11 компоновщик | `vcs_d3d11.dll`, паритет с CPU-бэкендом | ✅ 281 тест; вживую на WARP (без видеокарты): конвейер 1080p с двумя зонами — 59 кадров/с на выходе, рендер 13,6 мс (было 7 кадров/с и 121 мс на CPU) | скорость на реальной видеокарте |
| 8 | Виртуальная камера | DirectShow-фильтр + MF-источник + регистрация + петлевой тест | частично: DirectShow через GraphEdit-подобный тест; MF — если на сервере есть Frame Server | Камера Windows, Zoom, Teams, Chrome, Discord |
| 9 | Сквозная интеграция | OBS → ИИ → замена → камера → клиент | — | ✅ |
| 10 | Производительность и стресс | замеры 1080p60, soak 4 ч | только CPU-цифры, помеченные как «сервер без GPU» | реальные цифры |
| 11 | Установщик | Inno Setup: установка, регистрация камеры, удаление | ✅ установка/удаление на сервере | на чистой машине |

После каждого этапа — коммит в ветку `windows-port` и запись статуса в эту таблицу. Отправка в GitHub — только по согласию владельца.

## 8. Окружение разработки на Windows

```bat
:: Python 3.12 x64; venv по КОРОТКОМУ пути (в PySide6 есть пути длиннее 260 символов)
py -3.12 -m venv C:\vcs-env
C:\vcs-env\Scripts\python -m pip install -r requirements-windows.txt pytest pytest-qt pytest-timeout
C:\vcs-env\Scripts\python -m pytest -q
C:\vcs-env\Scripts\python -m vcstudio
```

Для нативной части (этапы 7–8, 11): Visual Studio 2022 Build Tools (C++ x64, Windows 11 SDK, CMake), Inno Setup 6.

## 9. Риски и ограничения

1. **Среда разработки**: Windows Server 2025 без камеры и видеокарты (Microsoft Basic Display Adapter). Здесь нельзя проверить реальный захват, DirectML/CUDA, скорость D3D11 и работу камеры в Zoom/Teams/Chrome/Discord. Эти пункты будут помечены 🟡 до проверки на настоящем ПК. Цифры производительности с сервера не будут выдаваться за целевые.
2. **MF-камера из службы**: доступ Frame Server (LocalService, сессия 0) к памяти пользовательской сессии — проверяется первым в этапе 8; запасной вариант — передача через именованный канал, открываемый службой.
3. **Frame Server на Windows Server** может отсутствовать — MF-камеру тогда можно проверить только на Windows 11.
4. **Неподписанные DLL**: Windows их загружает, но SmartScreen предупреждает при установке; отдельные приложения могут игнорировать неподписанные DirectShow-фильтры (известно для некоторых версий Teams — именно поэтому нужен MF-вариант). Подпись Authenticode рекомендуется для распространения.
5. **1080p60 на CPU-пути**: копия кадра в shm + конвертация в NV12 ≈ 2–4 мс; компоновка нескольких регионов на CPU может не уложиться в 16,7 мс на слабых CPU — для этого этап 7 (D3D11).
6. **Лицензии**: веса и код Ultralytics YOLO — AGPL-3.0 (как и в macOS-версии). YuNet — MIT. ONNX Runtime — MIT. PyAV — BSD, но поставляемые с ним сборки FFmpeg — LGPL/GPL; лицензию сборки нужно проверить до распространения.
8. **OBS Virtual Camera на этом сервере.** Фильтр OBS 33 (`obs-virtualcam-module64.dll`) здесь не отдаёт `IBaseFilter` через моникер устройства (`E_NOINTERFACE`); первый `CoCreateInstance` в процессе тоже отказывает, последующие работают. OpenCV и FFmpeg открывают камеру через моникер, поэтому захват здесь не стартует (приложение сообщает об этом понятной ошибкой и советует прямую передачу из OBS). На этом Windows Server нет компонента Media Foundation. На обычных Windows 10/11 захват OBS Virtual Camera через OpenCV широко используется, но здесь это не доказано — проверить у вас. Рекомендуемый вход на Windows — прямая передача из OBS (плагин): она проверена вживую и не занимает виртуальную камеру OBS.
9. **Две виртуальные камеры одновременно** (DirectShow и MF) могут показаться клиенту как два устройства с одинаковым именем на Windows 11 — на Windows 11 установщик ставит только MF-вариант, на Windows 10 — DirectShow.
