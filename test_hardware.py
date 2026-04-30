"""
test_hardware.py
================
硬體燒機測試：IR LED / 紅 LED / 蜂鳴器 / 按鈕單擊雙擊

執行：
    sudo python3 test_hardware.py
"""

import threading
import time
from collections import deque

import RPi.GPIO as GPIO

# 與 main_system.py 保持一致
IR_LED_PIN = 17
BUZZER_PIN = 27
RED_LED_PIN = 22
BUTTON_PIN = 4

DOUBLE_CLICK_WINDOW = 0.35
BTN_BOUNCETIME_MS = 120


class ButtonBurnInTester:
    def __init__(self):
        self.lock = threading.RLock()
        self.queue = deque(maxlen=16)
        self.last_ts = 0.0
        self.click_count = 0
        self.single_count = 0
        self.double_count = 0
        self.running = True

    def on_edge(self, channel):
        with self.lock:
            self.queue.append(time.monotonic())

    def worker(self, buzzer_pwm):
        while self.running:
            ts = None
            with self.lock:
                if self.queue:
                    ts = self.queue.popleft()

            if ts is None:
                time.sleep(0.01)
                continue

            with self.lock:
                if ts - self.last_ts <= DOUBLE_CLICK_WINDOW:
                    self.click_count += 1
                else:
                    self.click_count = 1
                self.last_ts = ts

            time.sleep(DOUBLE_CLICK_WINDOW)
            with self.lock:
                if time.monotonic() - self.last_ts < DOUBLE_CLICK_WINDOW:
                    continue
                count = self.click_count
                self.click_count = 0

            if count >= 2:
                self.double_count += 1
                print(f"[BTN] 雙擊偵測成功（累計 {self.double_count}）")
                # 雙擊回饋：LED 長亮 + 高頻短鳴
                GPIO.output(RED_LED_PIN, GPIO.HIGH)
                buzzer_pwm.ChangeFrequency(3200)
                buzzer_pwm.ChangeDutyCycle(50)
                time.sleep(0.12)
                buzzer_pwm.ChangeDutyCycle(0)
                GPIO.output(RED_LED_PIN, GPIO.LOW)
            else:
                self.single_count += 1
                print(f"[BTN] 單擊偵測成功（累計 {self.single_count}）")
                # 單擊回饋：LED 短閃 + 低頻短鳴
                GPIO.output(RED_LED_PIN, GPIO.HIGH)
                buzzer_pwm.ChangeFrequency(1800)
                buzzer_pwm.ChangeDutyCycle(50)
                time.sleep(0.08)
                buzzer_pwm.ChangeDutyCycle(0)
                GPIO.output(RED_LED_PIN, GPIO.LOW)


