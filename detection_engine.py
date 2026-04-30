"""
detection_engine.py
Pi Zero 2 W 友善：低記憶體、低延遲、只保留最新 frame/result。
"""

import threading
import time
from collections import deque

import cv2
import numpy as np


class DetectionEngine:
    SENSITIVITY_PARAMS = {
        0: 20,  # 高敏
        1: 30,  # 中敏（預設）
        2: 42,  # 低敏
    }

    def __init__(
        self,
        model_path=None,
        model_type="opencv",
        conf_threshold=0.7,
        hough_param2=30,
        min_brightness_ratio=1.25,
        min_brightness_abs=105,
    ):
        self.model_path = model_path
        self.model_type = model_type
        self.conf_threshold = conf_threshold
        self.hough_param2 = hough_param2
        self.min_brightness_ratio = min_brightness_ratio
        self.min_brightness_abs = min_brightness_abs

        # 改為 lazy load：避免 service 啟動時因 YOLO/torch 載入太慢被 systemd timeout 殺掉。
        self.yolo_model = None
        self._yolo_init_attempted = False
        self._yolo_enabled = bool(model_path)
        if self._yolo_enabled:
            print("[YOLO] lazy load enabled (will initialize on first candidate)")
        else:
            print("[YOLO] model NOT loaded")

        self.frame_buffer = deque(maxlen=1)
        self.detection_results = deque(maxlen=1)
        self.lock = threading.Lock()
        self.running = False
        self.detection_thread = None

    def set_sensitivity(self, level: int):
        if level in self.SENSITIVITY_PARAMS:
            self.hough_param2 = self.SENSITIVITY_PARAMS[level]

    def _ensure_yolo_loaded(self):
        if not self._yolo_enabled:
            return
        if self.yolo_model is not None or self._yolo_init_attempted:
            return
        self._yolo_init_attempted = True
        try:
            from ultralytics import YOLO

            self.yolo_model = YOLO(self.model_path)
            print(f"[YOLO] model loaded, classes: {self.yolo_model.names}")
        except ModuleNotFoundError as e:
            self._yolo_enabled = False
            print(f"[YOLO] ultralytics 未安裝，跳過 YOLO 驗證：{e}")
        except Exception as e:
            self._yolo_enabled = False
            print(f"[YOLO] YOLO 初始化失敗，跳過 YOLO 驗證：{e}")

    def detect_lenses(self, frame_bgr: np.ndarray) -> list:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        frame_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        # IR 環境：先做 CLAHE 拉開對比度，讓反光點更突出
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        blurred = cv2.medianBlur(gray, 5)
        min_side = min(gray.shape[:2])

        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(12, int(min_side * 0.04)),
            param1=80,
            param2=self.hough_param2,
            minRadius=8,
            maxRadius=max(60, int(min_side * 0.20)),
        )
        if circles is None:
            return []

        circles = np.uint16(np.around(circles))
        # 某些 OpenCV 回傳格式可能異常（空陣列或維度不符），直接視為無偵測
        if circles.ndim < 3 or circles.shape[0] == 0 or circles.shape[2] < 3:
            return []
        if circles.shape[1] == 0:
            return []

        h, w = gray.shape
        global_mean = float(np.mean(gray))
        scored_results = []

        for cx, cy, r in circles[0]:
            cx, cy, r = int(cx), int(cy), int(r)
            y0 = max(0, cy - r)
            y1 = min(h, cy + r)
            x0 = max(0, cx - r)
            x1 = min(w, cx + r)
            roi_gray = gray[y0:y1, x0:x1]
            if roi_gray.size == 0:
                print(
                    f"[DEBUG] circle at ({cx}, {cy}) r={r} | "
                    f"roi_max=nan | roi_mean=nan | FAIL"
                )
                continue
            roi_mean = float(np.mean(roi_gray))

            # 額外過濾：要求 ROI 的最亮點夠亮，避免整體亮的區域誤判
            roi_max = float(np.max(roi_gray))
            # 鏡頭通常是「中心較暗、外圈較亮」；LED 反光點通常相反。
            # 用中心/環狀亮度差把假陽性（紅點、焊點反光）砍掉。
            roi_h, roi_w = roi_gray.shape
            local_cx = cx - x0
            local_cy = cy - y0
            yy, xx = np.ogrid[:roi_h, :roi_w]
            dist2 = (xx - local_cx) ** 2 + (yy - local_cy) ** 2
            inner_r = max(2, int(r * 0.35))
            ring_r_in = max(inner_r + 1, int(r * 0.45))
            ring_r_out = max(ring_r_in + 1, int(r * 0.90))
            inner_mask = dist2 <= (inner_r * inner_r)
            ring_mask = (dist2 >= (ring_r_in * ring_r_in)) & (
                dist2 <= (ring_r_out * ring_r_out)
            )
            inner_mean = (
                float(np.mean(roi_gray[inner_mask]))
                if np.any(inner_mask)
                else float(roi_mean)
            )
            ring_mean = (
                float(np.mean(roi_gray[ring_mask]))
                if np.any(ring_mask)
                else float(roi_mean)
            )
            margin = 24
            cond_ratio = roi_mean > global_mean * 1.03
            cond_abs = roi_mean > 85
            # 你目前場景的亮點多落在 170 左右，205 會把真目標幾乎全擋掉
            cond_max = roi_max > 145
            cond_sat = roi_max <= 255
            cond_r = 8 <= r <= 50
            cond_lens_shape = (ring_mean - inner_mean) >= 2.5
            cond_x = margin <= cx < (w - margin)
            cond_y = margin <= cy < (h - margin)
            # 紅色 LED/反光點常是「非常亮 + 很小」，直接過濾。
            cond_not_hotspot = not (roi_max >= 250 and r <= 12 and roi_mean < 185.0)
            # IR LED 自反光常是大面積接近飽和，鏡頭反光通常不會整塊爆白。
            sat_ratio = float(np.mean(roi_gray >= 245))
            cond_not_saturated_blob = sat_ratio <= 0.18
            # 太亮的整片反光（通常是 LED 打牆/外殼）直接排除。
            cond_not_overbright = roi_mean <= 150.0
            passed = (
                cond_ratio
                and cond_abs
                and cond_max
                and cond_sat
                and cond_r
                and cond_lens_shape
                and cond_not_hotspot
                and cond_not_saturated_blob
                and cond_not_overbright
                and cond_x
                and cond_y
            )
            fail_reasons = []
            if not cond_ratio:
                fail_reasons.append("ratio")
            if not cond_abs:
                fail_reasons.append("abs")
            if not cond_max:
                fail_reasons.append("max")
            if not cond_sat:
                fail_reasons.append("sat")
            if not cond_r:
                fail_reasons.append("r")
            if not cond_lens_shape:
                fail_reasons.append("shape")
            if not cond_not_hotspot:
                fail_reasons.append("hotspot")
            if not cond_not_saturated_blob:
                fail_reasons.append("satblob")
            if not cond_not_overbright:
                fail_reasons.append("overbright")
            if not cond_x:
                fail_reasons.append("x")
            if not cond_y:
                fail_reasons.append("y")
            print(
                f"[DEBUG] circle at ({cx}, {cy}) r={r} | "
                f"roi_max={int(roi_max)} | roi_mean={roi_mean:.1f} | "
                f"inner={inner_mean:.1f} ring={ring_mean:.1f} sat={sat_ratio:.2f} | "
                f"{'PASS' if passed else 'FAIL:' + ','.join(fail_reasons)}"
            )
            if passed:
                self._ensure_yolo_loaded()
                if self.yolo_model is not None:
                    pad = int(r * 2.0)
                    ry0 = max(0, cy - pad)
                    ry1 = min(h, cy + pad)
                    rx0 = max(0, cx - pad)
                    rx1 = min(w, cx + pad)
                    roi_bgr = frame_bgr[ry0:ry1, rx0:rx1]
                    if roi_bgr.size > 0:
                        roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
                        roi_input = cv2.cvtColor(roi_gray, cv2.COLOR_GRAY2BGR)
                        yolo_results = self.yolo_model.predict(
                            roi_input, conf=0.25, verbose=False
                        )
                        found = False
                        for res in yolo_results:
                            boxes = getattr(res, "boxes", None)
                            if boxes is None or boxes.cls is None:
                                continue
                            for cls in boxes.cls:
                                try:
                                    cls_id = int(cls.item())
                                except Exception:
                                    cls_id = int(cls)
                                if self.yolo_model.names.get(cls_id) == "molka":
                                    found = True
                                    break
                            if found:
                                break
                        if not found:
                            continue
                # 中心優先 + 較大半徑優先，最後只留最像鏡頭的一個。
                center_dx = (cx - (w * 0.5)) / max(1.0, w * 0.5)
                center_dy = (cy - (h * 0.5)) / max(1.0, h * 0.5)
                center_penalty = (center_dx * center_dx + center_dy * center_dy) ** 0.5
                score = (ring_mean - inner_mean) * 2.0 + r * 1.2 - center_penalty * 45.0
                scored_results.append((score, cx, cy, r))

        if not scored_results:
            return []
        scored_results.sort(key=lambda x: x[0], reverse=True)
        _, best_cx, best_cy, best_r = scored_results[0]
        return [(best_cx, best_cy, best_r)]

    def _detection_loop(self):
        while self.running:
            with self.lock:
                frame = self.frame_buffer.pop() if self.frame_buffer else None

            if frame is None:
                time.sleep(0.01)
                continue

            try:
                verified = self.detect_lenses(frame)
            except Exception:
                verified = []
            with self.lock:
                self.detection_results.append((frame, verified))

    def start_detection_thread(self):
        self.running = True
        self.detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.detection_thread.start()

    def stop_detection_thread(self):
        self.running = False
        if self.detection_thread and self.detection_thread.is_alive():
            self.detection_thread.join(timeout=3)

    def add_frame_to_buffer(self, frame: np.ndarray):
        with self.lock:
            self.frame_buffer.append(frame)

    def get_latest_detection_result(self):
        with self.lock:
            if self.detection_results:
                return self.detection_results.pop()
            return None
