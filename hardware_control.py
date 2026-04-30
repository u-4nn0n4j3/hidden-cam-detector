"""
hardware_control.py
GPIO 控制：IR LED / 紅 LED / 無源蜂鳴器（PWM 方波驅動）
"""

import threading
import time

import RPi.GPIO as GPIO

# 無源蜂鳴器：RPi.GPIO 軟體 PWM 過高頻率抖動大，實務上約 100Hz–8kHz 較穩
_BUZZER_FREQ_MIN_HZ = 100
_BUZZER_FREQ_MAX_HZ = 8000
_IR_LED_PWM_HZ = 2000
_IR_LED_DUTY = 35


class HardwareControl:
    def __init__(self, ir_led_pin: int, buzzer_pin: int, red_led_pin: int):
        self.IR_LED_PIN = ir_led_pin
        self.BUZZER_PIN = buzzer_pin
        self.RED_LED_PIN = red_led_pin
        self.lock = threading.Lock()
        self._cleaned = False

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.IR_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.RED_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)

        self.ir_led_pwm = GPIO.PWM(self.IR_LED_PIN, _IR_LED_PWM_HZ)
        self._ir_pwm_running = False

        self.buzzer_pwm = GPIO.PWM(self.BUZZER_PIN, 2000)
        self.buzzer_pwm.start(0)

    def _clamp_buzzer_hz(self, hz: int) -> int:
        return max(_BUZZER_FREQ_MIN_HZ, min(_BUZZER_FREQ_MAX_HZ, int(hz)))

    def turn_on_ir_led(self):
        with self.lock:
            if self._cleaned:
                return
            if not self._ir_pwm_running:
                self.ir_led_pwm.start(0)
                self._ir_pwm_running = True
            self.ir_led_pwm.ChangeFrequency(_IR_LED_PWM_HZ)
            self.ir_led_pwm.ChangeDutyCycle(_IR_LED_DUTY)

    def turn_off_ir_led(self):
        with self.lock:
            if self._cleaned:
                return
            if self._ir_pwm_running:
                try:
                    self.ir_led_pwm.ChangeDutyCycle(0)
                except Exception:
                    pass
                try:
                    self.ir_led_pwm.stop()
                except Exception:
                    pass
                self._ir_pwm_running = False
                try:
                    GPIO.setup(self.IR_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
                except Exception:
                    pass

    def turn_on_red_led(self):
        if self._cleaned:
            return
        GPIO.output(self.RED_LED_PIN, GPIO.HIGH)

    def turn_off_red_led(self):
        if self._cleaned:
            return
        GPIO.output(self.RED_LED_PIN, GPIO.LOW)

    def flash_red_led(self, times: int, on_ms: int = 150, off_ms: int = 150):
        def _flash():
            for _ in range(max(0, int(times))):
                if self._cleaned:
                    break
                try:
                    GPIO.output(self.RED_LED_PIN, GPIO.HIGH)
                    time.sleep(max(0.0, on_ms / 1000.0))
                    if self._cleaned:
                        break
                    GPIO.output(self.RED_LED_PIN, GPIO.LOW)
                    time.sleep(max(0.0, off_ms / 1000.0))
                except Exception:
                    break

        threading.Thread(target=_flash, daemon=True).start()

    def beep(self, duration: float = 0.15, frequency: int = 2000):
        with self.lock:
            if self._cleaned:
                return
            try:
                self.buzzer_pwm.ChangeFrequency(self._clamp_buzzer_hz(frequency))
                self.buzzer_pwm.ChangeDutyCycle(50)
                time.sleep(max(0.0, float(duration)))
            finally:
                try:
                    self.buzzer_pwm.ChangeDutyCycle(0)
                except Exception:
                    pass

    def beep_async(self, duration: float = 0.15, frequency: int = 2000):
        threading.Thread(target=self.beep, args=(duration, frequency), daemon=True).start()

    def alert_beep(self):
        self.beep_async(duration=1.0, frequency=2500)

    def mode_beep(self):
        self.beep_async(duration=0.08, frequency=3200)

    def ready_beep(self):
        def _seq():
            self.beep(0.1, 1900)
            time.sleep(0.05)
            self.beep(0.1, 1900)
            time.sleep(0.05)
            self.beep(0.25, 2400)

        threading.Thread(target=_seq, daemon=True).start()

    def error_beep(self):
        def _seq():
            for _ in range(3):
                self.beep(0.18, 1200)
                time.sleep(0.08)

        threading.Thread(target=_seq, daemon=True).start()

    def cleanup(self):
        with self.lock:
            if self._cleaned:
                return
            self._cleaned = True
            try:
                self.buzzer_pwm.ChangeDutyCycle(0)
            except Exception:
                pass
            try:
                if self._ir_pwm_running:
                    self.ir_led_pwm.ChangeDutyCycle(0)
                    self.ir_led_pwm.stop()
                    self._ir_pwm_running = False
            except Exception:
                pass
            try:
                self.buzzer_pwm.stop()
            except Exception:
                pass
            try:
                GPIO.setup(self.BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
            except Exception:
                pass
            try:
                GPIO.output(self.IR_LED_PIN, GPIO.LOW)
                GPIO.output(self.RED_LED_PIN, GPIO.LOW)
            except Exception:
                pass
            try:
                GPIO.cleanup()
            except Exception:
                pass
