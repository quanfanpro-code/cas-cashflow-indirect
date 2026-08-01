import importlib.util
from pathlib import Path
import shutil

import tomllib


技能目录 = Path(__file__).resolve().parents[1]


def test_包装测试迁移后使用新技能目录(tmp_path):
    新测试目录 = tmp_path / "迁移后的技能" / "tests"
    新测试目录.mkdir(parents=True)
    新测试文件 = 新测试目录 / "test_skill_wrapper.py"
    shutil.copy2(技能目录 / "tests" / "test_skill_wrapper.py", 新测试文件)

    说明 = importlib.util.spec_from_file_location("迁移后的包装测试", 新测试文件)
    assert 说明 is not None and 说明.loader is not None
    模块 = importlib.util.module_from_spec(说明)
    说明.loader.exec_module(模块)

    assert 模块.SKILL_ROOT == 新测试目录.parent


def test_默认测试不收集依赖本机固定路径的旧验收文件():
    配置 = tomllib.loads((技能目录 / "pyproject.toml").read_text("utf-8"))
    参数 = 配置["tool"]["pytest"]["ini_options"]["addopts"]

    assert "--ignore=tests/test_real_input_compatibility.py" in 参数
    assert "--ignore=tests/test_jingrong_balance_compatibility.py" in 参数
