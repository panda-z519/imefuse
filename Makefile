.PHONY: help test lint clean doctor detect status run export

PY ?= python3
VERSION := $(shell $(PY) -c "import re;print(re.search(r'version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read()).group(1))")

# 所有动作都走同一个入口脚本：两套实现只会让行为漂移
SH := ./imefuse.sh

help:
	@echo "imefuse $(VERSION)"
	@echo ""
	@echo "  make doctor   能力分级与已知限制（建议先跑）"
	@echo "  make detect   检测本机装了哪些输入法"
	@echo "  make status   完整状态（JSON）"
	@echo "  make run      启动本地 GUI (127.0.0.1:8770)"
	@echo "  make test     跑单元测试（不需要真机数据）"
	@echo "  make lint     byte-compile + 静态检查"
	@echo "  make clean    清理构建产物"
	@echo ""
	@echo "带选项的调用直接用脚本：$(SH) --help"

doctor:
	$(SH) doctor -v

detect:
	$(SH) detect

status:
	$(SH) status

run:
	$(SH) gui

export:
	@test -n "$(IME)" || { echo "用法: make export IME=wetype"; exit 2; }
	$(SH) export $(IME)

test:
	$(PY) -W error::ResourceWarning -m unittest discover -s tests -v

lint:
	$(PY) -m compileall -q imefuse tests && echo "compile OK"
	@if command -v ruff >/dev/null 2>&1; then \
	  ruff check . --select F,E9,B; \
	else echo "(ruff 未安装，跳过静态检查)"; fi

clean:
	rm -rf build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
