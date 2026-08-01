.PHONY: run-dashboard setup

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run-dashboard:
	.venv/bin/python server.py
