# graph_viewer.py
import tkinter as tk
from typing import Dict, Tuple

class PatchViewer(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Patch Graph")
        self.canvas = tk.Canvas(self, width=800, height=600, bg="white")
        self.canvas.pack()
        self.modules: Dict[str, Tuple[float, float]] = {}
        self.connections: Dict[Tuple[str, str], Dict[str, int]] = {}
        self.param_labels: Dict[str, tk.Label] = {}

    def add_module(self, mod_id: str, label: str, x: float = None, y: float = None):
        if mod_id in self.modules:
            return
        if x is None or y is None:
            num = len(self.modules)
            x = (num % 5) * 150 + 50
            y = (num // 5) * 100 + 50
        self.canvas.create_rectangle(x - 50, y - 25, x + 50, y + 25, fill="lightblue")
        self.canvas.create_text(x, y, text=label)
        self.modules[mod_id] = (x, y)

    def connect(self, src: str, dst: str):
        src_mod = src.split(':')[0]
        dst_mod = dst.split(':')[0]
        if src_mod not in self.modules or dst_mod not in self.modules:
            return
        src_x, src_y = self.modules[src_mod]
        dst_x, dst_y = self.modules[dst_mod]
        line = self.canvas.create_line(src_x + 50, src_y, dst_x - 50, dst_y, arrow=tk.LAST)
        self.connections[(src, dst)] = {'line': line}

    def update_params(self, mod_id: str, params: Dict[str, float]):
        if mod_id not in self.modules:
            return
        x, y = self.modules[mod_id]
        param_text = ' '.join(f"{k}:{v:.1f}" for k, v in params.items())
        if mod_id in self.param_labels:
            self.param_labels[mod_id].config(text=param_text)
        else:
            label = tk.Label(self.canvas, text=param_text, bg="white")
            label.place(x=x - 50, y=y + 40)
            self.param_labels[mod_id] = label

    def clear(self):
        self.canvas.delete("all")
        self.modules.clear()
        self.connections.clear()
        self.param_labels.clear()