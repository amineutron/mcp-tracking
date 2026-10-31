PY := .venv/bin/python

.PHONY: test lint check smoke deploy ui

test:
	$(PY) -m pytest -q

lint:
	ruff check .

# Dette zero : lint propre + tests (dont la coherence code/doc/versions) avant tout commit.
check: lint test

smoke:
	@curl -sf http://127.0.0.1:8765/health && echo " tracking-api OK" || echo "FAIL tracking-api"
	@systemctl is-active tracking-api.service tracking-poller.service

deploy:
	sudo ./deploy.sh

ui:
	$(PY) server.py --ui
