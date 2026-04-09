sensor.py 定义了 mpu6050 组件的 YAML 配置格式，声明它对应的 C++ 类，并在 to_code() 里把用户配置装配成最终的 C++ 对象关系。

如果你愿意，下一步最合适的是继续做“精读”，但不要整文件一起讲了。我们可以只盯一个主题继续，比如你选一个：

CONFIG_SCHEMA 到底怎么读
to_code() 到底怎么把 Python 映射到 C++
cg / cv / sensor / i2c 这几个 import 在这里各自扮演什么角色
