from collections import defaultdict
from contextlib import contextmanager
import logging
import math
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING

from esphome.const import (
    CONF_COMMENT,
    CONF_ESPHOME,
    CONF_ETHERNET,
    CONF_OPENTHREAD,
    CONF_PORT,
    CONF_USE_ADDRESS,
    CONF_WEB_SERVER,
    CONF_WIFI,
    KEY_CORE,
    KEY_NATIVE_IDF,
    KEY_TARGET_FRAMEWORK,
    KEY_TARGET_PLATFORM,
    PLATFORM_BK72XX,
    PLATFORM_ESP32,
    PLATFORM_ESP8266,
    PLATFORM_HOST,
    PLATFORM_LN882X,
    PLATFORM_NRF52,
    PLATFORM_RP2040,
    PLATFORM_RTL87XX,
)

# pylint: disable=unused-import
from esphome.coroutine import (  # noqa: F401
    CoroPriority,
    FakeAwaitable as _FakeAwaitable,
    FakeEventLoop as _FakeEventLoop,
    coroutine,
    coroutine_with_priority,
)
from esphome.helpers import ensure_unique_string, get_str_env, is_ha_addon
from esphome.util import OrderedDict

if TYPE_CHECKING:
    from esphome.address_cache import AddressCache

    from ..cpp_generator import MockObj, MockObjClass, Statement
    from ..types import ConfigType, EntityMetadata

_LOGGER = logging.getLogger(__name__)

# Key for tracking controller count in CORE.data for ControllerRegistry StaticVector sizing
KEY_CONTROLLER_REGISTRY_COUNT = "controller_registry_count"


class EsphomeError(Exception):
    """发生的一般ESPHome异常。"""


class HexInt(int):
    def __str__(self):
        value = self
        sign = "-" if value < 0 else ""
        value = abs(value)
        if 0 <= value <= 255:
            return f"{sign}0x{value:02X}"
        return f"{sign}0x{value:X}"


class MACAddress:
    def __init__(self, *parts):
        if len(parts) != 6:
            raise ValueError("MAC Address must consist of 6 items")
        self.parts = parts

    def __str__(self):
        return ":".join(f"{part:02X}" for part in self.parts)

    @property
    def as_hex(self):
        from esphome.cpp_generator import RawExpression

        num = "".join(f"{part:02X}" for part in self.parts)
        return RawExpression(f"0x{num}ULL")


def is_approximately_integer(value):
    """检查一个值是否近似为整数。

    参数:
        value: 要检查的值

    返回:
        bool: 如果值是整数或非常接近整数则返回True
    """
    if isinstance(value, int):
        return True
    return abs(value - round(value)) < 0.001


