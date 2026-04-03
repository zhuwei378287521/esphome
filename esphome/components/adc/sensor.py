import logging

import esphome.codegen as cg
from esphome.components import sensor, voltage_sampler
from esphome.components.esp32 import get_esp32_variant, include_builtin_idf_component
from esphome.components.nrf52.const import AIN_TO_GPIO, EXTRA_ADC
from esphome.components.zephyr import (
    zephyr_add_overlay,
    zephyr_add_prj_conf,
    zephyr_add_user,
)
from esphome.config_helpers import filter_source_files_from_platform
import esphome.config_validation as cv
from esphome.const import (
    CONF_ATTENUATION,
    CONF_ID,
    CONF_NUMBER,
    CONF_PIN,
    CONF_RAW,
    DEVICE_CLASS_VOLTAGE,
    PLATFORM_NRF52,
    STATE_CLASS_MEASUREMENT,
    UNIT_VOLT,
    PlatformFramework,
)
from esphome.core import CORE

from . import (
    ATTENUATION_MODES,
    ESP32_VARIANT_ADC1_PIN_TO_CHANNEL,
    ESP32_VARIANT_ADC2_PIN_TO_CHANNEL,
    SAMPLING_MODES,
    adc_ns,
    adc_unit_t,
    validate_adc_pin,
)

# 2. 组件元数据
_LOGGER = logging.getLogger(__name__)

AUTO_LOAD = ["voltage_sampler"]  # 自动加载依赖组件

# 3. 配置常量和验证器
CONF_SAMPLES = "samples"
CONF_SAMPLING_MODE = "sampling_mode"


_attenuation = cv.enum(ATTENUATION_MODES, lower=True)
_sampling_mode = cv.enum(SAMPLING_MODES, lower=True)


# 4. 配置验证函数
def validate_config(config):
    # 验证自动衰减不能与原始输出或多采样同时使用
    if config[CONF_RAW] and config.get(CONF_ATTENUATION, None) == "auto":
        # 如果不合法，就抛异常
        raise cv.Invalid("Automatic attenuation cannot be used when raw output is set")

    if config.get(CONF_ATTENUATION, None) == "auto" and config.get(CONF_SAMPLES, 1) > 1:
        raise cv.Invalid(
            "Automatic attenuation cannot be used when multisampling is set"
        )

    # 弃用警告：11db -> 12db
    if config.get(CONF_ATTENUATION) == "11db":
        _LOGGER.warning(
            "`attenuation: 11db` is deprecated, use `attenuation: 12db` instead"
        )
        # Alter value here so `config` command prints the recommended change
        config[CONF_ATTENUATION] = _attenuation("12db")

    return config


# 5. C++ 类定义
# 定义 C++ 类 ADCSensor，继承自：
# sensor.Sensor: 基础传感器接口
# cg.PollingComponent: 轮询组件（定期采样）
# voltage_sampler.VoltageSampler: 电压采样器
ADCSensor = adc_ns.class_(
    "ADCSensor", sensor.Sensor, cg.PollingComponent, voltage_sampler.VoltageSampler
)

CONF_NRF_SAADC = "nrf_saadc"

adc_dt_spec = cg.global_ns.class_("adc_dt_spec")

# 6. 配置模式
CONFIG_SCHEMA = cv.All(
    sensor.sensor_schema(  # # 基础传感器配置
        ADCSensor,
        unit_of_measurement=UNIT_VOLT,  # 单位：伏特
        accuracy_decimals=2,  # 小数位数
        device_class=DEVICE_CLASS_VOLTAGE,
        state_class=STATE_CLASS_MEASUREMENT,
    )
    .extend(  # ADC 特定配置
        {
            cv.Required(CONF_PIN): validate_adc_pin,  # 必需：ADC 引脚
            cv.Optional(CONF_RAW, default=False): cv.boolean,  # 可选：原始输出
            cv.SplitDefault(CONF_ATTENUATION, esp32="0db"): cv.All(  # ESP32 衰减
                cv.only_on_esp32, _attenuation
            ),
            cv.OnlyWith(CONF_NRF_SAADC, PLATFORM_NRF52): cv.declare_id(adc_dt_spec),
            cv.Optional(CONF_SAMPLES, default=1): cv.int_range(
                min=1, max=255
            ),  # 采样数
            cv.Optional(CONF_SAMPLING_MODE, default="avg"): _sampling_mode,  # 采样模式
        }
    )
    .extend(cv.polling_component_schema("60s")),  # 轮询间隔：60秒
    validate_config,  # 自定义验证
)

CONF_ADC_CHANNEL_ID = "adc_channel_id"


