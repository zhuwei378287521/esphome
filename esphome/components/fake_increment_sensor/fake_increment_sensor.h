#pragma once

#include "esphome/core/component.h"
#include "esphome/components/sensor/sensor.h"

namespace esphome {
namespace fake_increment_sensor {

class FakeIncrementSensor : public sensor::Sensor, public PollingComponent {
 public:
  void setup() override;
  void update() override;
  void dump_config() override;

 protected:
  int counter_{0};
};

}  // namespace fake_increment_sensor
}  // namespace esphome