class TimePeriod:
    def __init__(
        self,
        nanoseconds=None,
        microseconds=None,
        milliseconds=None,
        seconds=None,
        minutes=None,
        hours=None,
        days=None,
    ):
        """使用各种时间单位初始化TimePeriod。

        自动将小数部分转换为更小的单位。
        例如，1.5天会变成1天和12小时。

        参数:
            nanoseconds: 纳秒数
            microseconds: 微秒数
            milliseconds: 毫秒数
            seconds: 秒数
            minutes: 分钟数
            hours: 小时数
            days: 天数
        """
        if days is not None:
            if not is_approximately_integer(days):
                frac_days, days = math.modf(days)
                hours = (hours or 0) + frac_days * 24
            self.days = int(round(days))
        else:
            self.days = None

        if hours is not None:
            if not is_approximately_integer(hours):
                frac_hours, hours = math.modf(hours)
                minutes = (minutes or 0) + frac_hours * 60
            self.hours = int(round(hours))
        else:
            self.hours = None

        if minutes is not None:
            if not is_approximately_integer(minutes):
                frac_minutes, minutes = math.modf(minutes)
                seconds = (seconds or 0) + frac_minutes * 60
            self.minutes = int(round(minutes))
        else:
            self.minutes = None

        if seconds is not None:
            if not is_approximately_integer(seconds):
                frac_seconds, seconds = math.modf(seconds)
                milliseconds = (milliseconds or 0) + frac_seconds * 1000
            self.seconds = int(round(seconds))
        else:
            self.seconds = None

        if milliseconds is not None:
            if not is_approximately_integer(milliseconds):
                frac_milliseconds, milliseconds = math.modf(milliseconds)
                microseconds = (microseconds or 0) + frac_milliseconds * 1000
            self.milliseconds = int(round(milliseconds))
        else:
            self.milliseconds = None

        if microseconds is not None:
            if not is_approximately_integer(microseconds):
                frac_microseconds, microseconds = math.modf(microseconds)
                nanoseconds = (nanoseconds or 0) + frac_microseconds * 1000
            self.microseconds = int(round(microseconds))
        else:
            self.microseconds = None

        if nanoseconds is not None:
            if not is_approximately_integer(nanoseconds):
                raise ValueError("Maximum precision is nanoseconds")
            self.nanoseconds = int(round(nanoseconds))
        else:
            self.nanoseconds = None

    def as_dict(self):
        """将TimePeriod转换为字典表示。

        返回:
            OrderedDict: 包含时间单位键及其值的字典
        """
        out = OrderedDict()
        if self.nanoseconds is not None:
            out["nanoseconds"] = self.nanoseconds
        if self.microseconds is not None:
            out["microseconds"] = self.microseconds
        if self.milliseconds is not None:
            out["milliseconds"] = self.milliseconds
        if self.seconds is not None:
            out["seconds"] = self.seconds
        if self.minutes is not None:
            out["minutes"] = self.minutes
        if self.hours is not None:
            out["hours"] = self.hours
        if self.days is not None:
            out["days"] = self.days
        return out

    def __str__(self):
        if self.nanoseconds is not None:
            return f"{self.total_nanoseconds}ns"
        if self.microseconds is not None:
            return f"{self.total_microseconds}us"
        if self.milliseconds is not None:
            return f"{self.total_milliseconds}ms"
        if self.seconds is not None:
            return f"{self.total_seconds}s"
        if self.minutes is not None:
            return f"{self.total_minutes}min"
        if self.hours is not None:
            return f"{self.total_hours}h"
        if self.days is not None:
            return f"{self.total_days}d"
        return "0s"

    def __repr__(self):
        return f"TimePeriod<{self.total_nanoseconds}ns>"

    @property
    def total_nanoseconds(self):
        """获取总时间周期的纳秒数。

        返回:
            int: 总纳秒数
        """
        return self.total_microseconds * 1000 + (self.nanoseconds or 0)

    @property
    def total_microseconds(self):
        """获取总时间周期的微秒数。

        返回:
            int: 总微秒数
        """
        return self.total_milliseconds * 1000 + (self.microseconds or 0)

    @property
    def total_milliseconds(self):
        """获取总时间周期的毫秒数。

        返回:
            int: 总毫秒数
        """
        return self.total_seconds * 1000 + (self.milliseconds or 0)

    @property
    def total_seconds(self):
        """获取总时间周期的秒数。

        返回:
            int: 总秒数
        """
        return self.total_minutes * 60 + (self.seconds or 0)

    @property
    def total_minutes(self):
        """获取总时间周期的分钟数。

        返回:
            int: 总分钟数
        """
        return self.total_hours * 60 + (self.minutes or 0)

    @property
    def total_hours(self):
        """获取总时间周期的小时数。

        返回:
            int: 总小时数
        """
        return self.total_days * 24 + (self.hours or 0)

    @property
    def total_days(self):
        """获取总时间周期的天数。

        返回:
            int: 总天数
        """
        return self.days or 0

    def __eq__(self, other):
        """检查与另一个TimePeriod的相等性。

        参数:
            other: 另一个TimePeriod实例

        返回:
            bool: 如果两个周期表示相同持续时间则返回True
        """
        if isinstance(other, TimePeriod):
            return self.total_nanoseconds == other.total_nanoseconds
        return NotImplemented

    def __ne__(self, other):
        """检查与另一个TimePeriod的不相等性。

        参数:
            other: 另一个TimePeriod实例

        返回:
            bool: 如果周期表示不同持续时间则返回True
        """
        if isinstance(other, TimePeriod):
            return self.total_nanoseconds != other.total_nanoseconds
        return NotImplemented

    def __lt__(self, other):
        """检查此周期是否小于另一个周期。

        参数:
            other: 另一个TimePeriod实例

        返回:
            bool: 如果此周期较短则返回True
        """
        if isinstance(other, TimePeriod):
            return self.total_nanoseconds < other.total_nanoseconds
        return NotImplemented

    def __gt__(self, other):
        """检查此周期是否大于另一个周期。

        参数:
            other: 另一个TimePeriod实例

        返回:
            bool: 如果此周期较长则返回True
        """
        if isinstance(other, TimePeriod):
            return self.total_nanoseconds > other.total_nanoseconds
        return NotImplemented

    def __le__(self, other):
        """检查此周期是否小于或等于另一个周期。

        参数:
            other: 另一个TimePeriod实例

        返回:
            bool: 如果此周期较短或相等则返回True
        """
        if isinstance(other, TimePeriod):
            return self.total_nanoseconds <= other.total_nanoseconds
        return NotImplemented

    def __ge__(self, other):
        """检查此周期是否大于或等于另一个周期。

        参数:
            other: 另一个TimePeriod实例

        返回:
            bool: 如果此周期较长或相等则返回True
        """
        if isinstance(other, TimePeriod):
            return self.total_nanoseconds >= other.total_nanoseconds
        return NotImplemented


class TimePeriodNanoseconds(TimePeriod):
    pass


class TimePeriodMicroseconds(TimePeriod):
    pass


class TimePeriodMilliseconds(TimePeriod):
    pass


class TimePeriodSeconds(TimePeriod):
    pass


class TimePeriodMinutes(TimePeriod):
    pass


LAMBDA_PROG = re.compile(r"\bid\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)(\.?)")


