"""Tiny splash subprocess. Intentionally imports only Python's standard library."""

import json
import math
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk


class SplashWindow(tk.Tk):
    def __init__(self, stream, *, hidden=False):
        super().__init__()
        self.withdraw()
        self.title("Himera · Запуск")
        self.configure(bg="#ffffff")
        self.resizable(False, False)
        self.exit_code = 0
        self.closed = False
        self.events = queue.Queue()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        width, height = 460, 270
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2 - 40)
        self.geometry(f"{width}x{height}+{x}+{y}")
        body = tk.Frame(self, bg="#ffffff", padx=30, pady=22)
        body.pack(fill="both", expand=True)
        brand = tk.Frame(body, bg="#ffffff")
        brand.pack(fill="x")
        mark = tk.Canvas(brand, width=62, height=62, bg="#ffffff", highlightthickness=0)
        mark.pack(side="left", padx=(0, 14))
        mark.create_rectangle(0, 0, 62, 62, fill="#0078d7", outline="")
        points = []
        for i in range(52):
            px = 5 + i
            py = 33 - 20 * math.exp(-(((i - 26) / 13) ** 2)) * math.cos((i - 26) * 0.48)
            points.extend((px, py))
        mark.create_line(*points, fill="#ffffff", width=2, smooth=True)
        titles = tk.Frame(brand, bg="#ffffff")
        titles.pack(side="left")
        tk.Label(
            titles, text="HIMERA", bg="#ffffff", fg="#202020", font=("Segoe UI", 25, "bold")
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="Обработка и анализ спектров",
            bg="#ffffff",
            fg="#666666",
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        self.stage = tk.StringVar(self, value="Начинаем запуск…")
        tk.Label(
            body,
            textvariable=self.stage,
            bg="#ffffff",
            fg="#202020",
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(fill="x", pady=(25, 10))
        style = ttk.Style(self)
        # Keep the progress colour consistent even when Windows uses another accent.
        style.theme_use("clam")
        style.configure(
            "Startup.Horizontal.TProgressbar",
            background="#0078d7",
            troughcolor="#e7edf3",
            borderwidth=0,
        )
        self.progress = ttk.Progressbar(
            body, style="Startup.Horizontal.TProgressbar", maximum=5, mode="determinate"
        )
        self.progress.pack(fill="x", ipady=1)
        footer = tk.Frame(body, bg="#ffffff")
        footer.pack(fill="x", pady=(12, 0))
        self.activity = tk.Canvas(footer, width=28, height=18, bg="#ffffff", highlightthickness=0)
        self.activity.pack(side="left")
        self.dots = [
            self.activity.create_oval(i * 9, 6, i * 9 + 5, 11, fill="#c4d3df", outline="")
            for i in range(3)
        ]
        self.tick = 0
        tk.Label(
            footer,
            text="Подготавливаем рабочее пространство",
            bg="#ffffff",
            fg="#666666",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=5)
        self.completed = 0
        self.stage_updates = 0
        self.poll_id = self.after(40, self.poll)
        self.animation_id = self.after(120, self.animate)
        threading.Thread(target=self.read_events, args=(stream,), daemon=True).start()
        if not hidden:
            self.deiconify()
            self.lift()

    def read_events(self, stream):
        try:
            for line in stream:
                try:
                    self.events.put(json.loads(line))
                except (ValueError, TypeError):
                    continue
        finally:
            self.events.put(None)

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event is None:
                    self.destroy()
                    return
                if not isinstance(event, dict):
                    continue
                self.stage.set(str(event.get("stage", "Загрузка…")))
                try:
                    completed = max(0, min(5, float(event.get("completed", 0))))
                except (ValueError, TypeError):
                    continue
                self.completed = max(self.completed, completed)
                self.progress["value"] = self.completed
                self.stage_updates += 1
        except queue.Empty:
            pass
        self.poll_id = self.after(40, self.poll)

    def animate(self):
        for i, dot in enumerate(self.dots):
            self.activity.itemconfigure(dot, fill="#0078d7" if i == self.tick % 3 else "#c4d3df")
        self.tick += 1
        self.animation_id = self.after(140, self.animate)

    def destroy(self):
        if self.closed:
            return
        self.closed = True
        for callback in (self.poll_id, self.animation_id):
            self.after_cancel(callback)
        super().destroy()

    def cancel(self):
        self.exit_code = 20
        self.destroy()


def main():
    # Parent sends UTF-8 JSON on stdin, irrespective of the console code page.
    sys.stdin.reconfigure(encoding="utf-8")
    window = SplashWindow(sys.stdin, hidden="--hidden" in sys.argv)
    window.mainloop()
    return window.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
