# Developer entry points. The Python package itself needs only sqlglot and
# psycopg; everything here is tooling around it.
#
#   make tools      install sv2v (pinned release) for `sqrtl check --sv`
#   make test       pytest (needs a PostgreSQL at $SQRTL_DSN and iverilog)
#   make check      sqrtl check on every example, Verilog-2005 target
#   make check-sv   sqrtl check --sv on every example (SystemVerilog -> sv2v -> Icarus)
#   make web        build the browser playground into web/dist
#   make serve      serve web/dist on http://localhost:8000

PYTHON ?= python3
EXAMPLES := $(wildcard examples/*/)
SV2V_DEST ?= /usr/local/bin

.PHONY: tools test check check-sv web serve clean

tools:
	scripts/install-sv2v.sh $(SV2V_DEST)

test:
	$(PYTHON) -m pytest

check:
	@for d in $(EXAMPLES); do sqrtl check "$$d" || exit 1; done

check-sv:
	@for d in $(EXAMPLES); do sqrtl check --sv "$$d" || exit 1; done

web:
	npm ci --no-audit --no-fund
	npm run build:web

serve:
	npm run serve:web

clean:
	rm -rf web/dist web/.cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
