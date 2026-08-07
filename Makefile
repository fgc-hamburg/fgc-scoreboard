# Detect operating system
ifeq ($(OS),Windows_NT)
    VENV_DIR = .venv
    PYTHON = $(VENV_DIR)/Scripts/python.exe
    PIP = $(VENV_DIR)/Scripts/pip.exe
    RM = rmdir /s /q
else
    VENV_DIR = .venv
    PYTHON = $(VENV_DIR)/bin/python
    PIP = $(VENV_DIR)/bin/pip
    RM = rm -rf
endif

.PHONY: run-dashboard setup

setup:
	python -m venv $(VENV_DIR)
	$(PYTHON) -m pip install --upgrade pip
	$(PIP) install -r requirements.txt

run-dashboard:
	$(PYTHON) server.py
