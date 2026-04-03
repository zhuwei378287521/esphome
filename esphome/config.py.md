展示下方法iter_component_configs方法的过程。

举例yaml文件如下

```yaml
sensor:
  - platform: ultrasonic
    trigger_pin: D1
    echo_pin: D2
  - platform: dht
    pin: D3
    model: DHT22

switch:
  - platform: gpio
    pin: D4
    name: "LED Switch"
```

函数会 yield：

("sensor", sensor_component, [ultrasonic_config, dht_config]) - 多配置组件
("sensor.ultrasonic", ultrasonic_platform, ultrasonic_config) - 平台组件
("sensor.dht", dht_platform, dht_config) - 平台组件
("switch", switch_component, [gpio_config]) - 多配置组件
("switch.gpio", gpio_platform, gpio_config) - 平台组件