class Lambda:
    def __init__(self, value):
        """初始化Lambda表达式。

        参数:
            value: Lambda表达式，作为字符串、Expression或其他Lambda
        """
        from esphome.cpp_generator import Expression, statement

        # pylint: disable=protected-access
        if isinstance(value, Lambda):
            self._value = value._value
        elif isinstance(value, Expression):
            self._value = str(statement(value))
        else:
            self._value = value
        self._parts = None
        self._requires_ids = None

    # https://stackoverflow.com/a/241506/229052
    def comment_remover(self, text):
        """从C++代码中移除注释，同时保留字符串字面量。

        参数:
            text: C++代码文本

        返回:
            str: 移除注释后的文本
        """

        def replacer(match):
            s = match.group(0)
            if s.startswith("/"):
                return " "  # note: a space and not an empty string
            return s

        pattern = re.compile(
            r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
            re.DOTALL | re.MULTILINE,
        )
        return re.sub(pattern, replacer, text)

    @property
    def parts(self):
        """获取Lambda表达式的解析部分。

        返回:
            list: 交替的文本和ID部分的列表
        """
        if self._parts is None:
            self._parts = re.split(LAMBDA_PROG, self.comment_remover(self._value))
        return self._parts

    @property
    def requires_ids(self):
        """获取此Lambda表达式所需的ID列表。

        返回:
            list: 从id()调用中提取的ID对象列表
        """
        if self._requires_ids is None:
            self._requires_ids = [
                ID(self.parts[i]) for i in range(1, len(self.parts), 3)
            ]
        return self._requires_ids

    @property
    def value(self):
        """获取Lambda表达式值。

        返回:
            str: Lambda表达式字符串
        """
        return self._value

    @value.setter
    def value(self, value):
        """设置Lambda表达式值并重置缓存的解析结果。

        参数:
            value: 新的Lambda表达式值
        """
        self._value = value
        self._parts = None
        self._requires_ids = None

    def __str__(self):
        """获取Lambda的字符串表示。

        返回:
            str: Lambda表达式值
        """
        return self.value

    def __repr__(self):
        """获取详细的字符串表示用于调试。

        返回:
            str: 显示Lambda值的表示
        """
        return f"Lambda<{self.value}>"


class ID:
    def __init__(self, id, is_declaration=False, type=None, is_manual=None):
        """初始化ESPHome组件的ID对象。

        参数:
            id: 字符串标识符
            is_declaration: 是否为声明
            type: 组件的类型
            is_manual: 此ID是否为手动指定
        """
        self.id = id
        if is_manual is None:
            self.is_manual = id is not None
        else:
            self.is_manual = is_manual
        self.is_declaration = is_declaration
        self.type: MockObjClass | None = type

    def resolve(self, registered_ids):
        """通过生成唯一名称（如果未提供）来解析ID。

        参数:
            registered_ids: 已注册ID的集合

        返回:
            str: 解析后的ID字符串
        """
        from esphome.config_validation import RESERVED_IDS

        if self.id is None:
            base = str(self.type).replace("::", "_").lower()
            if base == self.type:
                base = base + "_id"
            name = "".join(c for c in base if c.isalnum() or c == "_")
            used = set(registered_ids) | set(RESERVED_IDS) | CORE.loaded_integrations
            self.id = ensure_unique_string(name, used)
        return self.id

    def __str__(self):
        """获取ID的字符串表示。

        返回:
            str: ID字符串或空字符串（如果为None）
        """
        if self.id is None:
            return ""
        return self.id

    def __repr__(self):
        """获取详细的字符串表示用于调试。

        返回:
            str: 包含所有属性的详细表示
        """
        return (
            f"ID<{self.id} declaration={self.is_declaration}, "
            f"type={self.type}, manual={self.is_manual}>"
        )

    def __eq__(self, other):
        """检查与另一个ID的相等性。

        参数:
            other: 另一个ID实例

        返回:
            bool: 如果ID相等则返回True
        """
        if isinstance(other, ID):
            return self.id == other.id
        return NotImplemented

    def __hash__(self):
        """获取用于集合和字典的哈希值。

        返回:
            int: ID字符串的哈希值
        """
        return hash(self.id)

    def copy(self):
        """创建此ID对象的副本。

        返回:
            ID: 具有相同属性新的ID实例
        """
        return ID(
            self.id,
            is_declaration=self.is_declaration,
            type=self.type,
            is_manual=self.is_manual,
        )


class DocumentLocation:
    def __init__(self, document: str, line: int, column: int):
        """初始化用于错误报告的文档位置。

        参数:
            document: 文档路径
            line: 行号（1-基于）
            column: 列号（0-基于）
        """
        self.document: str = document
        self.line: int = line
        self.column: int = column

    @classmethod
    def from_mark(cls, mark):
        """从YAML标记创建DocumentLocation。

        参数:
            mark: YAML标记对象

        返回:
            DocumentLocation: 新实例
        """
        return cls(str(mark.name), mark.line, mark.column)

    def __str__(self):
        """获取位置的字符串表示。

        返回:
            str: 格式化的位置字符串
        """
        return f"{self.document} {self.line}:{self.column}"

    @property
    def as_line_directive(self):
        """转换为用于错误报告的C++行指令。

        返回:
            str: C++ #line指令
        """
        document_path = str(self.document).replace("\\", "\\\\")
        return f'#line {self.line + 1} "{document_path}"'


class DocumentRange:
    def __init__(self, start_mark: DocumentLocation, end_mark: DocumentLocation):
        """初始化用于错误报告的文档范围。

        参数:
            start_mark: 开始位置
            end_mark: 结束位置
        """
        self.start_mark: DocumentLocation = start_mark
        self.end_mark: DocumentLocation = end_mark

    @classmethod
    def from_marks(cls, start_mark, end_mark):
        """从YAML标记创建DocumentRange。

        参数:
            start_mark: 开始YAML标记
            end_mark: 结束YAML标记

        返回:
            DocumentRange: 新实例
        """
        return cls(
            DocumentLocation.from_mark(start_mark), DocumentLocation.from_mark(end_mark)
        )

    def __str__(self):
        """获取范围的字符串表示。

        返回:
            str: 格式化的范围字符串
        """
        return f"[{self.start_mark} - {self.end_mark}]"


