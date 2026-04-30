"""
main_system.py
Pi Zero 2 W 版：非阻塞通知、按鍵防彈跳、完整資源釋放。
"""

import atexit
import os
import queue
import signal
import subprocess
import threading
import time

import cv2
import requests
import RPi.GPIO as GPIO
from dotenv import load_dotenv
from picamera2 import Picamera2

from detection_engine import DetectionEngine
from hardware_control import HardwareControl

load_dotenv()

# GPIO (BCM)
IR_LED_GPIO = 17
BUZZER_GPIO = 27
RED_LED_GPIO = 22
SENSITIVITY_BTN_GPIO = 4  # 依焊接圖配置：按鍵接 GPIO4

# Runtime
CAMERA_RESOLUTION = (416, 320)
SAVE_DIR = "./alerts"
DETECTION_THRESHOLD = 5
ALERT_COOLDOWN = 12.0
BTN_BOUNCETIME_MS = 140
DOUBLE_CLICK_WINDOW = 0.35
MAIN_LOOP_SLEEP = 0.04
TRACK_MAX_CENTER_DIST = 22.0
TRACK_MAX_RADIUS_DELTA_RATIO = 0.45

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TIMEOUT_SEC = 8
TELEGRAM_QUEUE_SIZE = 6


class TelegramWorker:
    def __init__(self, token: str, chat_id: str, hw: HardwareControl):
        self.token = token
        self.chat_id = chat_id
        self.hw = hw
        self.enabled = bool(token and chat_id)
        self._queue = queue.Queue(maxsize=TELEGRAM_QUEUE_SIZE)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.enabled:
            print("[Telegram] 未設定 token/chat_id，停用推送")
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Telegram] 背景推送執行緒啟動")

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def enqueue_photo(self, image_path: str):
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(image_path)
        except queue.Full:
            print("[Telegram] 佇列已滿，丟棄最舊工作")
            try:
                old = self._queue.get_nowait()
                if old != image_path and os.path.exists(old):
                    pass
            except Exception:
                pass
            try:
                self._queue.put_nowait(image_path)
            except Exception:
                pass

    def _send_photo(self, image_path: str) -> bool:
        if not os.path.exists(image_path):
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        try:
            with open(image_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": self.chat_id, "caption": "偵測到可疑鏡頭"},
                    files={"photo": f},
                    timeout=TELEGRAM_TIMEOUT_SEC,
                )
            if resp.ok:
                try:
                    os.remove(image_path)
                except Exception:
                    pass
                return True
            print(f"[Telegram] API 失敗：{resp.status_code}")
            return False
        except requests.RequestException as exc:
            print(f"[Telegram] 網路錯誤：{exc}")
            return False
        except Exception as exc:
            print(f"[Telegram] 發送例外：{exc}")
            return False

    def _loop(self):
        while not self._stop.is_set():
            try:
                image_path = self._queue.get(timeout=0.4)
            except queue.Empty:
                continue
            ok = self._send_photo(image_path)
            if not ok:
                self.hw.error_beep()
            self._queue.task_done()


