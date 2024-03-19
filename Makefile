.PHONY: build serve
build:
	asciidoctor src/history.adoc -o docs/history.html

serve:
	(cd docs && python3 -m http.server)
