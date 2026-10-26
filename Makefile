.PHONY: help test lint app dist clean install run detect

PY ?= python3
VERSION := $(shell $(PY) -c "import re;print(re.search(r'version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read()).group(1))")

help:
	@echo "imefuse $(VERSION)"
	@echo ""
	@echo "  make detect   检测本机装了哪些输入法"
	@echo "  make run      启动本地 GUI (127.0.0.1:8770)"
	@echo "  make test     跑单元测试（不需要真机数据）"
	@echo "  make app      构建 build/imefuse.app（双击可开）"
	@echo "  make dist     PyInstaller 单文件版（需 pip install pyinstaller）"
	@echo "  make install  pip install -e .（安装 imefuse 命令）"
	@echo "  make clean     清理构建产物"

detect:
	$(PY) -m imefuse.cli detect

run:
	$(PY) -m imefuse.cli gui

test:
	$(PY) -W error::ResourceWarning -m unittest discover -s tests -v

lint:
	$(PY) -m compileall -q imefuse tests && echo "compile OK"

app:
	./tools/build_app.sh

dist:
	./tools/build_dist.sh

install:
	$(PY) -m pip install -e .

clean:
	rm -rf build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
