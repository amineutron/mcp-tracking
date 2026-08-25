PY := .venv/bin/python

.PHONY: test smoke deploy ui

test:
	$(PY) -m pytest -q

smoke:
	@curl -sf http://127.0.0.1:8765/health && echo " tracking-api OK" || echo "FAIL tracking-api"
	@systemctl is-active tracking-api.service tracking-poller.service

deploy:
	sudo ./deploy.sh

ui:
	$(PY) server.py --ui
