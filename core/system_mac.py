import os
import subprocess

from .system_base import BaseSystem

APP_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
]

# Фолбэк для файлов: быстрый `find` с таймаутом (без рекурсивного os.walk, который
# может зависнуть на iCloud-каталогах). Documents обрабатывает Spotlight на обычной машине.
FILE_SEARCH_ROOTS = [
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Downloads"),
]


class MacSystem(BaseSystem):
    platform = "macos"

    def _run(self, cmd, timeout=15):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, "", str(e)

    def _osascript(self, script, timeout=10):
        return self._run(["osascript", "-e", script], timeout=timeout)[0]

    def open_application(self, name):
        name = name.strip().strip("\"'")
        if not name:
            return False
        # Прямой путь, .app или путь с разделителем
        if name.endswith(".app") or os.path.exists(name) or "/" in name:
            if self._run(["open", name])[0]:
                return True
        # Попытка по имени через LaunchServices
        if self._run(["open", "-a", name])[0]:
            return True
        # Поиск по Spotlight и стандартным каталогам, затем открытие по полному пути
        found = self.search_application(name)
        if found:
            return self._run(["open", found])[0]
        return False

    def search_application(self, name):
        # 1) Spotlight (может быть отключён)
        query = f'kMDItemKind == "Application" && kMDItemDisplayName == "*{name}*"c'
        ok, out, _ = self._run(["mdfind", query])
        if ok:
            for line in out.splitlines():
                line = line.strip()
                if line:
                    return line
        # 2) Фолбэк: скан стандартных каталогов приложений
        return self._scan_app_dirs(name)

    def _scan_app_dirs(self, name):
        target = name.lower()
        if target.endswith(".app"):
            target = target[:-4]
        for directory in APP_DIRS:
            if not os.path.isdir(directory):
                continue
            try:
                entries = os.listdir(directory)
            except OSError:
                continue
            for entry in entries:
                if entry.lower() == target + ".app":
                    return os.path.join(directory, entry)
            for entry in entries:
                if entry.lower().endswith(".app") and target in entry.lower():
                    return os.path.join(directory, entry)
        return None

    def open_file(self, query):
        query = query.strip().strip("\"'")
        if not query:
            return False
        if os.path.exists(query):
            return self._run(["open", query])[0]
        found = self.search_files(query, limit=1)
        if found:
            return self._run(["open", found[0]])[0]
        return False

    def search_files(self, query, limit=5):
        # 1) Spotlight (основной механизм macOS)
        ok, out, _ = self._run(["mdfind", "-name", query])
        if ok:
            results = [line for line in out.splitlines() if line.strip()]
            if results:
                return results[:limit]
        # 2) Фолбэк: find по Desktop/Downloads с жёстким таймаутом
        results = []
        for root in FILE_SEARCH_ROOTS:
            if not os.path.isdir(root):
                continue
            ok, out, _ = self._run(
                ["find", root, "-maxdepth", "3", "-iname", f"*{query}*", "-type", "f"],
                timeout=2,
            )
            if not ok:
                continue
            for line in out.splitlines():
                line = line.strip()
                if line:
                    results.append(line)
                    if len(results) >= limit:
                        return results
        return results

    def volume_up(self, step=10):
        return self._osascript(
            f"set volume output volume (output volume of (get volume settings) + {step})"
        )

    def volume_down(self, step=10):
        return self._osascript(
            f"set volume output volume (output volume of (get volume settings) - {step})"
        )

    def mute(self):
        return self._osascript("set volume with output muted")