# 7. 代码生成函数
async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])  # 创建 C++ 变量
    await cg.register_component(var, config)  # 注册组件
    await sensor.register_sensor(var, config)  # 注册传感器

    # 配置引脚
    if config[CONF_PIN] == "VCC":
        cg.add_define("USE_ADC_SENSOR_VCC")
    elif config[CONF_PIN] == "TEMPERATURE":
        cg.add(var.set_is_temperature())
    elif not CORE.is_nrf52 or config[CONF_PIN][CONF_NUMBER] not in EXTRA_ADC:
        pin = await cg.gpio_pin_expression(config[CONF_PIN])
        cg.add(var.set_pin(pin))

    # 基本配置
    cg.add(var.set_output_raw(config[CONF_RAW]))
    cg.add(var.set_sample_count(config[CONF_SAMPLES]))
    cg.add(var.set_sampling_mode(config[CONF_SAMPLING_MODE]))

    # ESP32 特定配置
    if CORE.is_esp32:
        # Re-enable ESP-IDF's ADC driver (excluded by default to save compile time)
        include_builtin_idf_component("esp_adc")  # 启用 ESP-IDF ADC 驱动

        if attenuation := config.get(CONF_ATTENUATION):
            if attenuation == "auto":
                cg.add(var.set_autorange(cg.global_ns.true))
            else:
                cg.add(var.set_attenuation(attenuation))

        # 配置 ADC 通道
        variant = get_esp32_variant()

        # 根据引脚映射到 ADC1 或 ADC2 通道
        pin_num = config[CONF_PIN][CONF_NUMBER]
        if (
            variant in ESP32_VARIANT_ADC1_PIN_TO_CHANNEL
            and pin_num in ESP32_VARIANT_ADC1_PIN_TO_CHANNEL[variant]
        ):
            chan = ESP32_VARIANT_ADC1_PIN_TO_CHANNEL[variant][pin_num]
            cg.add(var.set_channel(adc_unit_t.ADC_UNIT_1, chan))
        elif (
            variant in ESP32_VARIANT_ADC2_PIN_TO_CHANNEL
            and pin_num in ESP32_VARIANT_ADC2_PIN_TO_CHANNEL[variant]
        ):
            chan = ESP32_VARIANT_ADC2_PIN_TO_CHANNEL[variant][pin_num]
            cg.add(var.set_channel(adc_unit_t.ADC_UNIT_2, chan))

    elif CORE.is_nrf52:  # NRF52 (Zephyr) 特定配置
        # 配置 Zephyr ADC 通道
        # 添加设备树覆盖和配置
        CORE.data.setdefault(CONF_ADC_CHANNEL_ID, 0)
        channel_id = CORE.data[CONF_ADC_CHANNEL_ID]
        CORE.data[CONF_ADC_CHANNEL_ID] = channel_id + 1
        zephyr_add_prj_conf("ADC", True)
        nrf_saadc = config[CONF_NRF_SAADC]
        rhs = cg.RawExpression(
            f"ADC_DT_SPEC_GET_BY_IDX(DT_PATH(zephyr_user), {channel_id})"
        )
        adc = cg.new_Pvariable(nrf_saadc, rhs)
        cg.add(var.set_adc_channel(adc))
        gain = "ADC_GAIN_1_6"
        pin_number = config[CONF_PIN][CONF_NUMBER]
        if pin_number == "VDDHDIV5":
            gain = "ADC_GAIN_1_2"
        if isinstance(pin_number, int):
            GPIO_TO_AIN = {v: k for k, v in AIN_TO_GPIO.items()}
            pin_number = GPIO_TO_AIN[pin_number]
        zephyr_add_user("io-channels", f"<&adc {channel_id}>")
        zephyr_add_overlay(
            f"""
                &adc {{
                    #address-cells = <1>;
                    #size-cells = <0>;

                    channel@{channel_id} {{
                        reg = <{channel_id}>;
                        zephyr,gain = "{gain}";
                        zephyr,reference = "ADC_REF_INTERNAL";
                        zephyr,acquisition-time = <ADC_ACQ_TIME_DEFAULT>;
                        zephyr,input-positive = <NRF_SAADC_{pin_number}>;
                        zephyr,resolution = <14>;
                        zephyr,oversampling = <8>;
                    }};
                }};
            """
        )


# 8. 源文件过滤
# 根据目标平台选择对应的 C++ 实现文件。
FILTER_SOURCE_FILES = filter_source_files_from_platform(
    {
        "adc_sensor_esp32.cpp": {
            PlatformFramework.ESP32_ARDUINO,
            PlatformFramework.ESP32_IDF,
        },
        "adc_sensor_esp8266.cpp": {PlatformFramework.ESP8266_ARDUINO},
        "adc_sensor_rp2040.cpp": {PlatformFramework.RP2040_ARDUINO},
        "adc_sensor_libretiny.cpp": {
            PlatformFramework.BK72XX_ARDUINO,
            PlatformFramework.RTL87XX_ARDUINO,
            PlatformFramework.LN882X_ARDUINO,
        },
        "adc_sensor_zephyr.cpp": {PlatformFramework.NRF52_ZEPHYR},
    }
)
