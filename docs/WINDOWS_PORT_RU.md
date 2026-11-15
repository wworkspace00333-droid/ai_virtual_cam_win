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
| AVFoundation захват | `core/video_capture.py` | Media Foundation через OpenCV (`CAP_MSMF`), запасной DirectShow (`CAP_DSHOW`); отдельный поток чтения, кадр — массив BGRA | ⬜ |
| AVFoundation список устройств | `core/devices.py` | Перечисление DirectShow-устройств (COM через ctypes: имя + device path как unique id) | ⬜ |
| Разрешение камеры (TCC) | `request_camera_access` | Windows: «Параметры → Конфиденциальность → Камера»; проверка ключа реестра `ConsentStore\webcam`, понятное сообщение | ⬜ |
| CoreMediaIO Camera Extension | `native/CameraExtension/*.swift` | **Своя камера**: (1) DirectShow source filter DLL — Windows 10/11; (2) Media Foundation virtual camera (`MFCreateVirtualCamera`) — Windows 11. Общий транспорт — разделяемая память | ⬜ |
| CMIO sink client | `native/VCSNative/CameraSink.swift`, `camera/output.py` | Писатель разделяемой памяти (Python `mmap` + именованные объекты Win32), протокол `vcam_shm.h` | ⬜ |
| Управление расширением | `camera/camera_manager.py` | Регистрация/удаление камеры: `vcam-setup.exe` с повышением прав (UAC), статус по реестру COM и списку устройств | ⬜ |
| CoreVideo `CVPixelBuffer` + пул | `core/pixelbuffer.py`, `core/frame.py` | На Windows кадр — непрерывный NumPy BGRA (пул буферов); GPU-путь — текстуры D3D11 | ⬜ |
| IOSurface (передача между процессами без копий) | расширение камеры | Разделяемая память (1 копия на кадр); вариант 2 — shared NT handle текстуры D3D11 для MF-источника | ⬜ |
| Metal компоновщик | `native/VCSNative/Compositor.swift`, `native/metal.py` | Direct3D 11 compute shader (HLSL), та же раскладка слоёв (28 float на слой); DLL `vcs_d3d11.dll` через ctypes; CPU-фоллбэк без изменений | ⬜ |
| Metal масштабирование (letterbox, превью) | `Scaler.swift` | `cv2.resize` (CPU), затем в D3D11 | ⬜ |
| Core ML + Vision (YOLO11 детекция) | `ai/coreml_engine.py`, `ai/model_manager.py` | ONNX Runtime: DirectML → CUDA → CPU (автовыбор); модели `.onnx` + `.json`; NMS в NumPy (уже есть в `segmentation.py`) | ⬜ |
| Core ML сегментация YOLO11-seg | `ai/segmentation.py` | Тот же постпроцессинг, вход через ONNX Runtime; меняется только вызов модели | ⬜ |
| Vision лица | `VisionFaceDetector` | YuNet (OpenCV Zoo, MIT) через `cv2.FaceDetectorYN`; только прямоугольники, без распознавания личности | ⬜ |
| Импорт моделей `.mlpackage` | `ai/model_import.py` | Импорт `.onnx`: проверка входов/выходов, пробный прогон, замер — та же логика отчёта | ⬜ |
| Конвертация моделей | `scripts/convert_models.py` | Экспорт Ultralytics `format=onnx` (opset 17, FP16 для GPU / FP32 для CPU) | ⬜ |
| ImageIO декодирование картинок | `replacement/assets.py` | OpenCV `IMREAD_UNCHANGED` (PNG/JPG/WEBP/BMP/TIFF) + Pillow для GIF/HEIC (опционально `pillow-heif`); премультипликация альфы | ⬜ |
| AVAssetReader/VideoToolbox | `replacement/video.py` | PyAV (FFmpeg в комплекте; аппаратное декодирование D3D11VA где доступно); существующий FFmpeg-subprocess декодер остаётся запасным | ⬜ |
| OBS плагин (POSIX shm) | `native/obs-plugin/*` | Порт плагина под Win32 (`CreateFileMapping`, тот же заголовок и seqlock); чтение в Python через `mmap` без нативной DLL | ⬜ |
| libVCSShm (чтение shm) | `native/shm-reader` | Не нужен: Python `mmap` + NumPy, копия сразу в массив кадра | ⬜ |
| Carbon глобальные клавиши | `native/Hotkeys.swift`, `native/hotkeys.py` | `RegisterHotKey` в отдельном потоке с циклом сообщений (ctypes) | ⬜ |
| `screencapture` (карточка для игры) | `game/cardframe.py` | GDI BitBlt основного монитора (ctypes), с учётом DPI | ⬜ |
| `fcntl.flock` (один экземпляр) | `core/single_instance.py` | `msvcrt.locking` на том же `app.lock` (снимается ОС при падении) | ⬜ |
| `~/Library/Application Support` | `core/paths.py` | `%LOCALAPPDATA%\AIVirtualCameraStudio` (модели и ассеты большие — не Roaming) | ⬜ |
| sysctl / ioreg / libproc / systemextensionsctl | `core/system_info.py`, `core/performance.py` | psutil, реестр (CPU), DXGI/WMI (видеокарты), PDH-счётчики `GPU Engine` (загрузка GPU), private bytes как «footprint» | ⬜ |
| PyObjC prewarm / autorelease pools | `core/pyobjc_prewarm.py`, конвейер, ИИ, видео | На Windows — пустые заглушки (`contextlib.nullcontext`) | ⬜ |
| `defaults read AppleLanguages` | `i18n.py` | `GetUserDefaultUILanguage` | ⬜ |
| PySide6 UI | `ui/*` | Остаётся PySide6; правка mac-специфичных текстов (⌘, Dock, «Системные настройки»), страница камеры под Windows-регистрацию | ⬜ |
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
| 1 | Аудит и архитектура | этот документ | — | ознакомиться |
| 2 | Структура платформ, окружение | `vcstudio/platform`, пути, один экземпляр, заглушки PyObjC, requirements-windows; приложение импортируется на Windows | ✅ юнит-тесты | — |
| 3 | GUI и конфигурация | окно стартует (`--smoke-test`), тестовый шаблон проходит через конвейер | ✅ смоук-тест, скриншоты страниц | внешний вид |
| 4 | Захват и OBS | MSMF/DirectShow источник, список устройств, OBS-плагин Windows + `mmap`-читатель, переподключение | частично (без камеры: тестовый источник, OBS-shm через имитатор писателя) | OBS Virtual Camera и плагин |
| 5 | ИИ и трекинг | ONNX Runtime + YuNet, конвертация моделей, импорт `.onnx`, эталонная проверка на `bus.jpg` и coco16 | ✅ на CPU (точность, совпадение с macOS-результатами) | скорость с DirectML/CUDA |
| 6 | ROI и замена | картинки, видео (PyAV), маски, сегментация — существующие тесты зелёные на Windows | ✅ | — |
| 7 | D3D11 компоновщик | `vcs_d3d11.dll`, паритет с CPU-бэкендом | 🟡 WARP (программный D3D11) для паритета | скорость на реальной видеокарте |
| 8 | Виртуальная камера | DirectShow-фильтр + MF-источник + регистрация + петлевой тест | частично: DirectShow через GraphEdit-подобный тест; MF — если на сервере есть Frame Server | Камера Windows, Zoom, Teams, Chrome, Discord |
| 9 | Сквозная интеграция | OBS → ИИ → замена → камера → клиент | — | ✅ |
| 10 | Производительность и стресс | замеры 1080p60, soak 4 ч | только CPU-цифры, помеченные как «сервер без GPU» | реальные цифры |
| 11 | Установщик | Inno Setup: установка, регистрация камеры, удаление | ✅ установка/удаление на сервере | на чистой машине |

