import json
import subprocess


def detect_monitors():
    """Возвращает список экранов с именем и разрешением (macOS, best-effort)."""
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(out.stdout)
        displays = []
        for gpu in data.get("SPDisplaysDataType", []):
            for disp in gpu.get("spdisplays_ndrvs", []):
                displays.append({
                    "name": disp.get("_name", "unknown"),
                    "resolution": disp.get("_spdisplays_resolution", "unknown"),
                })
        return displays
    except Exception:
        return []


def ocr_screen():
    """Локальный OCR — подключим на следующем этапе (EasyOCR/RapidOCR)."""
    raise NotImplementedError("OCR пока не подключён (см. PROJECT_SPEC.md, раздел 4.3).")