class Define:
    def __init__(self, name, value=None):
        """初始化C++预处理器定义。

        参数:
            name: 定义名称
            value: 定义值（可选）
        """
        self.name = name
        self.value = value

    @property
    def as_build_flag(self):
        """转换为编译器构建标志格式。

        返回:
            str: 编译器标志字符串
        """
        if self.value is None:
            return f"-D{self.name}"
        return f"-D{self.name}={self.value}"

    @property
    def as_macro(self):
        """转换为C++宏定义格式。

        返回:
            str: 宏定义字符串
        """
        if self.value is None:
            return f"#define {self.name}"
        return f"#define {self.name} {self.value}"

    @property
    def as_tuple(self):
        """获取元组用于比较和哈希。

        返回:
            tuple: (名称, 值)元组
        """
        return self.name, self.value

    def __hash__(self):
        """获取用于集合的哈希值。

        返回:
            int: 元组的哈希值
        """
        return hash(self.as_tuple)

    def __eq__(self, other):
        """检查与另一个Define的相等性。

        参数:
            other: 另一个Define实例

        返回:
            bool: 如果相等则返回True
        """
        if isinstance(other, Define):
            return self.as_tuple == other.as_tuple
        return NotImplemented

    def __str__(self):
        """获取字符串表示。

        返回:
            str: 格式化的定义字符串
        """
        return f"{self.name}={self.value}"


class Library:
    def __init__(self, name, version, repository=None):
        """初始化PlatformIO库依赖。

        参数:
            name: 库名称
            version: 库版本
            repository: 仓库URL（可选）
        """
        self.name = name
        self.version = version
        self.repository = repository

    def __str__(self):
        """获取字符串表示。

        返回:
            str: 库依赖字符串
        """
        return self.as_lib_dep

    @property
    def as_lib_dep(self):
        """转换为PlatformIO库依赖格式。

        返回:
            str: PlatformIO库依赖字符串
        """
        if self.repository is not None:
            if self.name is not None:
                return f"{self.name}={self.repository}"
            return self.repository

        if self.version is None:
            return self.name
        return f"{self.name}@{self.version}"

    @property
    def as_tuple(self):
        """获取元组用于比较和哈希。

        返回:
            tuple: (名称, 版本, 仓库)元组
        """
        return self.name, self.version, self.repository

    def __hash__(self):
        """获取用于集合的哈希值。

        返回:
            int: 元组的哈希值
        """
        return hash(self.as_tuple)

    def __eq__(self, other):
        """检查与另一个Library的相等性。

        参数:
            other: 另一个Library实例

        返回:
            bool: 如果相等则返回True
        """
        if isinstance(other, Library):
            return self.as_tuple == other.as_tuple
        return NotImplemented

    def reconcile_with(self, other):
        """合并两个库，解决任何冲突。

        参数:
            other: 要与之调和的另一个Library实例

        返回:
            Library: 具有调和值的自身

        引发:
            ValueError: 如果库无法调和
        """
        if self.name != other.name:
            # 不同的库，无法调和
            raise ValueError(
                f"Cannot reconcile libraries with different names: {self.name} and {other.name}"
            )

        # 仓库特异性优先于版本特异性
        if self.repository is None and other.repository is None:
            pass  # 没有仓库，没有冲突，继续

        elif self.repository is None:
            # 传入的库有仓库，使用它
            self.repository = other.repository
            self.version = other.version
            return self

        elif other.repository is None:
            return self  # 使用已存在的仓库/版本

        elif self.repository != other.repository:
            raise ValueError(
                f"Reconciliation failed! Libraries {self} and {other} requested with conflicting repositories!"
            )

        if self.version is None and other.version is None:
            return self  # Arduino库与另一个Arduino库调和，当前是可接受的

        if self.version is None:
            # 传入的库有版本，使用它
            self.version = other.version
            return self

        if other.version is None:
            return self  # 传入的库没有版本，当前是可接受的

            # 相同版本，当前库是可接受的
        if self.version != other.version:
            raise ValueError(
                f"Version pinning failed! Libraries {other} and {self} "
                "requested with conflicting versions!"
            )
        return self