После каждого этапа — коммит в ветку `windows-port` и запись статуса в эту таблицу. Отправка в GitHub — только по согласию владельца.

## 8. Риски и ограничения

1. **Среда разработки**: Windows Server 2025 без камеры и видеокарты (Microsoft Basic Display Adapter). Здесь нельзя проверить реальный захват, DirectML/CUDA, скорость D3D11 и работу камеры в Zoom/Teams/Chrome/Discord. Эти пункты будут помечены 🟡 до проверки на настоящем ПК. Цифры производительности с сервера не будут выдаваться за целевые.
2. **MF-камера из службы**: доступ Frame Server (LocalService, сессия 0) к памяти пользовательской сессии — проверяется первым в этапе 8; запасной вариант — передача через именованный канал, открываемый службой.
3. **Frame Server на Windows Server** может отсутствовать — MF-камеру тогда можно проверить только на Windows 11.
4. **Неподписанные DLL**: Windows их загружает, но SmartScreen предупреждает при установке; отдельные приложения могут игнорировать неподписанные DirectShow-фильтры (известно для некоторых версий Teams — именно поэтому нужен MF-вариант). Подпись Authenticode рекомендуется для распространения.
5. **1080p60 на CPU-пути**: копия кадра в shm + конвертация в NV12 ≈ 2–4 мс; компоновка нескольких регионов на CPU может не уложиться в 16,7 мс на слабых CPU — для этого этап 7 (D3D11).
6. **Лицензии**: веса и код Ultralytics YOLO — AGPL-3.0 (как и в macOS-версии). YuNet — MIT. ONNX Runtime — MIT. PyAV — BSD, но поставляемые с ним сборки FFmpeg — LGPL/GPL; лицензию сборки нужно проверить до распространения.
7. **Две виртуальные камеры одновременно** (DirectShow и MF) могут показаться клиенту как два устройства с одинаковым именем на Windows 11 — на Windows 11 установщик ставит только MF-вариант, на Windows 10 — DirectShow.
