"""
poe2_ring_capture.py — OBS Python Script
エグザイルの盗みの指輪UIをキャプチャして透明PNGで保存するOBSスクリプト

【必要環境】
- Windows 10/11
- OBS Studio 28以降
- Python 3.10 (OBSのスクリプト用Python)
- pywin32: pip install pywin32

【OBSセットアップ】
1. OBS → ツール → スクリプト → Pythonの設定 → Python 3.10のパスを指定
2. OBS → ツール → スクリプト → "+" でこのファイルを追加
3. スクリプト設定で解像度・座標・パスを入力
4. OBS → 設定 → ホットキー → 「指輪キャプチャ」にキーを割り当て
5. シーンにイメージソースを追加
   - ファイル: 設定した出力PNGパス
   - 「ファイルが変更された時に再読み込みする」にチェック
"""

import obspython as obs
import threading
import time
import ctypes
import ctypes.wintypes
import struct
import zlib
import re
import os

# ─────────────────────────────────────────
# 解像度別プリセット座標
# (ring_x, ring_y, cap_left, cap_top, cap_right, cap_bottom)
# ─────────────────────────────────────────
RESOLUTION_PRESETS = {
    "2560x1440 (WQHD)": (2008, 355, 1763, 212, 2270, 330),
    "1920x1080 (FHD)":  (1506, 266, 1322, 159, 1702, 247),
    "3840x2160 (4K)":   (3016, 710, 2526, 424, 3240, 660),
    "カスタム":          (0,    0,   0,    0,   0,    0),
}

# ─────────────────────────────────────────
# 設定値
# ─────────────────────────────────────────
resolution     = "2560x1440 (WQHD)"
ring_x         = 2008
ring_y         = 355
cap_left       = 1763
cap_top        = 212
cap_right      = 2270
cap_bottom     = 330
dark_thresh    = 80
allowed_scene  = "POE"
output_path    = ""
auto_capture   = False
log_path       = ""
scene_delay    = 0

COOLDOWN_SEC   = 2.0
key_delay      = 0.3   # K押してから画面が開くまで(秒)
hover_delay    = 0.4   # カーソル移動してからUI表示まで(秒)
AUTO_COOLDOWN  = 20

_cooldown      = 0
_processing    = False
_hotkey_id     = obs.OBS_INVALID_HOTKEY_ID
_last_auto_cap = 0
_last_pos      = 0
_watching      = False
_watch_thread  = None

# ─────────────────────────────────────────
# OBS UI
# ─────────────────────────────────────────
def script_description():
    return (
        "<b>POE2 盗みの指輪キャプチャ</b><br>"
        "エグザイルの盗みの指輪UIをOBSオーバーレイとして表示します。<br><br>"
        "ホットキー押下またはエリア切り替え時に自動でキャプチャします。<br>"
        "OBS → 設定 → ホットキー → 「指輪キャプチャ」にキーを割り当ててください。"
    )

def script_properties():
    props = obs.obs_properties_create()

    res_list = obs.obs_properties_add_list(
        props, "resolution", "解像度プリセット",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    for name in RESOLUTION_PRESETS:
        obs.obs_property_list_add_string(res_list, name, name)
    obs.obs_property_set_modified_callback(res_list, on_resolution_changed)

    obs.obs_properties_add_int(props, "ring_x",     "指輪アイコン X座標", 0, 7680, 1)
    obs.obs_properties_add_int(props, "ring_y",     "指輪アイコン Y座標", 0, 4320, 1)
    obs.obs_properties_add_int(props, "cap_left",   "キャプチャ Left",    0, 7680, 1)
    obs.obs_properties_add_int(props, "cap_top",    "キャプチャ Top",     0, 4320, 1)
    obs.obs_properties_add_int(props, "cap_right",  "キャプチャ Right",   0, 7680, 1)
    obs.obs_properties_add_int(props, "cap_bottom", "キャプチャ Bottom",  0, 4320, 1)
    obs.obs_properties_add_int(props, "dark_thresh","透明化しきい値 (R+G+B)", 0, 765, 1)
    obs.obs_properties_add_text(props, "allowed_scene", "動作するシーン名 (空欄=常時)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_path(props, "output_path", "出力PNG パス",
                                obs.OBS_PATH_FILE_SAVE, "*.png", "")
    obs.obs_properties_add_int(props, "key_delay",   "Kキー後の待機(ms)",        0, 2000, 10)
    obs.obs_properties_add_int(props, "hover_delay", "カーソル移動後の待機(ms)",  0, 2000, 10)
    obs.obs_properties_add_bool(props, "auto_capture", "エリア切り替え時に自動キャプチャ")
    obs.obs_properties_add_path(props, "log_path", "Client.txt パス",
                                obs.OBS_PATH_FILE, "*.txt", "")
    obs.obs_properties_add_int(props, "scene_delay", "切り替え後の待機秒数", 0, 60, 1)
    return props

def on_resolution_changed(props, prop, settings):
    res = obs.obs_data_get_string(settings, "resolution")
    if res in RESOLUTION_PRESETS and res != "カスタム":
        rx, ry, cl, ct, cr, cb = RESOLUTION_PRESETS[res]
        obs.obs_data_set_int(settings, "ring_x",    rx)
        obs.obs_data_set_int(settings, "ring_y",    ry)
        obs.obs_data_set_int(settings, "cap_left",  cl)
        obs.obs_data_set_int(settings, "cap_top",   ct)
        obs.obs_data_set_int(settings, "cap_right", cr)
        obs.obs_data_set_int(settings, "cap_bottom",cb)
    return True

def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "resolution",    "2560x1440 (WQHD)")
    obs.obs_data_set_default_int(settings,    "ring_x",        2008)
    obs.obs_data_set_default_int(settings,    "ring_y",        355)
    obs.obs_data_set_default_int(settings,    "cap_left",      1763)
    obs.obs_data_set_default_int(settings,    "cap_top",       212)
    obs.obs_data_set_default_int(settings,    "cap_right",     2270)
    obs.obs_data_set_default_int(settings,    "cap_bottom",    330)
    obs.obs_data_set_default_int(settings,    "dark_thresh",   80)
    obs.obs_data_set_default_string(settings, "allowed_scene", "POE")
    obs.obs_data_set_default_int(settings,    "key_delay",     300)
    obs.obs_data_set_default_int(settings,    "hover_delay",   400)
    obs.obs_data_set_default_bool(settings,   "auto_capture",  False)
    obs.obs_data_set_default_int(settings,    "scene_delay",   0)

