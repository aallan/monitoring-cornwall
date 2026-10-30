# Monitoring Cornwall. Standard library Python, no build step.
PYTHON  := $(shell which python3)
PORT    := 8090
LEAFLET := 1.9.4

.PHONY: help libs data poll serve clean

help:
	@echo "make libs    download Leaflet $(LEAFLET) into site/lib and verify it"
	@echo "make data    build the map overlays in site/data from OpenStreetMap"
	@echo "make poll    fetch the latest Environment Agency readings once"
	@echo "make serve   run the server, which also polls every 15 minutes"
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

clean:
	rm -rf site/lib site/data .cache data/places.json
