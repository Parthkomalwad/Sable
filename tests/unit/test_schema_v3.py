# tests/unit/test_schema_v3.py
from shell.config.schema import ShellConfig


def test_tasks_base_dir_default():
    cfg = ShellConfig.defaults()
    assert cfg.tasks_base_dir == "~/tasks"


def test_tasks_base_dir_from_dict_default():
    cfg = ShellConfig.from_dict({
        "backend": "ollama", "model": "llama3.1",
        "routing_mode": "auto", "setup_complete": True,
    })
    assert cfg.tasks_base_dir == "~/tasks"


def test_tasks_base_dir_from_dict_custom():
    cfg = ShellConfig.from_dict({
        "backend": "ollama", "model": "llama3.1",
        "routing_mode": "auto", "setup_complete": True,
        "tasks_base_dir": "/home/user/myagents",
    })
    assert cfg.tasks_base_dir == "/home/user/myagents"


def test_tasks_base_dir_round_trips():
    cfg = ShellConfig.defaults()
    cfg.tasks_base_dir = "/custom/path"
    d = cfg.to_dict()
    cfg2 = ShellConfig.from_dict(d)
    assert cfg2.tasks_base_dir == "/custom/path"