def script_update(settings):
    global resolution, ring_x, ring_y, cap_left, cap_top, cap_right, cap_bottom
    global dark_thresh, allowed_scene, output_path, auto_capture, log_path, scene_delay
    global key_delay, hover_delay
    resolution   = obs.obs_data_get_string(settings, "resolution")
    ring_x       = obs.obs_data_get_int(settings,    "ring_x")
    ring_y       = obs.obs_data_get_int(settings,    "ring_y")
    cap_left     = obs.obs_data_get_int(settings,    "cap_left")
    cap_top      = obs.obs_data_get_int(settings,    "cap_top")
    cap_right    = obs.obs_data_get_int(settings,    "cap_right")
    cap_bottom   = obs.obs_data_get_int(settings,    "cap_bottom")
    dark_thresh  = obs.obs_data_get_int(settings,    "dark_thresh")
    sc = obs.obs_data_get_string(settings, "allowed_scene").strip()
    if sc: allowed_scene = sc
    output_path  = obs.obs_data_get_string(settings, "output_path").strip()
    key_delay    = obs.obs_data_get_int(settings,    "key_delay")   / 1000.0
    hover_delay  = obs.obs_data_get_int(settings,    "hover_delay") / 1000.0
    auto_capture = obs.obs_data_get_bool(settings,   "auto_capture")
    log_path     = obs.obs_data_get_string(settings, "log_path").strip()
    scene_delay  = obs.obs_data_get_int(settings,    "scene_delay")
    if auto_capture:
        restart_watcher()
    else:
        stop_watcher()

def script_load(settings):
    global _hotkey_id
    _hotkey_id = obs.obs_hotkey_register_frontend(
        "ring_capture", "指輪キャプチャ", on_hotkey
    )
    hotkey_save = obs.obs_data_get_array(settings, "ring_capture_hotkey")
    obs.obs_hotkey_load(_hotkey_id, hotkey_save)
    obs.obs_data_array_release(hotkey_save)
    obs.script_log(obs.LOG_INFO, "[RingCapture] 読み込み完了")

def script_save(settings):
    hotkey_save = obs.obs_hotkey_save(_hotkey_id)
    obs.obs_data_set_array(settings, "ring_capture_hotkey", hotkey_save)
    obs.obs_data_array_release(hotkey_save)

def script_unload():
    stop_watcher()
    obs.script_log(obs.LOG_INFO, "[RingCapture] 停止")

# ─────────────────────────────────────────
# ホットキー
# ─────────────────────────────────────────
def on_hotkey(pressed):
    if pressed:
        threading.Thread(target=process_capture, daemon=True).start()

# ─────────────────────────────────────────
# シーン判定
# ─────────────────────────────────────────
def is_allowed_scene():
    if not allowed_scene:
        return True
    scene = obs.obs_frontend_get_current_scene()
    if scene is None:
        return False
    name = obs.obs_source_get_name(scene)
    obs.obs_source_release(scene)
    return name == allowed_scene

# ─────────────────────────────────────────
# マウス・キー操作
# ─────────────────────────────────────────
def get_cursor_pos():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def move_mouse(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)

def send_key(vk, down=True):
    ctypes.windll.user32.keybd_event(vk, 0, 0 if down else 0x0002, 0)

