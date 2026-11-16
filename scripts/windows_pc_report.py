"""Performance report of this Windows PC (stage 10): run it on the target machine and send back the report folder.

    python scripts\\windows_pc_report.py [--out build\\pc-report] [--seconds 20] [--soak-minutes 0] [--quick]

Measures, without inventing anything:
  1. hardware: CPU, memory, GPUs, Windows build, ONNX Runtime providers
  2. AI: pure inference time (median / p95 ms) of every bundled ONNX model on every available provider
     (CUDA, DirectML on the best hardware GPU, CPU), on the sample photo
  3. the real pipeline per operating mode (scripts/benchmark.py: synthetic 1080p60 input, no camera)
  4. the camera: each Media Foundation mode read through Frame Server while the app sends frames
     (needs the Windows 11 camera installed: vcam-setup.exe --install)
  5. optionally a soak of N minutes with fault injection (vcstudio.app.soak + scripts/analyse_soak.py)
Writes <out>/report.json and <out>/report.md. Close other heavy apps (games, OBS rendering) for clean numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VCS_DISABLE_GLOBAL_HOTKEYS", "1")


def hardware() -> dict:
    from vcstudio.core.system_info import inspect_environment
    env = inspect_environment()
    env["gpus"] = sorted({a["name"] for a in env.pop("display_adapters", []) if not a.get("software")})
    import onnxruntime as ort
    env["onnxruntime"] = ort.__version__
    return env


def ai_by_provider(runs: int) -> list[dict]:
    import numpy as np
    import onnxruntime as ort
    from vcstudio.ai.model_manager import REPO_MODELS
    from vcstudio.ai.onnx_engine import _best_dml_device, cpu_threads, letterbox_blob
    from vcstudio.ai.testimages import load_bgra
    from vcstudio.core.resources import sample
    img = load_bgra(sample("bus.jpg"))
    avail = ort.get_available_providers()
    plans = []
    if "CUDAExecutionProvider" in avail:
        plans.append(("CUDA", "CUDAExecutionProvider", {}))
    if "DmlExecutionProvider" in avail:
        idx, name = _best_dml_device()
        if idx is not None:
            plans.append((f"DirectML ({name})", "DmlExecutionProvider", {"device_id": idx}))
    plans.append(("CPU", "CPUExecutionProvider", {}))
    out = []
    for model in sorted(REPO_MODELS.glob("yolo11*.onnx")):
        for label, prov, opts in plans:
            row = {"model": model.stem, "provider": label}
            try:
                so = ort.SessionOptions()
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                so.log_severity_level = 3
                so.intra_op_num_threads = cpu_threads()
                if prov == "DmlExecutionProvider":
                    so.enable_mem_pattern = False
                    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess = ort.InferenceSession(str(model), sess_options=so, providers=[(prov, opts)])
                if sess.get_providers()[0] != prov:
                    raise RuntimeError(f"fell back to {sess.get_providers()[0]}")
                inp = sess.get_inputs()[0]
                size = inp.shape[2] if isinstance(inp.shape[2], int) else 640
                blob = letterbox_blob(img, size)[0].astype(np.float32)
                for _ in range(3):
                    sess.run(None, {inp.name: blob})                     # warm-up (kernel compilation)
                ts = []
                for _ in range(runs):
                    t0 = time.perf_counter(); sess.run(None, {inp.name: blob}); ts.append((time.perf_counter() - t0) * 1e3)
                ts.sort()
                row.update(input=size, median_ms=round(statistics.median(ts), 2),
                           p95_ms=round(ts[int(0.95 * (len(ts) - 1))], 2), runs=runs)
            except Exception as e:                                       # reported, the rest still runs
                row["error"] = f"{type(e).__name__}: {e}"[:300]
            print(json.dumps(row), flush=True)
            out.append(row)
    return out


def pipeline(seconds: float, modes: str, out_dir: Path) -> dict | None:
    out = out_dir / "benchmark.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "benchmark.py"), "--seconds", str(seconds),
                        "--modes", modes, "--out", str(out)], cwd=str(ROOT), capture_output=True, text=True)
    (out_dir / "benchmark.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    return json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"error": r.stderr[-2000:]}


def mf_client() -> Path | None:
    from vcstudio.platform.win.vcam_status import components, setup_tool
    cands = [Path(c.path).parent / "mf_client_test.exe" for c in components() if c.path]
    st = setup_tool()
    if st:
        cands.append(st.parent / "mf_client_test.exe")
    cands.append(ROOT / "build" / "native" / "mf_client_test.exe")
    return next((p for p in cands if p.exists()), None)


def camera_modes() -> dict:
    import numpy as np
    from vcstudio.platform.win.vcam_writer import VcamWriter
    exe = mf_client()
    name = "AI Virtual Camera (Windows Virtual Camera)"
    if exe is None:
        return {"error": "mf_client_test.exe not found (scripts\\build_native_win.ps1)"}
    lst = subprocess.run([str(exe), "--list"], capture_output=True, text=True, timeout=30).stdout
    if name not in lst:
        return {"error": "the Windows 11 camera is not installed (vcam-setup.exe --install)", "devices": lst}
    img = np.zeros((1080, 1920, 4), np.uint8); img[..., 1] = 40; img[..., 2] = 200; img[..., 3] = 255
    w = VcamWriter(); w.configure(1920, 1080, 60)
    stop = threading.Event()

    def send():
        k, t0 = 0, time.perf_counter()
        while not stop.is_set():
            k += 1; w.publish(img); time.sleep(max(0.0, t0 + k / 60 - time.perf_counter()))
    threading.Thread(target=send, daemon=True).start()
    rows = []
    try:
        time.sleep(0.3)
        for i in range(6):
            r = subprocess.run([str(exe), "--device", name, "180", str(i), "raw"], capture_output=True, text=True,
                               timeout=60)
            m = re.search(r"native %d: (\d+x\d+) @ ([\d.]+) fps, subtype (\w+).*selected" % i, r.stdout)
            f = re.search(r"frames (\d+) in ([\d.]+) s \(([\d.]+) fps\), centre BGRA (\w\w) (\w\w) (\w\w)", r.stdout)
            if not (m and f):
                rows.append({"mode": i, "error": (r.stdout + r.stderr)[-400:]}); continue
            fmt = {"3231564E": "NV12", "00000016": "RGB32"}.get(m[3], m[3])
            rows.append({"mode": i, "size": m[1], "nominal_fps": float(m[2]), "format": fmt,
                         "measured_fps": float(f[3]), "frames": int(f[1]),
                         "centre_bgr": [int(f[4], 16), int(f[5], 16), int(f[6], 16)]})
    finally:
        stop.set(); w.close()
    return {"client": "Media Foundation source reader through Frame Server", "sent": "1920x1080 @ 60 fps",
            "modes": rows}


def soak(minutes: float, out_dir: Path) -> dict:
    d = out_dir / "soak"
    subprocess.run([sys.executable, "-m", "vcstudio.app.main", "--soak", str(d), "--minutes", str(minutes)],
                   cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT)))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "analyse_soak.py"), str(d)], capture_output=True,
                       text=True)
    return json.loads(r.stdout) if r.returncode == 0 else {"error": r.stderr[-2000:]}


def markdown(rep: dict) -> str:
    hw = rep["hardware"]
    L = ["# AI Virtual Camera Studio: Windows PC report", "",
         f"- CPU: {hw.get('cpu')} ({hw.get('cpu_cores')} cores / {hw.get('cpu_threads')} threads), "
         f"memory {hw.get('memory_gb')} GB", f"- Windows: {hw.get('windows')} (build {hw.get('windows_build')})",
         f"- GPUs: {', '.join(hw.get('gpus') or []) or 'none (software rendering)'}; hardware GPU: {hw.get('hardware_gpu')}",
         f"- ONNX Runtime {hw.get('onnxruntime')}: {', '.join(hw.get('onnx_providers') or [])}", "",
         "## AI inference (ms per frame, sample photo)", "", "| model | provider | median | p95 |", "|---|---|---|---|"]
    for r in rep.get("ai", []):
        L.append(f"| {r['model']} | {r['provider']} | {r.get('median_ms', r.get('error', ''))} | {r.get('p95_ms', '')} |")
    b = rep.get("pipeline") or {}
    if b.get("results"):
        L += ["", f"## Pipeline per mode ({b.get('input')}, {b.get('seconds_per_run')} s per run)", "",
              "| scenario | mode | compositor | AI | out fps (avg / min) | latency ms (p50 / p95) | AI fps (rate) "
              "| CPU % | GPU % | MB |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in b["results"]:
            gpu = r.get("gpu_util_pct_avg")
            scale = r.get("ai_rate_scale", 1.0)
            L.append(f"| {r['scenario']} | {r['mode']} | {r.get('compositor')} | {r['ai_model']} on {r.get('ai_backend')} | "
                     f"{r['output_fps_avg']} / {r['output_fps_min_1s']} | "
                     f"{r.get('latency_ms_p50', r['latency_ms_avg'])} / {r['latency_ms_p95']} | "
                     f"{r['ai_fps']}{'' if scale >= 1 else f' ({scale:.0%})'} | {r['app_cpu_pct']} | "
                     f"{'n/a' if gpu is None else gpu} | {r['footprint_mb']} |")
    c = rep.get("camera") or {}
    L += ["", "## Camera (Media Foundation, through Frame Server)", ""]
    if c.get("modes"):
        L += ["| mode | format | nominal fps | measured fps |", "|---|---|---|---|"]
        for r in c["modes"]:
            L.append(f"| {r.get('size', r['mode'])} | {r.get('format', '')} | {r.get('nominal_fps', '')} | "
                     f"{r.get('measured_fps', r.get('error', ''))} |")
    else:
        L.append(str(c.get("error", "skipped")))
    if rep.get("soak"):
        s = rep["soak"]
        L += ["", f"## Soak ({s.get('duration_min')} min)", "", "```", json.dumps(s.get("verdict"), indent=1), "```"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "pc-report")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--soak-minutes", type=float, default=0)
    ap.add_argument("--quick", action="store_true", help="short runs, two modes (a smoke check of the script)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    seconds, runs = (4, 8) if a.quick else (a.seconds, a.runs)
    modes = "performance,balanced" if a.quick else "performance,balanced,low_resource,max_quality"
    rep: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "hardware": hardware()}
    print("== AI inference by provider", flush=True)
    rep["ai"] = ai_by_provider(runs)
    print("== pipeline benchmark", flush=True)
    rep["pipeline"] = pipeline(seconds, modes, a.out)
    print("== camera", flush=True)
    rep["camera"] = camera_modes()
    if a.soak_minutes > 0:
        print(f"== soak {a.soak_minutes} min", flush=True)
        rep["soak"] = soak(a.soak_minutes, a.out)
    (a.out / "report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    (a.out / "report.md").write_text(markdown(rep), encoding="utf-8")
    print(f"\nwrote {a.out / 'report.md'} and report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
