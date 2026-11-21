.PHONY: run-dashboard setup attendees

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

run-dashboard:
	.venv/bin/python server.py

# Export all attendees of a start.gg tournament to CSV, with entry fees.
#   make attendees TOURNAMENT=bareknuckle-altona-2026
#   make attendees TOURNAMENT=... REGISTRATIONS=startgg-export.csv OUT=fees.csv
TOURNAMENT ?= bareknuckle-altona-2026
OUT ?= attendees-$(notdir $(TOURNAMENT)).csv

attendees:
	python3 scripts/export_attendees.py "$(TOURNAMENT)" -o "$(OUT)" \
		$(if $(REGISTRATIONS),--registrations "$(REGISTRATIONS)",) \
		$(if $(EARLY_BIRD_UNTIL),--early-bird-until "$(EARLY_BIRD_UNTIL)",)
