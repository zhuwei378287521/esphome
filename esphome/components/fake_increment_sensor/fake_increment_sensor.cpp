#include "fake_increment_sensor.h"  //把你自己写的头文件包含进来
#include "esphome/core/log.h"

namespace esphome {
namespace fake_increment_sensor {

static const char *const TAG = "fake_increment_sensor";

void FakeIncrementSensor::setup() { this->counter_ = 0; }

void FakeIncrementSensor::dump_config() {
  ESP_LOGCONFIG(TAG, "Fake Increment Sensor:");
  LOG_SENSOR("", "Fake Increment Sensor", this);
  LOG_UPDATE_INTERVAL(this);
}

void FakeIncrementSensor::update() {
  this->counter_ += 1;
  ESP_LOGD(TAG, "Publishing state: %d", this->counter_);
  this->publish_state(this->counter_);
}

}  // namespace fake_increment_sensor
}  // namespace esphome
