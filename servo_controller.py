import config

# Power Independence Design Note:
# The software logic controls the SG90/MG90S servo using a standard PWM signal
# sent through the GPIO pin. This implementation is completely agnostic of how
# the servo is powered (whether from the Raspberry Pi 5V pin or an external
# regulated 5V power supply sharing a common ground). No software modifications
# are required when changing the power source configuration.

# Pulse widths in microseconds for SG90/MG90S:
#   500us  = 0 degrees  (CLOSED)
#   1500us = 90 degrees (OPEN)
#   2500us = 180 degrees (full range)
PULSE_CLOSED = 500   # microseconds — 0 degrees
PULSE_OPEN   = 1500  # microseconds — 90 degrees

try:
    import pigpio
    PIGPIO_AVAILABLE = True
except ImportError:
    PIGPIO_AVAILABLE = False


class ServoController:
    """
    Controls a physical servo motor directly via pigpio's DMA-based hardware PWM.
    Uses an internal state guard to prevent duplicate commands.
    Gracefully degrades to simulation mode if pigpio is unavailable.
    """

    def __init__(self, pin=18, pulse_closed=PULSE_CLOSED, pulse_open=PULSE_OPEN):
        self.pin = pin
        self.pulse_closed = pulse_closed
        self.pulse_open = pulse_open

        self.is_open = False  # Internal state guard — prevents duplicate commands
        self.pi = None

        if PIGPIO_AVAILABLE:
            try:
                self.pi = pigpio.pi()
                if not self.pi.connected:
                    print("[SERVO WARNING] pigpiod daemon is not running. Falling back to SIMULATION MODE.")
                    print("[SERVO INFO] Start it with: sudo pigpiod")
                    self.pi = None
                else:
                    # Start in LOCKED position
                    self.pi.set_servo_pulsewidth(self.pin, self.pulse_closed)
                    print(f"[SERVO] Initialized on GPIO Pin {self.pin} via pigpio (LOCKED / 0 deg)")
            except Exception as e:
                print(f"[SERVO ERROR] Failed to connect to pigpio: {e}")
                print("[SERVO INFO] Falling back to SIMULATION MODE.")
                self.pi = None
        else:
            print("[SERVO WARNING] pigpio not installed. Running in SIMULATION MODE.")
            print("[SERVO INFO] Install with: pip install pigpio  and start daemon: sudo pigpiod")

    def is_hardware_active(self):
        """Returns True if hardware servo control is active, False if in simulation."""
        return self.pi is not None

    def open_lock(self):
        """
        Rotates servo to open position (90 degrees / 1500us).
        No-op if already open (duplicate command guard).
        """
        if self.is_open:
            return False  # Skip duplicate command

        self.is_open = True
        print("[SERVO] State Transition: LOCK -> OPEN (90 degrees)")
        if self.pi:
            try:
                self.pi.set_servo_pulsewidth(self.pin, self.pulse_open)
            except Exception as e:
                print(f"[SERVO ERROR] Failed to open lock: {e}")
        return True

    def close_lock(self):
        """
        Rotates servo to closed/locked position (0 degrees / 500us).
        No-op if already closed (duplicate command guard).
        """
        if not self.is_open:
            return False  # Skip duplicate command

        self.is_open = False
        print("[SERVO] State Transition: LOCK -> CLOSED (0 degrees)")
        if self.pi:
            try:
                self.pi.set_servo_pulsewidth(self.pin, self.pulse_closed)
            except Exception as e:
                print(f"[SERVO ERROR] Failed to close lock: {e}")
        return True

    def cleanup(self):
        """
        Emergency fail-safe: guarantees servo returns to CLOSED before releasing GPIO.
        Called on any exit (normal, Ctrl+C, crash, etc.)
        """
        print("[SERVO] Releasing GPIO resources...")

        # Always ensure we're locked before releasing
        if self.is_open:
            self.is_open = False  # Force override the guard for cleanup
            if self.pi:
                try:
                    self.pi.set_servo_pulsewidth(self.pin, self.pulse_closed)
                    import time
                    time.sleep(0.5)  # Give servo time to physically reach closed position
                except Exception:
                    pass

        if self.pi:
            try:
                self.pi.set_servo_pulsewidth(self.pin, 0)  # Stop sending pulses
                self.pi.stop()
                print("[SERVO] pigpio connection closed and GPIO pin released.")
            except Exception as e:
                print(f"[SERVO ERROR] Failed to release pigpio: {e}")
            finally:
                self.pi = None
