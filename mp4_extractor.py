# -*- coding: utf-8 -*-
import json
import os
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD


VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm")
PREVIEW_BOX = (560, 315)
CROP_PREVIEW_BOX = (560, 240)

current_video_path = None
video_info = {"width": None, "height": None, "duration": None}
orig_img_start = None
orig_img_end = None
crop_drag_anchor = None


def run_hidden(cmd, check=True):
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def parse_time_to_seconds(value):
    value = value.strip()
    if not value:
        raise ValueError("time is empty")

    parts = value.split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError("unsupported time format")


def seconds_to_time(value):
    value = max(0.0, value)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = value % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def get_video_info(path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration:format=duration",
        "-of",
        "json",
        path,
    ]
    result = run_hidden(cmd)
    data = json.loads(result.stdout)
    stream = data.get("streams", [{}])[0]
    duration = stream.get("duration") or data.get("format", {}).get("duration")
    return {
        "width": int(stream["width"]) if stream.get("width") else None,
        "height": int(stream["height"]) if stream.get("height") else None,
        "duration": float(duration) if duration else None,
    }


def get_safe_preview_times(time_text):
    requested = parse_time_to_seconds(time_text)
    duration = video_info.get("duration")
    if duration:
        requested = min(requested, max(0.0, duration - 0.04))

    times = [requested]
    for backoff in (0.04, 0.1, 0.25, 0.5, 1.0):
        candidate = requested - backoff
        if candidate >= 0:
            times.append(candidate)
    return [seconds_to_time(t) for t in dict.fromkeys(round(t, 3) for t in times)]


def extract_preview_image(time_text, output_path):
    if not current_video_path:
        return False

    for seek_time in get_safe_preview_times(time_text):
        attempts = [
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", seek_time, "-i", current_video_path],
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", current_video_path, "-ss", seek_time],
        ]
        for base_cmd in attempts:
            cmd = base_cmd + ["-frames:v", "1", "-q:v", "2", output_path]
            result = run_hidden(cmd, check=False)
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
    return False


def load_image(path):
    with Image.open(path) as img:
        return img.convert("RGBA")


def fit_image(img, box_size):
    max_w, max_h = box_size
    ratio = min(max_w / img.width, max_h / img.height)
    width = max(1, int(img.width * ratio))
    height = max(1, int(img.height * ratio))
    return img.resize((width, height), Image.Resampling.LANCZOS)


class PreviewCanvas(tk.Canvas):
    def __init__(self, parent, size, placeholder):
        self.box_w, self.box_h = size
        super().__init__(
            parent,
            width=self.box_w,
            height=self.box_h,
            bg="#0d0d0d",
            highlightthickness=1,
            highlightbackground="#555",
        )
        self.image_ref = None
        self.display_bounds = None
        self.source_size = None
        self.placeholder = placeholder
        self.show_text(placeholder)

    def show_text(self, text=None):
        self.delete("all")
        self.image_ref = None
        self.display_bounds = None
        self.source_size = None
        self.create_text(
            self.box_w // 2,
            self.box_h // 2,
            text=text or self.placeholder,
            fill="#d0d0d0",
            font=("Meiryo", 11),
            justify="center",
        )

    def show_image(self, img):
        self.delete("all")
        fitted = fit_image(img, (self.box_w, self.box_h))
        self.image_ref = ImageTk.PhotoImage(fitted)
        left = (self.box_w - fitted.width) // 2
        top = (self.box_h - fitted.height) // 2
        self.display_bounds = (left, top, fitted.width, fitted.height)
        self.source_size = img.size
        self.create_image(left, top, image=self.image_ref, anchor="nw")

    def clamp_to_image_area(self, x, y):
        if not self.display_bounds:
            return None
        left, top, width, height = self.display_bounds
        return (
            min(max(x, left), left + width),
            min(max(y, top), top + height),
        )

    def canvas_to_image_point(self, x, y):
        if not self.display_bounds or not self.source_size:
            return None
        left, top, width, height = self.display_bounds
        source_w, source_h = self.source_size
        x, y = self.clamp_to_image_area(x, y)
        image_x = round((x - left) * source_w / width)
        image_y = round((y - top) * source_h / height)
        return (
            min(max(image_x, 0), source_w),
            min(max(image_y, 0), source_h),
        )