def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(IR_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(RED_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def run_basic_output_tests(buzzer_pwm):
    print("\n[1] IR LED 測試（亮 3 秒）")
    GPIO.output(IR_LED_PIN, GPIO.HIGH)
    time.sleep(3)
    GPIO.output(IR_LED_PIN, GPIO.LOW)
    print("    ✓ IR LED 正常")

    print("\n[2] 紅 LED 測試（閃 5 下）")
    for _ in range(5):
        GPIO.output(RED_LED_PIN, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(RED_LED_PIN, GPIO.LOW)
        time.sleep(0.2)
    print("    ✓ 紅 LED 正常")

    print("\n[3] 蜂鳴器測試")
    for freq, dur, label in [(1000, 0.25, "低頻"), (2500, 0.25, "中頻"), (3200, 0.25, "高頻")]:
        print(f"    → {label} {freq}Hz")
        buzzer_pwm.ChangeFrequency(freq)
        buzzer_pwm.ChangeDutyCycle(50)
        time.sleep(dur)
        buzzer_pwm.ChangeDutyCycle(0)
        time.sleep(0.15)
    print("    ✓ 蜂鳴器正常")


def run_button_burnin_test(buzzer_pwm):
    print("\n[4] 按鈕燒機測試（單擊/雙擊）")
    print("    - 單擊按鈕：應顯示『單擊偵測成功』")
    print("    - 連按兩下：應顯示『雙擊偵測成功』")
    print("    - 建議連續測試 30 秒")

    tester = ButtonBurnInTester()
    GPIO.add_event_detect(
        BUTTON_PIN,
        GPIO.FALLING,
        callback=tester.on_edge,
        bouncetime=BTN_BOUNCETIME_MS,
    )

    worker_thread = threading.Thread(target=tester.worker, args=(buzzer_pwm,), daemon=True)
    worker_thread.start()

    t0 = time.time()
    duration = 30
    try:
        while time.time() - t0 < duration:
            remain = duration - int(time.time() - t0)
            with tester.lock:
                s = tester.single_count
                d = tester.double_count
            print(f"    [狀態] 剩餘 {remain:02d}s | 單擊={s} | 雙擊={d}", end="\r", flush=True)
            time.sleep(1)
    finally:
        tester.running = False
        time.sleep(0.1)
        GPIO.remove_event_detect(BUTTON_PIN)
        print()
        print(f"    統計結果：單擊={tester.single_count}，雙擊={tester.double_count}")
        if tester.single_count == 0 and tester.double_count == 0:
            print("    ! 未偵測到任何按鈕事件，請檢查按鈕接線/GND")
        else:
            print("    ✓ 按鈕事件有正常觸發")


def main():
    setup_gpio()
    buzzer_pwm = GPIO.PWM(BUZZER_PIN, 2000)
    buzzer_pwm.start(0)

    print("=" * 48)
    print(" HiddenCam Hardware Burn-in Test")
    print("=" * 48)
    try:
        run_basic_output_tests(buzzer_pwm)
        run_button_burnin_test(buzzer_pwm)
        print("\n✓ 全部測試結束")
    except KeyboardInterrupt:
        print("\n[TEST] 使用者中止")
    finally:
        try:
            buzzer_pwm.ChangeDutyCycle(0)
        except Exception:
            pass
        try:
            buzzer_pwm.stop()
        except Exception:
            pass
        GPIO.output(IR_LED_PIN, GPIO.LOW)
        GPIO.output(RED_LED_PIN, GPIO.LOW)
        GPIO.cleanup()
        print("[TEST] GPIO 已清理")


if __name__ == "__main__":
    main()
"""
test_hardware.py
================
硬體燒機測試腳本。
在接好電路但還沒執行主程式之前，先跑這個確認所有硬體正常。

執行方式：sudo python3 test_hardware.py
"""

import RPi.GPIO as GPIO
import time

# ── 腳位（需與 main_system.py 一致） ──
IR_LED_PIN  = 17   # IR LED（透過 S8050 電晶體）
BUZZER_PIN  = 27   # 被動式蜂鳴器
RED_LED_PIN = 22   # 紅色警示 LED

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(IR_LED_PIN,  GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RED_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(BUZZER_PIN,  GPIO.OUT, initial=GPIO.LOW)

buzzer_pwm = GPIO.PWM(BUZZER_PIN, 2000)
buzzer_pwm.start(0)

print("=" * 40)
print(" 硬體燒機測試")
print("=" * 40)

try:
    # ── 1. IR LED ──
    print("\n[1] IR LED 亮 3 秒")
    print("   → 請用手機相機（前鏡頭）對著 IR LED，應看到紫色亮光")
    GPIO.output(IR_LED_PIN, GPIO.HIGH)
    time.sleep(3)
    GPIO.output(IR_LED_PIN, GPIO.LOW)
    print("   ✓ IR LED 關閉")

    # ── 2. 紅色 LED ──
    print("\n[2] 紅色 LED 閃爍 5 下")
    for i in range(5):
        GPIO.output(RED_LED_PIN, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(RED_LED_PIN, GPIO.LOW)
        time.sleep(0.2)
    print("   ✓ 紅色 LED 測試完成")

    # ── 3. LED 模式指示測試 ──
    print("\n[3] 靈敏度模式 LED 測試（閃 1/2/3 下）")
    for count in [1, 2, 3]:
        print(f"   → 閃 {count} 下（模式 {count}）")
        for _ in range(count):
            GPIO.output(RED_LED_PIN, GPIO.HIGH)
            time.sleep(0.15)
            GPIO.output(RED_LED_PIN, GPIO.LOW)
            time.sleep(0.15)
        time.sleep(0.5)
    print("   ✓ 模式指示測試完成")

    # ── 4. 蜂鳴器 ──
    print("\n[4] 蜂鳴器測試")
    print("   → 提示音（1000Hz 0.3秒）")
    buzzer_pwm.ChangeFrequency(1000)
    buzzer_pwm.ChangeDutyCycle(50)
    time.sleep(0.3)
    buzzer_pwm.ChangeDutyCycle(0)
    time.sleep(0.2)

    print("   → 模式切換音（3000Hz 0.1秒）")
    buzzer_pwm.ChangeFrequency(3000)
    buzzer_pwm.ChangeDutyCycle(50)
    time.sleep(0.1)
    buzzer_pwm.ChangeDutyCycle(0)
    time.sleep(0.2)

    print("   → 警報音（2500Hz 1秒）")
    buzzer_pwm.ChangeFrequency(2500)
    buzzer_pwm.ChangeDutyCycle(50)
    time.sleep(1.0)
    buzzer_pwm.ChangeDutyCycle(0)
    time.sleep(0.2)

    print("   → 開機就緒音（兩短一長）")
    for freq, dur in [(2000, 0.1), (2000, 0.1), (2500, 0.3)]:
        buzzer_pwm.ChangeFrequency(freq)
        buzzer_pwm.ChangeDutyCycle(50)
        time.sleep(dur)
        buzzer_pwm.ChangeDutyCycle(0)
        time.sleep(0.05)
    print("   ✓ 蜂鳴器測試完成")

    # ── 5. 完整警報模擬 ──
    print("\n[5] 完整警報模擬（LED 亮 + 蜂鳴器響）")
    GPIO.output(RED_LED_PIN, GPIO.HIGH)
    buzzer_pwm.ChangeFrequency(2500)
    buzzer_pwm.ChangeDutyCycle(50)
    time.sleep(1.0)
    buzzer_pwm.ChangeDutyCycle(0)
    GPIO.output(RED_LED_PIN, GPIO.LOW)
    print("   ✓ 警報模擬完成")

    print("\n" + "=" * 40)
    print(" 全部測試通過！硬體正常")
    print("=" * 40)
    print("\n 如果有任何項目沒反應：")
    print(" - IR LED：確認電晶體 S8050 接線，Base 接 1kΩ → GPIO 17")
    print(" - 紅色 LED：確認串了 330Ω 電阻，短腳接 GND")
    print(" - 蜂鳴器：確認是被動式，正極接 GPIO 27")

except KeyboardInterrupt:
    print("\n測試中止")
finally:
    buzzer_pwm.stop()
    GPIO.output(IR_LED_PIN,  GPIO.LOW)
    GPIO.output(RED_LED_PIN, GPIO.LOW)
    GPIO.cleanup()
    print("GPIO 已清理")