# pylint: disable=too-many-public-methods
class EsphomeCore:
    def __init__(self):
        # True if command is run from dashboard
        self.dashboard = False
        # True if command is run from vscode api
        self.vscode = False
        # True if running in testing mode (disables validation checks for grouped testing)
        self.testing_mode = False
        # The name of the node
        self.name: str | None = None
        # The friendly name of the node
        self.friendly_name: str | None = None
        # The area / zone of the node
        self.area: str | None = None
        # Additional data components can store temporary data in.
        # This dict is cleared between compilation runs.
        #
        # Usage pattern (use @dataclass for type safety):
        #   DOMAIN = "my_component"
        #
        #   @dataclass
        #   class MyComponentData:
        #       feature_enabled: bool = False
        #
        #   def _get_data() -> MyComponentData:
        #       if DOMAIN not in CORE.data:
        #           CORE.data[DOMAIN] = MyComponentData()
        #       return CORE.data[DOMAIN]
        #
        # The first key should always be the component domain name (DOMAIN constant).
        self.data = {}
        # The relative path to the configuration YAML
        self.config_path: Path | None = None
        # The relative path to where all build files are stored
        self.build_path: Path | None = None
        # The validated configuration, this is None until the config has been validated
        self.config: ConfigType | None = None
        # The pending tasks in the task queue (mostly for C++ generation)
        # This is a priority queue (with heapq)
        # Each item is a tuple of form: (-priority, unique number, task)
        self.event_loop = _FakeEventLoop()
        # Task counter for pending tasks
        self.task_counter = 0
        # The variable cache, for each ID this holds a MockObj of the variable obj
        self.variables: dict[str, MockObj] = {}
        # A list of statements that go in the main setup() block
        self.main_statements: list[Statement] = []
        # A list of statements to insert in the global block (includes and global variables)
        self.global_statements: list[Statement] = []
        # A map of platformio libraries to add to the project (shortname: (name, version, repository))
        self.platformio_libraries: dict[str, Library] = {}
        # A set of build flags to set in the platformio project
        self.build_flags: set[str] = set()
        # A set of build unflags to set in the platformio project
        self.build_unflags: set[str] = set()
        # A set of defines to set for the compile process in esphome/core/defines.h
        self.defines: set[Define] = set()
        # A map of all platformio options to apply
        self.platformio_options: dict[str, str | list[str]] = {}
        # A set of strings of names of loaded integrations, used to find namespace ID conflicts
        self.loaded_integrations = set()
        # A set of strings for platform/integration combos
        self.loaded_platforms: set[str] = set()
        # A set of component IDs to track what Component subclasses are declared
        self.component_ids = set()
        # Dict to track platform entity counts for pre-allocation
        # Key: platform name (e.g. "sensor", "binary_sensor"), Value: count
        self.platform_counts: defaultdict[str, int] = defaultdict(int)
        # Track entity unique IDs to handle duplicates
        # Dict mapping (device_id, platform, sanitized_name) -> entity metadata
        self.unique_ids: dict[tuple[str, str, str], EntityMetadata] = {}
        # Whether ESPHome was started in verbose mode
        self.verbose = False
        # Whether ESPHome was started in quiet mode
        self.quiet = False
        # A list of all known ID classes
        self.id_classes = {}
        # The current component being processed during validation
        self.current_component: str | None = None
        # Address cache for DNS and mDNS lookups from command line arguments
        self.address_cache: AddressCache | None = None
        # Cached config hash (computed lazily)
        self._config_hash: int | None = None

    def reset(self):
        """重置所有状态以进行新的编译运行。

        清除所有数据结构并将核心重置为干净状态
        以处理新配置。
        """
        from esphome.pins import PIN_SCHEMA_REGISTRY

        self.dashboard = False
        self.name = None
        self.friendly_name = None
        self.area = None
        self.data = {}
        self.config_path = None
        self.build_path = None
        self.config = None
        self.event_loop = _FakeEventLoop()
        self.task_counter = 0
        self.variables = {}
        self.main_statements = []
        self.global_statements = []
        self.platformio_libraries = {}
        self.build_flags = set()
        self.build_unflags = set()
        self.defines = set()
        self.platformio_options = {}
        self.loaded_integrations = set()
        self.component_ids = set()
        self.platform_counts = defaultdict(int)
        self.unique_ids = {}
        self.current_component = None
        self.address_cache = None
        self._config_hash = None
        PIN_SCHEMA_REGISTRY.reset()

    @contextmanager
    def component_context(self, component: str):
        """上下文管理器，用于设置当前正在处理的组件。

        参数:
            component: 正在处理的组件名称

        生成:
            None
        """
        old_component = self.current_component
        self.current_component = component
        try:
            yield
        finally:
            self.current_component = old_component

    @property
    def address(self) -> str | None:
        """获取设备的网络地址。

        返回:
            str或None: 配置的网络地址，如果未配置则为None

        引发:
            ValueError: 如果配置尚未加载
        """
        if self.config is None:
            raise ValueError("Config has not been loaded yet")

        for network_type in (CONF_WIFI, CONF_ETHERNET, CONF_OPENTHREAD):
            if network_type in self.config:
                return self.config[network_type][CONF_USE_ADDRESS]

        if CONF_OPENTHREAD in self.config:
            return f"{self.name}.local"

        return None

    @property
    def web_port(self) -> int | None:
        """获取Web服务器端口。

        返回:
            int或None: 配置的Web服务器端口，如果未配置则为None

        引发:
            ValueError: 如果配置尚未加载
        """
        if self.config is None:
            raise ValueError("Config has not been loaded yet")

        if CONF_WEB_SERVER in self.config:
            try:
                return self.config[CONF_WEB_SERVER][CONF_PORT]
            except KeyError:
                return 80

        return None

    @property
    def comment(self) -> str | None:
        """获取配置注释。

        返回:
            str或None: esphome部分的注释，如果未设置则为None

        引发:
            ValueError: 如果配置尚未加载
        """
        if self.config is None:
            raise ValueError("Config has not been loaded yet")

        if CONF_COMMENT in self.config[CONF_ESPHOME]:
            return self.config[CONF_ESPHOME][CONF_COMMENT]

        return None

    @property
    def config_hash(self) -> int:
        """获取配置的FNV-1a 32位哈希。

        哈希是延迟计算并缓存以提高性能。
        使用sort_keys=True确保确定性排序。
        """
        if self._config_hash is None:
            from esphome import yaml_util
            from esphome.helpers import fnv1a_32bit_hash

            config_str = yaml_util.dump(self.config, show_secrets=True, sort_keys=True)
            self._config_hash = fnv1a_32bit_hash(config_str)
        return self._config_hash

    @property
    def config_dir(self) -> Path:
        """获取配置目录路径。

        返回:
            Path: 配置目录的绝对路径
        """
        if self.config_path.is_dir():
            return self.config_path.absolute()
        return self.config_path.absolute().parent

    @property
    def data_dir(self) -> Path:
        """获取数据目录路径。

        返回:
            Path: 数据目录的路径（根据环境而异）
        """
        if is_ha_addon():
            return Path("/data")
        if "ESPHOME_DATA_DIR" in os.environ:
            return Path(get_str_env("ESPHOME_DATA_DIR", None))
        return self.relative_config_path(".esphome")

    @property
    def config_filename(self) -> str:
        """获取配置文件名。

        返回:
            str: 配置文件的名称
        """
        return self.config_path.name

    def has_at_least_one_component(self, *components: str) -> bool:
        """检查是否配置了任何给定的组件。

        参数:
            *components: 要检查的组件名称

        返回:
            bool: 如果配置了任何组件则返回True

        引发:
            ValueError: 如果配置尚未加载
        """
        if self.config is None:
            raise ValueError("Config has not been loaded yet")

        return any(component in self.config for component in components)

    @property
    def has_networking(self) -> bool:
        """检查是否配置了网络组件。

        返回:
            bool: 如果配置了wifi、ethernet或openthread则返回True
        """
        return self.has_at_least_one_component("wifi", "ethernet", "openthread")

    def relative_config_path(self, *path: str | Path) -> Path:
        """获取相对于配置目录的路径。

        参数:
            *path: 要附加的路径组件

        返回:
            Path: 相对于配置目录的绝对路径
        """
        path_ = Path(*path).expanduser()
        return self.config_dir / path_

    def relative_internal_path(self, *path: str | Path) -> Path:
        """获取相对于内部数据目录的路径。

        参数:
            *path: 要附加的路径组件

        返回:
            Path: 相对于数据目录的绝对路径
        """
        path_ = Path(*path).expanduser()
        return self.data_dir / path_

    def relative_build_path(self, *path: str | Path) -> Path:
        """获取相对于构建目录的路径。

        参数:
            *path: 要附加的路径组件

        返回:
            Path: 相对于构建目录的绝对路径
        """
        path_ = Path(*path).expanduser()
        return self.build_path / path_

    def relative_src_path(self, *path: str | Path) -> Path:
        """获取相对于源目录的路径。

        参数:
            *path: 要附加的路径组件

        返回:
            Path: 相对于src目录的绝对路径
        """
        return self.relative_build_path("src", *path)

    def relative_pioenvs_path(self, *path: str | Path) -> Path:
        """获取相对于PlatformIO环境目录的路径。

        参数:
            *path: 要附加的路径组件

        返回:
            Path: 相对于.pioenvs目录的绝对路径
        """
        return self.relative_build_path(".pioenvs", *path)

    def relative_piolibdeps_path(self, *path: str | Path) -> Path:
        """获取相对于PlatformIO库依赖目录的路径。

        参数:
            *path: 要附加的路径组件

        返回:
            Path: 相对于.piolibdeps目录的绝对路径
        """
        return self.relative_build_path(".piolibdeps", *path)

    @property
    def firmware_bin(self) -> Path:
        """获取编译后的固件二进制文件的路径。

        返回:
            Path: 固件文件路径（.bin或.uf2）
        """
        # Check if using native ESP-IDF build (--native-idf)
        if self.data.get(KEY_NATIVE_IDF, False):
            return self.relative_build_path("build", f"{self.name}.bin")
        if self.is_libretiny:
            return self.relative_pioenvs_path(self.name, "firmware.uf2")
        return self.relative_pioenvs_path(self.name, "firmware.bin")

    @property
    def target_platform(self):
        """获取此构建的目标平台。

        返回:
            str: 平台标识符（例如'esp32'、'esp8266'）
        """
        return self.data[KEY_CORE][KEY_TARGET_PLATFORM]

    @property
    def is_esp8266(self):
        """检查目标平台是否为ESP8266。

        返回:
            bool: 如果为ESP8266构建则返回True
        """
        return self.target_platform == PLATFORM_ESP8266

    @property
    def is_esp32(self):
        """检查目标平台是否为ESP32。

        返回:
            bool: 如果为ESP32构建则返回True
        """
        return self.target_platform == PLATFORM_ESP32

    @property
    def is_rp2040(self):
        """检查目标平台是否为RP2040。

        返回:
            bool: 如果为RP2040构建则返回True
        """
        return self.target_platform == PLATFORM_RP2040

    @property
    def is_bk72xx(self):
        """检查目标平台是否为BK72XX。

        返回:
            bool: 如果为BK72XX构建则返回True
        """
        return self.target_platform == PLATFORM_BK72XX

    @property
    def is_rtl87xx(self):
        """检查目标平台是否为RTL87XX。

        返回:
            bool: 如果为RTL87XX构建则返回True
        """
        return self.target_platform == PLATFORM_RTL87XX

    @property
    def is_ln882x(self):
        """检查目标平台是否为LN882X。

        返回:
            bool: 如果为LN882X构建则返回True
        """
        return self.target_platform == PLATFORM_LN882X

    @property
    def is_libretiny(self):
        """检查目标平台是否基于LibreTiny。

        返回:
            bool: 如果为BK72XX、RTL87XX或LN882X构建则返回True
        """
        return self.is_bk72xx or self.is_rtl87xx or self.is_ln882x

    @property
    def is_nrf52(self):
        """检查目标平台是否为NRF52。

        返回:
            bool: 如果为NRF52构建则返回True
        """
        return self.target_platform == PLATFORM_NRF52

    @property
    def is_host(self):
        """检查目标平台是否为主机/本机。

        返回:
            bool: 如果为主机平台构建则返回True
        """
        return self.target_platform == PLATFORM_HOST

    @property
    def target_framework(self):
        """Get the target framework for this build.

        Returns:
            str: Framework identifier (e.g., 'arduino', 'esp-idf', 'zephyr')
        """
        return self.data[KEY_CORE][KEY_TARGET_FRAMEWORK]

    @property
    def using_arduino(self):
        """Check if using Arduino framework.

        Returns:
            bool: True if using Arduino framework
        """
        return self.target_framework == "arduino"

    @property
    def using_esp_idf(self):
        """Check if using ESP-IDF framework (deprecated).

        Returns:
            bool: True if using ESP-IDF framework

        Note:
            This method is deprecated. Use is_esp32 and/or using_arduino instead.
        """
        _LOGGER.warning(
            "CORE.using_esp_idf was deprecated in 2026.1, will change behavior in 2026.6. "
            "ESP32 Arduino builds on top of ESP-IDF, so ESP-IDF features are available in both frameworks. "
            "Use CORE.is_esp32 and/or CORE.using_arduino instead."
        )
        return self.target_framework == "esp-idf"

    @property
    def using_zephyr(self):
        """Check if using Zephyr framework.

        Returns:
            bool: True if using Zephyr framework
        """
        return self.target_framework == "zephyr"

    def add_job(self, func, *args, **kwargs) -> None:
        """Add a job to the event loop for execution.

        Args:
            func: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
        """
        self.event_loop.add_job(func, *args, **kwargs)

    def flush_tasks(self) -> None:
        """Execute all pending tasks in the event loop.

        Raises:
            EsphomeError: If task execution fails
        """
        try:
            self.event_loop.flush_tasks()
        except RuntimeError as e:
            raise EsphomeError(str(e)) from e

    def add(self, expression, prepend=False) -> "Statement":
        """Add an expression or statement to the main setup() block.

        Args:
            expression: Expression or statement to add
            prepend: If True, add to beginning instead of end

        Returns:
            Statement: The added statement
        """
        from esphome.cpp_generator import Expression, Statement, statement

        if isinstance(expression, Expression):
            expression = statement(expression)
        if not isinstance(expression, Statement):
            raise ValueError(
                f"Add '{expression}' must be expression or statement, not {type(expression)}"
            )

        if prepend:
            self.main_statements.insert(0, expression)
        else:
            self.main_statements.append(expression)
        _LOGGER.debug("Adding: %s", expression)
        return expression

    def add_global(self, expression, prepend=False) -> "Statement":
        """Add an expression or statement to the global block.

        Args:
            expression: Expression or statement to add
            prepend: If True, add to beginning instead of end

        Returns:
            Statement: The added statement
        """
        from esphome.cpp_generator import Expression, Statement, statement

        if isinstance(expression, Expression):
            expression = statement(expression)
        if not isinstance(expression, Statement):
            raise ValueError(
                f"Add '{expression}' must be expression or statement, not {type(expression)}"
            )
        if prepend:
            self.global_statements.insert(0, expression)
        else:
            self.global_statements.append(expression)
        _LOGGER.debug("Adding global: %s", expression)
        return expression

    def add_library(self, library: Library):
        """Add a PlatformIO library to the project.

        Args:
            library: Library object to add

        Raises:
            TypeError: If library is not a Library instance
        """
        if not isinstance(library, Library):
            raise TypeError(
                f"Library {library} must be instance of Library, not {type(library)}"
            )

        if not library.name:
            raise ValueError(f"The library for {library.repository} must have a name")

        short_name = (
            library.name if "/" not in library.name else library.name.split("/")[-1]
        )

        # Auto-enable Arduino libraries on ESP32 Arduino builds
        if self.is_esp32 and self.using_arduino:
            from esphome.components.esp32 import (
                ARDUINO_DISABLED_LIBRARIES,
                _enable_arduino_library,
            )

            if short_name in ARDUINO_DISABLED_LIBRARIES:
                _enable_arduino_library(short_name)

        if short_name not in self.platformio_libraries:
            _LOGGER.debug("Adding library: %s", library)
            self.platformio_libraries[short_name] = library
            return library

        self.platformio_libraries[short_name].reconcile_with(library)
        return self.platformio_libraries[short_name]

    def add_build_flag(self, build_flag: str) -> str:
        """Add a compiler build flag.

        Args:
            build_flag: Build flag to add

        Returns:
            str: The added build flag
        """
        self.build_flags.add(build_flag)
        _LOGGER.debug("Adding build flag: %s", build_flag)
        return build_flag

    def add_build_unflag(self, build_unflag: str) -> None:
        """Add a compiler build unflag (removes a flag).

        Args:
            build_unflag: Build flag to remove
        """
        self.build_unflags.add(build_unflag)
        _LOGGER.debug("Adding build unflag: %s", build_unflag)

    def add_define(self, define):
        """Add a preprocessor define.

        Args:
            define: Define to add (string or Define object)

        Returns:
            Define: The added define object

        Raises:
            ValueError: If define is not string or Define
        """
        if isinstance(define, str):
            define = Define(define)
        elif isinstance(define, Define):
            pass
        else:
            raise ValueError(
                f"Define {define} must be string or Define, not {type(define)}"
            )
        self.defines.add(define)
        _LOGGER.debug("Adding define: %s", define)
        return define

    def add_platformio_option(self, key: str, value: str | list[str]) -> None:
        """Add a PlatformIO configuration option.

        Args:
            key: Option key
            value: Option value (string or list of strings)
        """
        new_val = value
        old_val = self.platformio_options.get(key)
        if isinstance(old_val, list):
            assert isinstance(value, list)
            new_val = old_val + value
        self.platformio_options[key] = new_val

    def _get_variable_generator(self, id):
        """Generator that waits for a variable to be registered.

        Args:
            id: Variable ID to wait for

        Yields:
            None: Until variable is available
        """
        while True:
            try:
                return self.variables[id]
            except KeyError:
                _LOGGER.debug("Waiting for variable %s (%r)", id, id)
                yield

    async def get_variable(self, id) -> "MockObj":
        """Get a registered variable by ID, waiting if necessary.

        Args:
            id: Variable ID to retrieve

        Returns:
            MockObj: The registered variable

        Raises:
            ValueError: If id is not an ID instance
        """
        if not isinstance(id, ID):
            raise ValueError(f"ID {id!r} must be of type ID!")
        # Fast path, check if already registered without awaiting
        if id in self.variables:
            return self.variables[id]
        return await _FakeAwaitable(self._get_variable_generator(id))

    def _get_variable_with_full_id_generator(self, id):
        """Generator that waits for a variable with full ID match.

        Args:
            id: Variable ID to wait for

        Yields:
            None: Until variable is available
        """
        while True:
            if id in self.variables:
                for k, v in self.variables.items():
                    if k == id:
                        return (k, v)
            _LOGGER.debug("Waiting for variable %s", id)
            yield

    async def get_variable_with_full_id(self, id: ID) -> tuple[ID, "MockObj"]:
        """Get a registered variable with full ID match, waiting if necessary.

        Args:
            id: Variable ID to retrieve

        Returns:
            tuple[ID, MockObj]: Tuple of (ID, variable)

        Raises:
            ValueError: If id is not an ID instance
        """
        if not isinstance(id, ID):
            raise ValueError(f"ID {id!r} must be of type ID!")
        return await _FakeAwaitable(self._get_variable_with_full_id_generator(id))

    def register_variable(self, id, obj):
        """Register a variable with the given ID.

        Args:
            id: Variable ID
            obj: Variable object to register

        Raises:
            EsphomeError: If ID is already registered
        """
        if id in self.variables:
            raise EsphomeError(f"ID {id} is already registered")
        _LOGGER.debug("Registered variable %s of type %s", id.id, id.type)
        self.variables[id] = obj

    def has_id(self, id):
        """Check if a variable ID is registered.

        Args:
            id: Variable ID to check

        Returns:
            bool: True if ID is registered
        """
        return id in self.variables

    def register_platform_component(self, platform_name: str, var) -> None:
        """Register a component for a platform and track its count.

        Args:
            platform_name: The name of the platform (e.g., 'sensor', 'binary_sensor')
            var: The variable (component) being registered (currently unused but kept for future use)
        """
        self.platform_counts[platform_name] += 1

    def testing_ensure_platform_registered(self, platform_name: str) -> None:
        """Ensure a platform has at least one entity registered for testing.

        Used during C++ test builds to guarantee USE_* defines are emitted
        without needing a real component variable.

        Args:
            platform_name: Platform name to ensure is registered
        """
        if not self.platform_counts[platform_name]:
            self.platform_counts[platform_name] = 1

    def register_controller(self) -> None:
        """跟踪Controller的注册以进行ControllerRegistry StaticVector大小调整。"""
        controller_count = self.data.setdefault(KEY_CONTROLLER_REGISTRY_COUNT, 0)
        self.data[KEY_CONTROLLER_REGISTRY_COUNT] = controller_count + 1

    @property
    def cpp_main_section(self):
        """Generate C++ code for the main setup() function section.

        Returns:
            str: Formatted C++ code for main statements
        """
        from esphome.cpp_generator import statement

        main_code = []
        for exp in self.main_statements:
            text = str(statement(exp))
            text = text.rstrip()
            main_code.append(text)
        return "\n".join(main_code) + "\n\n"

    @property
    def cpp_global_section(self):
        """Generate C++ code for the global declarations section.

        Returns:
            str: Formatted C++ code for global statements
        """
        from esphome.cpp_generator import statement

        global_code = []
        for exp in self.global_statements:
            text = str(statement(exp))
            text = text.rstrip()
            global_code.append(text)
        return "\n".join(global_code) + "\n"


class AutoLoad(OrderedDict):
    pass


class EnumValue:
    """ESPHome用于为cv.enum标记枚举值的特殊类型。"""

    @property
    def enum_value(self):
        return getattr(self, "_enum_value", None)

    @enum_value.setter
    def enum_value(self, value):
        setattr(self, "_enum_value", value)


CORE = EsphomeCore()
