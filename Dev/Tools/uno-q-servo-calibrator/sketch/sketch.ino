#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

constexpr uint8_t Pca9685Address = 0x40;
constexpr uint8_t ServoFrequencyHz = 50;
constexpr uint8_t FirstServoChannel = 0;
constexpr uint8_t LastServoChannel = 7;
constexpr uint16_t SafePulseMin = 50;
constexpr uint16_t SafePulseMax = 600;

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(Pca9685Address);

uint16_t clampPulse(int pulse) {
  if (pulse < SafePulseMin) {
    return SafePulseMin;
  }
  if (pulse > SafePulseMax) {
    return SafePulseMax;
  }
  return static_cast<uint16_t>(pulse);
}

String setServoPulse(int channel, int pulse) {
  if (channel < FirstServoChannel || channel > LastServoChannel) {
    return "error: invalid channel";
  }

  uint16_t safePulse = clampPulse(pulse);
  pwm.setPWM(static_cast<uint8_t>(channel), 0, safePulse);

  return "ok";
}

void setup() {
  Bridge.begin();
  Monitor.begin();

  Wire.begin();
  pwm.begin();
  pwm.setPWMFreq(ServoFrequencyHz);
  delay(10);

  Bridge.provide_safe("set_servo_pulse", setServoPulse);

  Monitor.println("UNO Q servo calibrator ready");
}

void loop() {
}
