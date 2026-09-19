build-SharedLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python/shared"
	cp *.py "$(ARTIFACTS_DIR)/python/shared/"
	if [ -f requirements.txt ]; then python3 -m pip install -r requirements.txt -t "$(ARTIFACTS_DIR)/python"; fi
