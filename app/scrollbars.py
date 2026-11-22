"""Shared thin, rounded scrollbars with hover/wheel reveal and short fades."""
import time
import tkinter as tk
from tkinter import ttk


class AutoHideScrollbar(tk.Canvas):
    """A quiet rounded thumb with a permanent, narrow mouse target."""

    def __init__(self, master, command, orient="vertical", bg=None, target=None,
                 reveal_on_change=True):
        if bg is None and target is not None:
            bg = target.cget("bg")
        if bg is None:
            try:
                bg = master.cget("bg")
            except tk.TclError:
                bg = ttk.Style(master).lookup("TFrame", "background") or "#fbfbfe"
        self.orient = orient
        self.reveal_on_change = reveal_on_change
        # Let the viewport determine length; Canvas defaults would otherwise
        # force compact fields to be hundreds of pixels tall (or wide).
        dimensions = ({"width": 12, "height": 1} if orient == "vertical"
                      else {"height": 12, "width": 1})
        super().__init__(master, bg=bg, bd=0, highlightthickness=0, **dimensions)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self.visible = False
        self._opacity = 0.0
        self._fade_job = None
        self._hovered = self._edge_hover = False
        self._drag_offset = None
        self._previous_grab = None
        self._hide_job = None
        self._scope_bindings = []
        self._target = None
        self._has_view = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind(sequence, self._wheel)
        if target is not None:
            self.attach(target)

    @property
    def can_scroll(self):
        return self.last - self.first < 0.999

    def attach(self, target):
        """Observe the viewport's window, including its dynamically added children."""
        self._detach()
        self._target = target
        owner = target.winfo_toplevel()
        for sequence, callback in (("<Motion>", self._scope_motion),
                                   ("<Leave>", self._scope_motion),
                                   ("<MouseWheel>", self._scope_wheel),
                                   ("<Button-4>", self._scope_wheel),
                                   ("<Button-5>", self._scope_wheel)):
            self._scope_bindings.append((owner, sequence, owner.bind(sequence, callback, add="+")))

    def _detach(self):
        for owner, sequence, token in self._scope_bindings:
            try:
                owner.unbind(sequence, token)
            except tk.TclError:
                pass
        self._scope_bindings.clear()

    def _pointer_in_target(self, event):
        target = self._target
        if target is None or not target.winfo_exists() or not target.winfo_ismapped():
            return False
        x = event.x_root - target.winfo_rootx()
        y = event.y_root - target.winfo_rooty()
        return 0 <= x < target.winfo_width() and 0 <= y < target.winfo_height()

    def _scope_motion(self, event):
        near_edge = False
        if self._pointer_in_target(event):
            target = self._target
            if self.orient == "vertical":
                near_edge = event.x_root >= target.winfo_rootx() + target.winfo_width() - 18
            else:
                near_edge = event.y_root >= target.winfo_rooty() + target.winfo_height() - 18
        self.set_edge_hover(near_edge)

    def _scope_wheel(self, event):
        if self._pointer_in_target(event):
            self.reveal()

    def _wheel(self, event):
        if self.can_scroll:
            direction = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
            self.command("scroll", direction * 3, "units")
            self.reveal()
        return "break"

    def destroy(self):
        self._detach()
        if self._fade_job is not None:
            self.after_cancel(self._fade_job)
            self._fade_job = None
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
            self._hide_job = None
        super().destroy()

    def set(self, first, last):
        previous = self.first
        self.first, self.last = float(first), float(last)
        moved = self._has_view and abs(previous - self.first) > 1e-8
        self._has_view = True
        if not self.can_scroll:
            self._set_visible(False)
        elif self._hovered or self._edge_hover:
            self._set_visible(True)
        if moved and self.can_scroll and self.reveal_on_change:
            self.reveal()
        self._draw()

    def reveal(self):
        if self.can_scroll:
            self._set_visible(True)
        self._schedule_hide()

    def _set_visible(self, visible):
        if visible == self.visible:
            return
        self.visible = visible
        if self._fade_job is not None:
            self.after_cancel(self._fade_job)
            self._fade_job = None
        start = self._opacity
        target = 1.0 if visible else 0.0
        started = time.monotonic()
        duration = 0.16 if visible else 0.20

        def step():
            progress = min(1.0, (time.monotonic() - started) / duration)
            eased = progress * progress * (3 - 2 * progress)
            self._opacity = start + (target - start) * eased
            self._fade_job = self.after(16, step) if progress < 1 else None
            self._draw()

        step()

    def set_edge_hover(self, hovered):
        if hovered == self._edge_hover:
            return
        self._edge_hover = hovered
        if hovered:
            self.reveal()
        else:
            self._schedule_hide()

    def _hover(self, hovered):
        self._hovered = hovered
        if hovered:
            self.reveal()
        else:
            self._schedule_hide()

    def _schedule_hide(self):
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
        self._hide_job = self.after(1100, self._hide)

    def _hide(self):
        self._hide_job = None
        if not (self._hovered or self._edge_hover or self._drag_offset is not None):
            self._set_visible(False)

    def _thumb(self):
        track = max(1, (self.winfo_height() if self.orient == "vertical" else self.winfo_width()) - 8)
        length = min(track, max(28, track * (self.last - self.first)))
        travel = track - length
        maximum = max(1e-9, 1 - (self.last - self.first))
        top = 4 + travel * min(1, self.first / maximum)
        return top, length, travel, maximum

    def _draw(self):
        self.delete("all")
        if (self.visible or self._opacity > 0) and self.can_scroll:
            top, length, _, _ = self._thumb()
            background = self.winfo_rgb(self.cget("bg"))
            foreground = self.winfo_rgb("#8b8fa3")
            color = "#" + "".join(
                f"{round((bg + (fg - bg) * self._opacity) / 257):02x}"
                for bg, fg in zip(background, foreground))
            points = ((6, top + 3, 6, top + length - 3) if self.orient == "vertical"
                      else (top + 3, 6, top + length - 3, 6))
            self.create_line(*points, width=6,
                             capstyle="round", fill=color,
                             tags="thumb")

    def _press(self, event):
        if not self.can_scroll:
            return
        self.reveal()
        top, length, _, _ = self._thumb()
        position = event.y if self.orient == "vertical" else event.x
        self._drag_offset = position - top if top <= position <= top + length else length / 2
        self._previous_grab = self.grab_current()
        self.grab_set()
        self._drag(event)

    def _drag(self, event):
        if self._drag_offset is None:
            return
        _, _, travel, maximum = self._thumb()
        position = event.y if self.orient == "vertical" else event.x
        fraction = max(0, min(1, (position - self._drag_offset - 4) / max(1, travel)))
        self.command("moveto", fraction * maximum)

    def _release(self, _event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.grab_release()
            previous = self._previous_grab
            self._previous_grab = None
            if previous is not None and previous is not self and previous.winfo_exists():
                previous.grab_set()
            self._schedule_hide()


