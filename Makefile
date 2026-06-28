# Makefile — 工程硬化②一键验证入口。
# `make verify` 串起:后端 pytest(.venv) + 前端 tsc --noEmit + 前端 npm run build
#                     + check_repo_hardening.py --strict + 红线 grep(viltrox_fit_score)。
# 任一步失败 → 整体非 0 退出。真正逻辑在 scripts/verify.sh,Makefile 只是薄入口。

SHELL := /usr/bin/env bash

.PHONY: verify help

help:
	@echo "可用目标:"
	@echo "  make verify   一键全量验证(pytest + tsc + build + 硬化 + 红线)"

verify:
	@bash scripts/verify.sh
