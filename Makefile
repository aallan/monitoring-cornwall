# Monitoring Cornwall. Standard library Python, no build step.
PYTHON  := $(shell which python3)
PORT    := 8090
LEAFLET := 1.9.4

.PHONY: help libs data poll serve test test-py test-js og clean

help:
	@echo "make libs    download Leaflet $(LEAFLET) into site/lib and verify it"
	@echo "make data    build the map overlays in site/data from OpenStreetMap"
	@echo "make poll    fetch the latest Environment Agency readings once"
	@echo "make serve   run the server, which also polls every 5 minutes"
	@echo "make test    run the Python and JavaScript tests (needs Node 22 or later)"
	@echo "make og      redraw the link preview image (needs Google Chrome and make data)"
	@echo "make clean   remove downloaded libraries, overlays and the download cache"
	@echo "             (the readings database in data/ is kept)"

# Leaflet is pinned by version and by checksum (libs.sha256).
libs:
	@mkdir -p site/lib/leaflet
	@for f in leaflet.js leaflet.css; do \
		test -f site/lib/leaflet/$$f || curl -sfL "https://unpkg.com/leaflet@$(LEAFLET)/dist/$$f" -o site/lib/leaflet/$$f || exit 1; \
	done
	@shasum -a 256 -c libs.sha256

data:
	$(PYTHON) scripts/fetch_geodata.py

poll:
	$(PYTHON) -m monitor.poll --once

serve:
	$(PYTHON) serve.py $(PORT)

test: test-py test-js

test-py:
	$(PYTHON) -m unittest discover -s tests -t .

test-js:
	node --test 'tests/js/*.test.mjs'

og:
	$(PYTHON) scripts/og_image.py

clean:
	rm -rf site/lib site/data .cache data/places.json