def create_label(parent, text, font_size=10, color="white"):
    return tk.Label(parent, text=text, bg="#2d2d2d", fg=color, font=("Meiryo", font_size))


def create_input(parent, row, col, label_text, default_val, on_change=None):
    create_label(parent, label_text).grid(row=row, column=col, padx=5, pady=5, sticky="e")
    entry = tk.Entry(parent, font=("Meiryo", 11), justify="center", width=12)
    entry.insert(0, default_val)
    entry.grid(row=row, column=col + 1, padx=5, pady=5, sticky="w")
    if on_change:
        entry.bind("<KeyRelease>", on_change)
    return entry


def set_entry_value(entry, value):
    entry.delete(0, tk.END)
    entry.insert(0, str(value))


def parse_crop_box():
    x1 = int(entry_x1.get())
    x2 = int(entry_x2.get())
    y1 = int(entry_y1.get())
    y2 = int(entry_y2.get())
    if x2 <= x1 or y2 <= y1:
        raise ValueError("crop size must be positive")
    return x1, y1, x2, y2


def validate_crop_box_for_image(img, box):
    x1, y1, x2, y2 = box
    if x1 < 0 or y1 < 0 or x2 > img.width or y2 > img.height:
        raise ValueError("crop area is outside image")
    if x2 <= x1 or y2 <= y1:
        raise ValueError("crop area is outside image")
    return x1, y1, x2, y2


def make_overlay_preview(orig_img, box):
    crop_box = validate_crop_box_for_image(orig_img, box)
    overlay = Image.new("RGBA", orig_img.size, (0, 0, 0, 150))
    clear = Image.new("RGBA", orig_img.size, (0, 0, 0, 0))
    mask = Image.new("L", orig_img.size, 180)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rectangle(crop_box, fill=0)
    masked = Image.composite(overlay, clear, mask)
    preview = Image.alpha_composite(orig_img, masked)

    draw = ImageDraw.Draw(preview)
    line_width = max(4, round(min(orig_img.size) / 220))
    draw.rectangle(crop_box, outline="#ff4d4d", width=line_width)
    return preview


def make_cropped_preview(orig_img, box):
    crop_box = validate_crop_box_for_image(orig_img, box)
    return orig_img.crop(crop_box)


def draw_previews(event=None):
    if not orig_img_start and not orig_img_end:
        return

    try:
        crop_box = parse_crop_box()
    except ValueError:
        for canvas in (canvas_start_crop, canvas_end_crop):
            canvas.show_text("クロップ座標を入力してください")
        return

    def update_pair(orig_img, source_canvas, crop_canvas):
        if not orig_img:
            source_canvas.show_text()
            crop_canvas.show_text()
            return
        try:
            source_canvas.show_image(make_overlay_preview(orig_img, crop_box))
            crop_canvas.show_image(make_cropped_preview(orig_img, crop_box))
        except ValueError:
            source_canvas.show_image(orig_img)
            crop_canvas.show_text("クロップ範囲が画像外です")

    update_pair(orig_img_start, canvas_start_source, canvas_start_crop)
    update_pair(orig_img_end, canvas_end_source, canvas_end_crop)


def draw_crop_drag_rect(start, end):
    canvas_start_source.delete("crop_drag")
    canvas_start_source.create_rectangle(
        start[0],
        start[1],
        end[0],
        end[1],
        outline="#42a5f5",
        width=2,
        dash=(5, 3),
        tags="crop_drag",
    )