class HiddenCamDetectorSystem:
    def __init__(self):
        self.hw = HardwareControl(IR_LED_GPIO, BUZZER_GPIO, RED_LED_GPIO)
        self.engine = DetectionEngine(model_path=None)
        self.picam2 = None
        self.running = False
        self._shutdown_done = False
        self._poweroff_requested = False

        self.detection_count = 0
        self.last_alert_time = 0.0
        self.sensitivity_level = 1
        self._tracked_lens = None  # (cx, cy, r)
        self._tracked_hits = 0

        self.state_lock = threading.RLock()
        self.shutdown_requested = threading.Event()
        self.last_btn_ts = 0.0
        self.click_count = 0

        self.telegram = TelegramWorker(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, self.hw)

        os.makedirs(SAVE_DIR, exist_ok=True)
        self._setup_button()
        self._register_exit_hooks()

    def _setup_button(self):
        GPIO.setup(SENSITIVITY_BTN_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        # 某些環境下 edge detect 狀態會殘留，先清理避免 RuntimeError
        try:
            GPIO.remove_event_detect(SENSITIVITY_BTN_GPIO)
        except Exception:
            pass

        last_exc = None
        for _ in range(3):
            try:
                GPIO.add_event_detect(
                    SENSITIVITY_BTN_GPIO,
                    GPIO.FALLING,
                    callback=self._on_button_edge,
                    bouncetime=BTN_BOUNCETIME_MS,
                )
                return
            except RuntimeError as exc:
                last_exc = exc
                time.sleep(0.15)

        # 不讓服務整體崩潰：按鍵失效時仍維持偵測主流程
        print(f"[System] 警告：按鍵事件初始化失敗，已停用按鍵功能 ({last_exc})")

    def _on_button_edge(self, _channel):
        now = time.monotonic()
        with self.state_lock:
            if now - self.last_btn_ts <= DOUBLE_CLICK_WINDOW:
                self.click_count += 1
            else:
                self.click_count = 1
            self.last_btn_ts = now

        threading.Thread(target=self._resolve_button_click, daemon=True).start()

    def _resolve_button_click(self):
        time.sleep(DOUBLE_CLICK_WINDOW)
        with self.state_lock:
            if time.monotonic() - self.last_btn_ts < DOUBLE_CLICK_WINDOW:
                return
            count = self.click_count
            self.click_count = 0

        if count >= 2:
            print("[System] 按鍵雙擊：關機")
            self._poweroff_requested = True
            self.shutdown_requested.set()
            self.running = False
            return
        self._switch_sensitivity()

    def _switch_sensitivity(self):
        with self.state_lock:
            self.sensitivity_level = (self.sensitivity_level + 1) % 3
            level = self.sensitivity_level
        self.engine.set_sensitivity(level)
        self.hw.flash_red_led(level + 1)
        self.hw.mode_beep()

    def _register_exit_hooks(self):
        atexit.register(self._safe_shutdown)
        signal.signal(signal.SIGINT, lambda *_: self._safe_shutdown())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: self._safe_shutdown())

    def _safe_shutdown(self):
        try:
            self._shutdown()
        except Exception as exc:
            print(f"[System] shutdown 例外：{exc}")

    def _init_camera(self) -> bool:
        try:
            self.picam2 = Picamera2()
            cfg = self.picam2.create_video_configuration(
                main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
            )
            self.picam2.configure(cfg)
            self.picam2.start()
            time.sleep(1.5)
            self.hw.turn_on_ir_led()
            print("[System] 相機初始化成功")
            return True
        except Exception as exc:
            print(f"[System] 相機初始化失敗：{exc}")
            self.hw.error_beep()
            return False

    def _startup_feedback(self):
        # 開機成功回饋：紅燈閃 3 次 + 蜂鳴器兩聲
        self.hw.flash_red_led(3, on_ms=120, off_ms=120)
        self.hw.beep(0.09, 2100)
        time.sleep(0.08)
        self.hw.beep(0.09, 2100)

    def _capture_loop(self):
        while self.running and self.picam2 is not None:
            try:
                self.hw.turn_off_ir_led()
                time.sleep(0.05)
                frame_off_rgb = self.picam2.capture_array()
                frame_off_bgr = cv2.cvtColor(frame_off_rgb, cv2.COLOR_RGB2BGR)

                self.hw.turn_on_ir_led()
                time.sleep(0.05)
                frame_on_rgb = self.picam2.capture_array()
                frame_on_bgr = cv2.cvtColor(frame_on_rgb, cv2.COLOR_RGB2BGR)

                frame_diff = cv2.absdiff(frame_on_bgr, frame_off_bgr)
                self.engine.add_frame_to_buffer(frame_diff)
                time.sleep(0.03)
            except Exception as exc:
                print(f"[System] 相機擷取失敗：{exc}")
                self.hw.error_beep()
                self.running = False
                break

    def _trigger_alert(self, frame, lenses: list):
        annotated = frame.copy()
        for cx, cy, r in lenses:
            cv2.circle(annotated, (cx, cy), r, (0, 255, 0), 2)
            cv2.circle(annotated, (cx, cy), 3, (0, 0, 255), -1)
        cv2.putText(
            annotated,
            f"DETECTED: {len(lenses)}",
            (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
        )
        path = os.path.join(SAVE_DIR, f"alert_{int(time.time())}.jpg")
        ok = cv2.imwrite(path, annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            print("[System] 截圖寫檔失敗")
            self.hw.error_beep()
            return

        self.hw.turn_on_red_led()
        self.hw.alert_beep()
        self.telegram.enqueue_photo(path)

        time.sleep(0.25)
        self.hw.turn_off_red_led()

    def _pick_candidate(self, lenses: list):
        if not lenses:
            return None
        # detection_engine 目前已偏向回傳最佳候選；這裡仍保底挑最大半徑。
        return max(lenses, key=lambda x: x[2])

    def _update_temporal_vote(self, lenses: list) -> int:
        candidate = self._pick_candidate(lenses)
        if candidate is None:
            self._tracked_lens = None
            self._tracked_hits = 0
            return 0

        cx, cy, r = candidate
        if self._tracked_lens is None:
            self._tracked_lens = candidate
            self._tracked_hits = 1
            return self._tracked_hits

        pcx, pcy, pr = self._tracked_lens
        dist = ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5
        r_base = max(float(pr), 1.0)
        r_delta_ratio = abs(r - pr) / r_base

        if dist <= TRACK_MAX_CENTER_DIST and r_delta_ratio <= TRACK_MAX_RADIUS_DELTA_RATIO:
            self._tracked_hits += 1
        else:
            self._tracked_lens = candidate
            self._tracked_hits = 1

        self._tracked_lens = candidate
        return self._tracked_hits

    def _shutdown(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.running = False
        self.shutdown_requested.set()

        try:
            GPIO.remove_event_detect(SENSITIVITY_BTN_GPIO)
        except Exception:
            pass

        try:
            self.engine.stop_detection_thread()
        except Exception:
            pass

        self.telegram.stop()

        if self.picam2:
            try:
                self.picam2.stop()
            except Exception:
                pass
            try:
                self.picam2.close()
            except Exception:
                pass
            self.picam2 = None

        try:
            self.hw.turn_off_ir_led()
            self.hw.turn_off_red_led()
        except Exception:
            pass
        self.hw.cleanup()
        print("[System] 資源已釋放")

    def _execute_poweroff(self):
        if not self._poweroff_requested:
            return
        for cmd in (["sudo", "-n", "shutdown", "-h", "now"], ["shutdown", "-h", "now"]):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if proc.returncode == 0:
                    print("[System] 已送出關機指令")
                    return
            except Exception:
                pass
        print("[System] 無法送出關機指令")

    def run(self):
        self.running = True
        self.telegram.start()
        try:
            if not self._init_camera():
                return

            self.engine.start_detection_thread()
            threading.Thread(target=self._capture_loop, daemon=True).start()
            self._startup_feedback()

            while self.running and not self.shutdown_requested.is_set():
                try:
                    result = self.engine.get_latest_detection_result()
                    if result is None:
                        time.sleep(MAIN_LOOP_SLEEP)
                        continue
                    frame, lenses = result

                    self.detection_count = self._update_temporal_vote(lenses)

                    now = time.time()
                    if (
                        self.detection_count >= DETECTION_THRESHOLD
                        and (now - self.last_alert_time) > ALERT_COOLDOWN
                    ):
                        alert_lenses = [self._tracked_lens] if self._tracked_lens else lenses
                        self._trigger_alert(frame, alert_lenses)
                        self.last_alert_time = now
                        self.detection_count = 0
                        self._tracked_lens = None
                        self._tracked_hits = 0

                    time.sleep(MAIN_LOOP_SLEEP)
                except Exception as exc:
                    print(f"[System] 主迴圈例外：{exc}")
                    self.hw.error_beep()
                    time.sleep(0.1)
        except KeyboardInterrupt:
            print("[System] 使用者中止")
        finally:
            self._shutdown()
            self._execute_poweroff()


if __name__ == "__main__":
    HiddenCamDetectorSystem().run()
