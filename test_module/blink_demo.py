import tkinter as tk
from tkinter import ttk
from enum import Enum

logger = lambda msg: print(msg)  # Simple print (swap to logging)

class LedState(Enum):
    OFF = 0
    BLINK_SLOW = 1
    BLINK_RAPID = 2
    SOLID = 3
    ERROR = 4

class BlinkDemo:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("LED Blink Demo - DMS Whiteboard (Timeouts)")
        self.root.geometry("300x280")
        
        self.led_state = LedState.OFF
        self.led_label = tk.Label(self.root, text="LED OFF", bg="gray", width=15, height=2, relief="solid")
        self.led_label.pack(pady=20)
        
        # Buttons for states (static)
        ttk.Button(self.root, text="Slow Blink (Yellow)", command=lambda: self.set_state(LedState.BLINK_SLOW)).pack(pady=2)
        ttk.Button(self.root, text="Rapid Blink (Red)", command=lambda: self.set_state(LedState.BLINK_RAPID)).pack(pady=2)
        ttk.Button(self.root, text="Solid Green", command=lambda: self.set_state(LedState.SOLID)).pack(pady=2)
        ttk.Button(self.root, text="Off Gray", command=lambda: self.set_state(LedState.OFF)).pack(pady=2)
        ttk.Button(self.root, text="Error Orange", command=lambda: self.set_state(LedState.ERROR)).pack(pady=2)
        
        # Timeout tests: General flash, specific OFF→RED 3s
        ttk.Button(self.root, text="Flash Orange 3s (General)", command=self.flash_orange).pack(pady=5)
        ttk.Button(self.root, text="Flash Red 3s from Off (Specific)", command=self.flash_red_from_off).pack(pady=5)
        
        # Extras
        ttk.Button(self.root, text="Text: BLINKING F/S or SOLID", command=self.toggle_text).pack(pady=5)
        ttk.Button(self.root, text="Cycle Color", command=self.color_change).pack(pady=5)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()
    
    def set_state(self, state):
        self.led_state = state
        color_map = {
            'OFF': 'gray',
            'BLINK_SLOW': 'yellow',
            'BLINK_RAPID': 'red',
            'SOLID': 'green',
            'ERROR': 'orange'
        }
        color = color_map.get(state.name, 'gray')
        self.root.after(0, lambda: (
            self.led_label.config(bg=color, text=f"LED {state.name}"),
            logger(f"Set LED state: {state.name} → bg={color}")
        ))
    
    def flash_orange(self):
        """General: Flash orange 3s, revert to prior state."""
        prior_state = self.led_state
        prior_color = self.led_label.cget('bg')
        self.root.after(0, lambda: (
            self.led_label.config(bg='orange', text="LED FLASHING ORANGE"),
            logger(f"Flash orange 3s (prior: {prior_state.name})")
        ))
        self.root.after(3000, lambda: (
            self.led_label.config(bg=prior_color, text=f"LED {prior_state.name}"),
            logger(f"Reverted to {prior_state.name} after 3s")
        ))
    
    def flash_red_from_off(self):
        """Specific: Force OFF → RED 3s, revert to prior (module-style override)."""
        prior_state = self.led_state
        prior_color = self.led_label.cget('bg')
        # Step 1: Force OFF (if not already)
        self.root.after(0, lambda: self.led_label.config(bg='gray', text="LED OFF (Forced)"))
        # Step 2: After 100ms, to RED
        self.root.after(100, lambda: (
            self.led_label.config(bg='red', text="LED FLASHING RED"),
            logger(f"Flash red 3s from off (prior: {prior_state.name})")
        ))
        # Step 3: Revert after 3s total
        self.root.after(3100, lambda: (
            self.led_label.config(bg=prior_color, text=f"LED {prior_state.name}"),
            logger(f"Reverted to {prior_state.name} after 3s")
        ))
    
    def toggle_text(self):
        current = self.led_label.cget("text")
        if "BLINKING S" in current or "SOLID" in current:
            new_text = "LED BLINKING F"
        else:
            new_text = "LED BLINKING S"
        self.led_label.config(text=new_text)
        logger(f"Text toggled to: {new_text}")
    
    def color_change(self):
        current_bg = self.led_label.cget("bg")
        colors = {"gray": "green", "green": "yellow", "yellow": "orange", "orange": "red", "red": "gray"}
        new_bg = colors.get(current_bg, "gray")
        self.led_label.config(bg=new_bg)
        logger(f"BG cycled to: {new_bg}")
    
    def on_closing(self):
        self.root.destroy()

if __name__ == "__main__":
    BlinkDemo()