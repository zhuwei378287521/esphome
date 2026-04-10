import esphome.codegen as cg
from esphome.components import sensor
import esphome.config_validation as cv
from esphome.const import STATE_CLASS_MEASUREMENT

fake_increment_sensor_ns = cg.esphome_ns.namespace("fake_increment_sensor")
FakeIncrementSensor = fake_increment_sensor_ns.class_(
    "FakeIncrementSensor", sensor.Sensor, cg.PollingComponent
)

CONFIG_SCHEMA = (
    sensor.sensor_schema(
        accuracy_decimals=0,
        state_class=STATE_CLASS_MEASUREMENT,
    )
    .extend(
        {
            cv.GenerateID(): cv.declare_id(FakeIncrementSensor),
        }
    )
    .extend(cv.polling_component_schema("1s"))
)


async def to_code(config):
    var = await sensor.new_sensor(config)
    await cg.register_component(var, config)
