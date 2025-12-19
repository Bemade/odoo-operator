PYTHON ?= python3

.PHONY: test lint coverage

test:
	$(PYTHON) -m pytest -q

coverage:
	$(PYTHON) -m pytest --cov=handlers --cov=operator --cov-report=term-missing

lint:
	helm lint helm-charts/odoo-operator
