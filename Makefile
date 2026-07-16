# Makefile — canonical gate 的零逻辑入口。
# 全部检查只定义在 scripts/verify.sh；CI/deploy/Runbook 复用同一实现。

SHELL := /usr/bin/env bash

.PHONY: verify help

help:
	@echo "可用目标:"
	@echo "  make verify   运行 canonical repository/release gate"

verify:
	@bash scripts/verify.sh
