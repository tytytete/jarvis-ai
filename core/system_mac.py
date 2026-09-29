import os
import re
import subprocess

from .system_base import BaseSystem

APP_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
]

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
        # exit-код osascript ненадёжен (0 даже при ошибке) — проверяем ещё и stderr
        ok, _, err = self._run(["osascript", "-e", script], timeout=timeout)
        return ok and not err.strip()

    # ---- приложения -------------------------------------------------------

    def open_application(self, name):
        name = name.strip().strip("\"'")
        if not name:
            return False
        if name.endswith(".app") or os.path.exists(name) or "/" in name:
            if self._run(["open", name])[0]:
                return True
        if self._run(["open", "-a", name])[0]:
            return True
        found = self.search_application(name)
        if found:
            return self._run(["open", found])[0]
        return False

    def close_application(self, name):
        name = name.strip().strip("\"'")
        if not name:
            return False
        if name.endswith(".app") or "/" in name:
            base = os.path.basename(name)
            if base.endswith(".app"):
                name = base[:-4]
        # Если приложение не установлено/не найдено — точно не «успешно».
        if self.search_application(name) is None:
            return False
        if self._osascript(f'quit app "{name}"'):
            return True
        return self._osascript(f'tell application "{name}" to quit')

    def search_application(self, name):
        query = f'kMDItemKind == "Application" && kMDItemDisplayName == "*{name}*"c'
        ok, out, _ = self._run(["mdfind", query])
        if ok:
            for line in out.splitlines():
                line = line.strip()
                if line:
                    return line
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

    # ---- файлы / URL ------------------------------------------------------

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
        ok, out, _ = self._run(["mdfind", "-name", query])
        if ok:
            results = [line for line in out.splitlines() if line.strip()]
            if results:
                return results[:limit]
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

    def open_url(self, url):
        url = url.strip().strip("\"'")
        if not url:
            return False
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            url = "https://" + url
        return self._run(["open", url])[0]

    # ---- звук / яркость ---------------------------------------------------

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

    def unmute(self):
        return self._osascript("set volume without output muted")

    def brightness_up(self):
        return self._osascript('tell application "System Events" to key code 113')

    def brightness_down(self):
        return self._osascript('tell application "System Events" to key code 107')

    # ---- система ----------------------------------------------------------

    def screenshot(self):
        import datetime
        path = os.path.expanduser(
            "~/Desktop/screenshot_%s.png" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        ok, _, _ = self._run(["screencapture", "-x", path])
        return path if ok else None

    def lock_screen(self):
        return self._osascript(
            'tell application "System Events" to keystroke "q" using {control down, command down}'
        )

    def sleep(self):
        return self._run(["pmset", "sleepnow"])[0]

    def type_text(self, text):
        # Экранируем кавычки/бэкслеши для AppleScript-строки
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return self._osascript(f'tell application "System Events" to keystroke "{escaped}"')
