import esphome.codegen as cg  # 提供“把 Python 描述映射成 C++ 对象”的能力
from esphome.components import (  # 表示当前组件复用了 i2c 和 sensor 两个基础组件
    i2c,
    sensor,
)
import esphome.config_validation as cv  # 提供配置校验能力
from esphome.const import (
    CONF_ID,
    CONF_TEMPERATURE,
    DEVICE_CLASS_TEMPERATURE,
    ICON_BRIEFCASE_DOWNLOAD,
    ICON_SCREEN_ROTATION,
    STATE_CLASS_MEASUREMENT,
    UNIT_CELSIUS,
    UNIT_DEGREE_PER_SECOND,
    UNIT_METER_PER_SECOND_SQUARED,
)  # 提供统一的配置名、单位、图标、设备类型常量

# 这是一种系统组件的依赖
DEPENDENCIES = ["i2c"]  # 表示当前组件依赖 i2c 组件，必须先安装 i2c 组件才能使用当前组件

# 这部分是在定义 YAML 配置项名字。你以后写组件也会经常这么干。
CONF_ACCEL_X = "accel_x"
CONF_ACCEL_Y = "accel_y"
CONF_ACCEL_Z = "accel_z"
CONF_GYRO_X = "gyro_x"
CONF_GYRO_Y = "gyro_y"
CONF_GYRO_Z = "gyro_z"

# 这两行是整个文件里最核心的桥梁。
mpu6050_ns = cg.esphome_ns.namespace(
    "mpu6050"
)  # 说明这个组件对应的 C++ 命名空间是 esphome::mpu6050
MPU6050Component = mpu6050_ns.class_(
    "MPU6050Component", cg.PollingComponent, i2c.I2CDevice
)  # 说明这个组件对应的 C++ 类是 MPU6050Component，它继承了 PollingComponent 和 I2CDevice 两个基类

# PollingComponent 表示它会周期性执行 update()
# i2c.I2CDevice 表示它是一个 I2C 设备，能复用 I2C 读写能力


# 这一步其实是在给每个输出通道定义“配置模板”。
accel_schema = sensor.sensor_schema(
    unit_of_measurement=UNIT_METER_PER_SECOND_SQUARED,
    icon=ICON_BRIEFCASE_DOWNLOAD,
    accuracy_decimals=2,
    state_class=STATE_CLASS_MEASUREMENT,
)
gyro_schema = sensor.sensor_schema(
    unit_of_measurement=UNIT_DEGREE_PER_SECOND,
    icon=ICON_SCREEN_ROTATION,
    accuracy_decimals=2,
    state_class=STATE_CLASS_MEASUREMENT,
)
temperature_schema = sensor.sensor_schema(
    unit_of_measurement=UNIT_CELSIUS,
    accuracy_decimals=1,
    device_class=DEVICE_CLASS_TEMPERATURE,
    state_class=STATE_CLASS_MEASUREMENT,
)

CONFIG_SCHEMA = (
    cv.Schema(  # cv.Schema({...}) 表示“定义这个组件自己的配置项”。
        {
            cv.GenerateID(): cv.declare_id(
                MPU6050Component
            ),  # 这个配置会对应一个 MPU6050Component 类型的对象 ID。
            # 简单理解为：“告诉代码生成系统，将来这里要生成一个 C++ 组件实例。”
            cv.Optional(
                CONF_ACCEL_X
            ): accel_schema,  # 这个配置项是可选的，如果用户在 YAML 里配置了 accel_x，就会按照 accel_schema 的模板进行校验。
            cv.Optional(
                CONF_ACCEL_Y
            ): accel_schema,  # 同上，配置项名字是 accel_y，校验模板是 accel_schema。
            cv.Optional(
                CONF_ACCEL_Z
            ): accel_schema,  # 同上，配置项名字是 accel_z，校验模板是 accel_schema。
            cv.Optional(
                CONF_GYRO_X
            ): gyro_schema,  # 同上，配置项名字是 gyro_x，校验模板是 gyro_schema。
            cv.Optional(
                CONF_GYRO_Y
            ): gyro_schema,  # 同上，配置项名字是 gyro_y，校验模板是 gyro_schema。
            cv.Optional(
                CONF_GYRO_Z
            ): gyro_schema,  # 同上，配置项名字是 gyro_z，校验模板是 gyro_schema。
            cv.Optional(
                CONF_TEMPERATURE
            ): temperature_schema,  # 同上，配置项名字是 temperature，校验模板是 temperature_schema。
        }
    )
    .extend(
        cv.polling_component_schema("60s")
    )  # cv.polling_component_schema("60s") 表示“这个组件还支持 PollingComponent 的通用配置项”，比如 update_interval: 60s。
    .extend(
        i2c.i2c_device_schema(0x68)
    )  # i2c.i2c_device_schema(0x68) 表示“这个组件还支持 I2CDevice 的通用配置项”，比如 address: 0x68。
)


# 这部分是把用户的 YAML 配置转换成 C++ 代码的核心逻辑。
# 这就是“把配置翻译成 C++ 对象”的地方。
async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])  # 创建一个 C++ 组件对象。
    await cg.register_component(
        var, config
    )  # 把它注册成：一个 ESPHome 组件，这样它才能被调用 update()。
    await i2c.register_i2c_device(
        var, config
    )  # 把它注册成：一个 I2C 设备，这样它才能被调用 I2C 读写方法。

    for d in ["x", "y", "z"]:
        accel_key = f"accel_{d}"
        if accel_key in config:
            # 这一步非常重要，它体现了这个文件真正的职责：
            sens = await sensor.new_sensor(config[accel_key])
            cg.add(getattr(var, f"set_accel_{d}_sensor")(sens))
        accel_key = f"gyro_{d}"  # 这里是个小错误，应该是 gyro_key = f"gyro_{d}"，不过不影响理解。
        if accel_key in config:
            sens = await sensor.new_sensor(config[accel_key])
            cg.add(getattr(var, f"set_gyro_{d}_sensor")(sens))

    # 如果用户在配置里写了 temperature 这个项，就创建一个传感器对象，并调用 var.set_temperature_sensor(sens) 把它关联到组件里。
    if CONF_TEMPERATURE in config:
        sens = await sensor.new_sensor(config[CONF_TEMPERATURE])
        cg.add(var.set_temperature_sensor(sens))