def begin_crop_drag(event):
    global crop_drag_anchor
    if not orig_img_start or not canvas_start_source.display_bounds:
        return
    crop_drag_anchor = canvas_start_source.clamp_to_image_area(event.x, event.y)
    draw_crop_drag_rect(crop_drag_anchor, crop_drag_anchor)


def update_crop_drag(event):
    if crop_drag_anchor is None:
        return
    current = canvas_start_source.clamp_to_image_area(event.x, event.y)
    if current:
        draw_crop_drag_rect(crop_drag_anchor, current)


def finish_crop_drag(event):
    global crop_drag_anchor
    if crop_drag_anchor is None:
        return

    start_canvas = crop_drag_anchor
    end_canvas = canvas_start_source.clamp_to_image_area(event.x, event.y)
    crop_drag_anchor = None
    canvas_start_source.delete("crop_drag")

    if not end_canvas:
        return

    start_point = canvas_start_source.canvas_to_image_point(*start_canvas)
    end_point = canvas_start_source.canvas_to_image_point(*end_canvas)
    if not start_point or not end_point:
        return

    x1, x2 = sorted((start_point[0], end_point[0]))
    y1, y2 = sorted((start_point[1], end_point[1]))
    if x2 - x1 < 2 or y2 - y1 < 2:
        lbl_status.config(text="選択範囲が小さすぎます", fg="#ff7777")
        return

    set_entry_value(entry_x1, x1)
    set_entry_value(entry_x2, x2)
    set_entry_value(entry_y1, y1)
    set_entry_value(entry_y2, y2)
    draw_previews()
    lbl_status.config(text=f"クロップ範囲を選択しました: {x2 - x1} x {y2 - y1}", fg="#8bd17c")


def cancel_crop_drag(event):
    global crop_drag_anchor
    crop_drag_anchor = None
    canvas_start_source.delete("crop_drag")


def fetch_previews(event=None):
    global orig_img_start, orig_img_end
    if not current_video_path:
        return

    lbl_status.config(text="プレビュー画像を生成中...", fg="#ffcc66")
    root.update_idletasks()

    temp_dir = tempfile.gettempdir()
    p_start = os.path.join(temp_dir, "convert_prev_start.jpg")
    p_end = os.path.join(temp_dir, "convert_prev_end.jpg")

    try:
        start_ok = extract_preview_image(entry_start.get(), p_start)
        end_ok = extract_preview_image(entry_end.get(), p_end)
    except ValueError:
        lbl_status.config(text="開始/終了時刻の入力値を確認してください", fg="#ff7777")
        return

    orig_img_start = load_image(p_start) if start_ok else None
    orig_img_end = load_image(p_end) if end_ok else None

    canvas_start_source.show_text("開始位置の取得に失敗しました") if not start_ok else None
    canvas_end_source.show_text("終了位置の取得に失敗しました") if not end_ok else None
    canvas_start_crop.show_text("開始位置の取得に失敗しました") if not start_ok else None
    canvas_end_crop.show_text("終了位置の取得に失敗しました") if not end_ok else None

    draw_previews()

    if start_ok and end_ok:
        lbl_status.config(text="準備完了", fg="#8bd17c")
    elif start_ok:
        lbl_status.config(text="開始位置のみ取得できました。終了時刻を少し手前にしてください。", fg="#ffcc66")
    else:
        lbl_status.config(text="プレビュー取得に失敗しました", fg="#ff7777")


def get_dropped_file(event):
    files = root.tk.splitlist(event.data)
    if not files:
        return None
    return os.path.normpath(files[0])


