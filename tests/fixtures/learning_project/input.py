"""输入与配置：用于静态分析和界面验证的预置样例，不运行此项目。"""


def read_config(source):
    """读取并校验用户提供的配置。"""
    options = {"language": source.get("language", "python"), "depth": 3}
    return validate_config(options)


def validate_config(options):
    """验证分析所需的配置字段。"""
    return "language" in options and options["depth"] > 0
