import os
import sys

# Power Independence Design Note:
# The software logic controls the SG90/MG90S servo using a standard PWM signal 
# sent through the GPIO pin. This implementation is completely agnostic of how
# the servo is powered (whether from the Raspberry Pi 5V pin or an external 
# regulated 5V power supply sharing a common ground). No software modifications 
# are required when changing the power source configuration.

try:
    from gpiozero import AngularServo
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

class ServoController:
    """
    Maintains lock state and controls physical servo rotation using gpiozero.
    Implements a strict command guard to prevent duplicate PWM commands.
    """
    def __init__(self, pin=18, min_angle=0, max_angle=90, min_pulse=0.5/1000, max_pulse=2.5/1000):
        self.pin = pin
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.min_pulse = min_pulse
        self.max_pulse = max_pulse
        
        self.is_open = False  # Internal state guard to prevent duplicate commands
        self.servo = None
        
        if GPIO_AVAILABLE:
            try:
                # Initialize angular servo
                self.servo = AngularServo(
                    self.pin,
                    initial_angle=self.min_angle,
                    min_angle=self.min_angle,
                    max_angle=self.max_angle,
                    min_pulse_width=self.min_pulse,
                    max_pulse_width=self.max_pulse
                )
                print(f"[SERVO] Initialized AngularServo on GPIO Pin {self.pin} (LOCKED / {self.min_angle} deg)")
            except Exception as e:
                print(f"[SERVO ERROR] Failed to initialize hardware servo: {e}")
                print("[SERVO INFO] Falling back to SIMULATION MODE.")
                self.servo = None
        else:
            print("[SERVO WARNING] gpiozero not available. Running in SIMULATION MODE.")

    def is_hardware_active(self):
        """
        Returns True if hardware servo control is active, False if in simulation.
        """
        return self.servo is not None

    def open_lock(self):
        """
        Command the servo to rotate to the open position (90 degrees).
        Guards against duplicate commands if already open.
        """
        if self.is_open:
            return False  # Skip duplicate command
        
        self.is_open = True
        print("[SERVO] State Transition: LOCK -> OPEN (90 degrees)")
        if self.servo:
            try:
                self.servo.angle = self.max_angle
            except Exception as e:
                print(f"[SERVO ERROR] Failed to write angle to servo: {e}")
        return True

    def close_lock(self):
        """
        Command the servo to rotate to the closed/locked position (0 degrees).
        Guards against duplicate commands if already closed.
        """
        if not self.is_open:
            return False  # Skip duplicate command
        
        self.is_open = False
        print("[SERVO] State Transition: LOCK -> CLOSED (0 degrees)")
        if self.servo:
            try:
                self.servo.angle = self.min_angle
            except Exception as e:
                print(f"[SERVO ERROR] Failed to write angle to servo: {e}")
        return True

    def cleanup(self):
        """
        Gracefully releases GPIO resources. Ensures lock returns to CLOSED.
        """
        print("[SERVO] Releasing GPIO resources...")
        # Emergency safety: return servo to locked position before exit
        if self.is_open:
            self.close_lock()
            
        if self.servo:
            try:
                self.servo.close()
                print("[SERVO] AngularServo closed and GPIO pin released.")
            except Exception as e:
                print(f"[SERVO ERROR] Failed to close/release servo: {e}")
            finally:
                self.servo = None
