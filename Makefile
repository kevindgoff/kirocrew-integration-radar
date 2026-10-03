# Run the tests under the same interpreter the gateway uses for the backend when it
# is available (it already ships aiohttp and PyYAML); override with PYTHON=...
CREW_PYTHON := /Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12
PYTHON ?= $(if $(wildcard $(CREW_PYTHON)),$(CREW_PYTHON),python3)

.PHONY: test ui

test:
	PYTHONPATH=backend $(PYTHON) -m unittest discover -s tests -v

ui:
	cd ui && npm install && npm run build