def on_drop(event):
    global current_video_path, video_info, orig_img_start, orig_img_end

    input_path = get_dropped_file(event)
    if not input_path:
        return
    if not input_path.lower().endswith(VIDEO_EXTENSIONS):
        messagebox.showerror("エラー", "動画ファイルをドロップしてください。")
        return

    current_video_path = input_path
    orig_img_start = None
    orig_img_end = None
    canvas_start_source.show_text()
    canvas_end_source.show_text()
    canvas_start_crop.show_text()
    canvas_end_crop.show_text()

    lbl_file.config(text=f"選択中: {os.path.basename(current_video_path)}")

    try:
        video_info = get_video_info(current_video_path)
        if video_info["width"] and video_info["height"]:
            lbl_video_info.config(
                text=f"{video_info['width']} x {video_info['height']} / {video_info['duration']:.3f}s"
                if video_info["duration"]
                else f"{video_info['width']} x {video_info['height']}"
            )
    except Exception as exc:
        video_info = {"width": None, "height": None, "duration": None}
        lbl_video_info.config(text="動画情報を取得できませんでした")
        lbl_status.config(text=f"ffprobe エラー: {exc}", fg="#ff7777")

    fetch_previews()


def process_video():
    if not current_video_path:
        messagebox.showwarning("警告", "先に動画をウィンドウへドロップしてください。")
        return

    try:
        start_sec = parse_time_to_seconds(entry_start.get())
        end_sec = parse_time_to_seconds(entry_end.get())
        if end_sec <= start_sec:
            raise ValueError("end must be later than start")

        x1, y1, x2, y2 = parse_crop_box()
        target_h = int(entry_h.get())
        fps = float(entry_fps.get())
        crf = int(entry_crf.get())
        if target_h <= 0 or fps <= 0 or not 0 <= crf <= 51:
            raise ValueError("invalid output settings")
    except ValueError:
        messagebox.showerror("エラー", "時間・座標・出力設定の入力値を確認してください。")
        return

    if video_info.get("width") and video_info.get("height"):
        if x1 < 0 or y1 < 0 or x2 > video_info["width"] or y2 > video_info["height"]:
            messagebox.showerror("エラー", "クロップ範囲が動画サイズを超えています。")
            return

    crop_w, crop_h = x2 - x1, y2 - y1
    dir_name, file_name = os.path.split(current_video_path)
    base_name, ext = os.path.splitext(file_name)
    output_path = os.path.join(dir_name, f"{base_name}_processed{ext}")

    duration = end_sec - start_sec
    filter_str = f"crop={crop_w}:{crop_h}:{x1}:{y1},scale=-2:{target_h}"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        seconds_to_time(start_sec),
        "-i",
        current_video_path,
        "-t",
        seconds_to_time(duration),
        "-vf",
        filter_str,
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        output_path,
    ]

    lbl_status.config(text="変換中...", fg="#ffcc66")
    root.update_idletasks()

    result = run_hidden(cmd, check=False)
    if result.returncode == 0:
        lbl_status.config(text=f"完了: {os.path.basename(output_path)}", fg="#8bd17c")
        messagebox.showinfo("完了", f"動画の変換が完了しました。\n{output_path}")
    else:
        lbl_status.config(text="変換エラー", fg="#ff7777")
        messagebox.showerror("エラー", result.stderr or "ffmpeg の実行に失敗しました。")


def enable_window_drop(widget):
    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", on_drop)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        enable_window_drop(child)


root = TkinterDnD.Tk()
root.title("動画クロップ変換ツール")
root.geometry("1220x920")
root.minsize(1080, 820)
root.configure(bg="#2d2d2d")

lbl_file = tk.Label(
    root,
    text="動画をこのウィンドウ内のどこへでもドロップしてください",
    bg="#1e1e1e",
    fg="#a5d6a7",
    font=("Meiryo", 11, "bold"),
    pady=9,
)
lbl_file.pack(fill="x")

lbl_video_info = tk.Label(root, text="", bg="#2d2d2d", fg="#bdbdbd", font=("Meiryo", 9))
lbl_video_info.pack(fill="x", pady=(4, 0))

frame_prev = tk.Frame(root, bg="#2d2d2d")
frame_prev.pack(padx=18, pady=10)
frame_prev.grid_columnconfigure(0, weight=1)
frame_prev.grid_columnconfigure(1, weight=1)