VK_K = 0x4B

# ─────────────────────────────────────────
# キャプチャ・PNG保存
# ─────────────────────────────────────────
def capture_region():
    import win32gui, win32ui, win32con
    w = cap_right  - cap_left
    h = cap_bottom - cap_top
    hdesktop   = win32gui.GetDesktopWindow()
    hdesktopdc = win32gui.GetWindowDC(hdesktop)
    hdc    = win32ui.CreateDCFromHandle(hdesktopdc)
    memdc  = hdc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(hdc, w, h)
    memdc.SelectObject(bitmap)
    memdc.BitBlt((0,0), (w,h), hdc, (cap_left, cap_top), win32con.SRCCOPY)
    bmpstr = bitmap.GetBitmapBits(True)
    win32gui.DeleteObject(bitmap.GetHandle())
    memdc.DeleteDC()
    hdc.DeleteDC()
    win32gui.ReleaseDC(hdesktop, hdesktopdc)
    return w, h, bmpstr

def save_png(w, h, rgba_bytes, path):
    def chunk(name, data):
        c = name + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgba_bytes[y*w*4:(y+1)*w*4])
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(bytes(raw), 9)))
        f.write(chunk(b'IEND', b''))

# ─────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────
def process_capture():
    global _processing, _cooldown
    now = time.time()
    if _processing or now < _cooldown:
        return
    if not is_allowed_scene():
        obs.script_log(obs.LOG_INFO, "[RingCapture] シーンが違うためスキップ")
        return
    if not output_path:
        obs.script_log(obs.LOG_WARNING, "[RingCapture] 出力パスが未設定")
        return

    _processing = True
    _cooldown   = now + COOLDOWN_SEC

    try:
        orig_x, orig_y = get_cursor_pos()

        send_key(VK_K, down=True)
        send_key(VK_K, down=False)
        time.sleep(key_delay)

        move_mouse(ring_x, ring_y)
        time.sleep(hover_delay)

        w, h, bmpstr = capture_region()

        send_key(VK_K, down=True)
        send_key(VK_K, down=False)
        move_mouse(orig_x, orig_y)

        pixels_in  = struct.unpack(f'{w*h*4}B', bmpstr)
        pixels_out = bytearray(w * h * 4)
        for i in range(w * h):
            b = pixels_in[i*4]
            g = pixels_in[i*4+1]
            r = pixels_in[i*4+2]
            a = 0 if (r + g + b) < dark_thresh else 255
            pixels_out[i*4]   = r
            pixels_out[i*4+1] = g
            pixels_out[i*4+2] = b
            pixels_out[i*4+3] = a

        save_png(w, h, bytes(pixels_out), output_path)
        obs.script_log(obs.LOG_INFO, f"[RingCapture] 保存完了: {output_path}")

    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[RingCapture] エラー: {e}")
        import traceback
        obs.script_log(obs.LOG_ERROR, traceback.format_exc())
    finally:
        _processing = False

# ─────────────────────────────────────────
# Client.txt 監視
# ─────────────────────────────────────────
def restart_watcher():
    stop_watcher()
    if log_path and os.path.isfile(log_path):
        start_watcher()
    elif log_path:
        obs.script_log(obs.LOG_WARNING, f"[RingCapture] Client.txt が見つかりません: {log_path}")

def start_watcher():
    global _watching, _watch_thread, _last_pos
    _watching = True
    try:
        _last_pos = os.path.getsize(log_path)
    except OSError:
        _last_pos = 0
    _watch_thread = threading.Thread(target=_watch_loop, daemon=True)
    _watch_thread.start()
    obs.script_log(obs.LOG_INFO, f"[RingCapture] ログ監視開始: {log_path}")

def stop_watcher():
    global _watching
    _watching = False

def _watch_loop():
    global _last_pos
    while _watching:
        try:
            size = os.path.getsize(log_path)
            if size > _last_pos:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(_last_pos)
                    new_text = f.read()
                _last_pos = size
                _check_scene_change(new_text)
        except OSError:
            pass
        time.sleep(1.0)

def _check_scene_change(text):
    matches = re.findall(r'\[SCENE\] Set Source \[(.+?)\]', text)
    for area in matches:
        if area == "(null)":
            continue
        obs.script_log(obs.LOG_INFO,
            f"[RingCapture] エリア切り替え: {area} → {scene_delay}秒後にキャプチャ")
        threading.Thread(target=_delayed_capture, args=(area,), daemon=True).start()

def _delayed_capture(area):
    global _last_auto_cap
    time.sleep(scene_delay)
    now = time.time()
    if now - _last_auto_cap < AUTO_COOLDOWN:
        obs.script_log(obs.LOG_INFO, f"[RingCapture] 重複スキップ: {area}")
        return
    _last_auto_cap = now
    obs.script_log(obs.LOG_INFO, f"[RingCapture] 自動キャプチャ: {area}")
    process_capture()