start_frame = tk.Frame(frame_prev, bg="#2d2d2d")
start_frame.grid(row=0, column=0, padx=10, sticky="n")
end_frame = tk.Frame(frame_prev, bg="#2d2d2d")
end_frame.grid(row=0, column=1, padx=10, sticky="n")

create_label(start_frame, "開始位置プレビュー", 11, "#dddddd").pack(anchor="w", pady=(0, 5))
canvas_start_source = PreviewCanvas(start_frame, PREVIEW_BOX, "開始位置プレビュー")
canvas_start_source.configure(cursor="crosshair")
canvas_start_source.bind("<ButtonPress-1>", begin_crop_drag)
canvas_start_source.bind("<B1-Motion>", update_crop_drag)
canvas_start_source.bind("<ButtonRelease-1>", finish_crop_drag)
canvas_start_source.pack()
create_label(start_frame, "開始位置 / クロップ後", 10, "#bdbdbd").pack(anchor="w", pady=(8, 5))
canvas_start_crop = PreviewCanvas(start_frame, CROP_PREVIEW_BOX, "クロップ後プレビュー")
canvas_start_crop.pack()

create_label(end_frame, "終了位置プレビュー", 11, "#dddddd").pack(anchor="w", pady=(0, 5))
canvas_end_source = PreviewCanvas(end_frame, PREVIEW_BOX, "終了位置プレビュー")
canvas_end_source.pack()
create_label(end_frame, "終了位置 / クロップ後", 10, "#bdbdbd").pack(anchor="w", pady=(8, 5))
canvas_end_crop = PreviewCanvas(end_frame, CROP_PREVIEW_BOX, "クロップ後プレビュー")
canvas_end_crop.pack()

frame_inputs = tk.Frame(root, bg="#2d2d2d")
frame_inputs.pack(pady=(8, 6))

entry_start = create_input(frame_inputs, 0, 0, "開始 (-ss):", "00:00:01.200")
entry_end = create_input(frame_inputs, 1, 0, "終了:", "00:00:03.800")
entry_start.bind("<Return>", fetch_previews)
entry_end.bind("<Return>", fetch_previews)
entry_start.bind("<FocusOut>", fetch_previews)
entry_end.bind("<FocusOut>", fetch_previews)

btn_update_prev = tk.Button(
    frame_inputs,
    text="時間プレビュー更新",
    command=fetch_previews,
    bg="#555",
    fg="white",
    activebackground="#666",
    activeforeground="white",
    font=("Meiryo", 9),
)
btn_update_prev.grid(row=0, column=2, rowspan=2, padx=12, sticky="ns")

entry_x1 = create_input(frame_inputs, 0, 3, "X1:", "100", draw_previews)
entry_x2 = create_input(frame_inputs, 0, 5, "X2:", "800", draw_previews)
entry_y1 = create_input(frame_inputs, 1, 3, "Y1:", "100", draw_previews)
entry_y2 = create_input(frame_inputs, 1, 5, "Y2:", "500", draw_previews)

frame_opt = tk.Frame(root, bg="#2d2d2d")
frame_opt.pack(pady=4)
entry_h = create_input(frame_opt, 0, 0, "高さ (-2:H):", "720")
entry_fps = create_input(frame_opt, 0, 2, "fps:", "10")
entry_crf = create_input(frame_opt, 0, 4, "画質 (CRF):", "28")

btn_run = tk.Button(
    root,
    text="変換を実行",
    command=process_video,
    bg="#4CAF50",
    fg="white",
    activebackground="#5abf5e",
    activeforeground="white",
    font=("Meiryo", 16, "bold"),
    width=24,
    height=2,
)
btn_run.pack(pady=10)

lbl_status = tk.Label(root, text="待機中", bg="#2d2d2d", fg="#bdbdbd", font=("Meiryo", 10))
lbl_status.pack()

root.bind("<Escape>", cancel_crop_drag)
enable_window_drop(root)
root.mainloop()
