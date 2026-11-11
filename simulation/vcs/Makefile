.DEFAULT_GOAL := help
.PHONY: help gui doctor compile build run regress wave coverage report open-report list \
        new-case validate explain diagram trace replay selftest export constraints constraint-test tc-test fsdb-inspect fsdb-formal smoke dist configure
PYTHON ?= python3
# SSH sessions on the old VM may inherit the C locale; explanations contain Chinese.
export PYTHONIOENCODING := utf-8
EDA_PROJECT_ROOT ?= $(CURDIR)
EDA_HERE := $(EDA_PROJECT_ROOT)/simulation/vcs/
EDA := $(PYTHON) "$(EDA_HERE)eda.py" --local
CASE ?=
SEED ?= 1
JOBS ?= 1
SEEDS ?=
CYCLES ?=
CASES ?=
RANDOM_ONLY ?= 0
RANDOM_CASE ?=
TIMEOUT ?= 120
REGRESSION ?= latest
RUN_ID ?=
TEMPLATE ?= tc_directed
DISPLAY ?=
XAUTHORITY ?=
LIMIT ?= 50
ALL ?= 0
WAVES ?= 1
PLAN ?=
REQUEST ?=
EDA_RUN_CASE = $(if $(strip $(CASE)),$(CASE),tc_directed)
EDA_SELECT = $(if $(strip $(RUN_ID)),--case "$(RUN_ID)",$(if $(strip $(CASE)),--case "$(CASE)_seed_$(SEED)"))
EDA_CYCLES = $(if $(strip $(CYCLES)),--cycles "$(CYCLES)")
EDA_REGRESSION = --regression "$(REGRESSION)"
EDA_GUI = --display "$(DISPLAY)" $(if $(strip $(XAUTHORITY)),--xauthority "$(XAUTHORITY)")

help:
	@echo 'CMDBUF RD: local Linux or SSH EDA workspace'
	@echo '  make doctor | smoke                 Check tools, then real compile/simulation/coverage'
	@echo '  make dist                           Build a clean portable ZIP and tar.gz'
	@echo '  make gui                             Open CMDBUF Studio on this computer'
	@echo '  make fsdb-inspect REQUEST=request.json  Inspect FSDB through NPI'
	@echo '  make fsdb-formal REQUEST=request.json   Extract FSDB and generate pattern-selected SVA'
	@echo '  make doctor                          Check VCS / Verdi / URG'
	@echo '  make compile                         Compile, or reuse identical build'
	@echo '  make run CASE=tc_directed SEED=17           Compile if needed, simulate, merge coverage'
	@echo '  make regress SEEDS=1,2,42 JOBS=2      Directed suite + random seeds'
	@echo '       PLAN=simulation/vcs/plans/name.json WAVES=0|1 (FSDB + VCD)'
	@echo '       CYCLES=2000 CASES=tc_directed,tc_retry RANDOM_ONLY=1 TIMEOUT=120'
	@echo '       Unspecified SEEDS/CYCLES/CASES use regression.json'
	@echo '       RANDOM_CASE=my_random RANDOM_ONLY=1  Seed sweep of your own SV TC'
	@echo '  make wave CASE=tc_random SEED=42        Open selected FSDB in Verdi'
	@echo '  make wave                            First failure, otherwise first case'
	@echo '  make coverage                        Open merged code coverage in Verdi'
	@echo '       REGRESSION=latest|batch_id DISPLAY=:0 XAUTHORITY=/path (optional)'
	@echo '  make report                          Print verdicts and report path'
	@echo '  make open-report                     Open HTML report on this machine'
	@echo '  make new-case CASE=my_retry TEMPLATE=tc_retry'
	@echo '  make new-case CASE=my_read            Copy a complete main_phase TC'
	@echo '  make constraints                      Audit complete versioned contract inventory'
	@echo '  make constraint-test JOBS=2            Per-rule positive/negative/interaction checks'
	@echo '  make tc-test JOBS=2                    main_phase timing/if/clock/negative acceptance'
	@echo '       TEMPLATE=tc_random                 Start from constrained random inputs'
	@echo '  make explain CASE=my_retry            Explain main_phase assignments and timing'
	@echo '  make validate CASE=my_retry           Check SV TC with VCS compilation'
	@echo '  make trace CASE=my_retry SEED=17      Show observed events and counters'
	@echo '  make replay CASE=tc_random SEED=42      Replay archived inputs and binary'
	@echo '       RUN_ID=tc_random_seed_42           Alternative full run selector'
	@echo '  make list | selftest | export ALL=1  List cases / negative checks / archive'
	@echo 'Windows PowerShell: .\make.cmd <same target and NAME=value arguments>'
	@echo 'Windows source sync: .\make.cmd sync (preserve VM edits; stop on conflicts)'

gui:
	$(PYTHON) "$(EDA_HERE)gui/server.py" --desktop

dist:
	$(PYTHON) "$(EDA_HERE)portable.py"

configure:
	$(PYTHON) "$(EDA_HERE)vm_make.py" configure MODE=local $(if $(strip $(TOOL_SETUP)),"TOOL_SETUP=$(TOOL_SETUP)")

smoke:
	$(EDA) regress --cases tc_directed --seeds 17,42 --cycles 64 --jobs "$(JOBS)" --timeout "$(TIMEOUT)" --waves "$(WAVES)"

fsdb-inspect fsdb-formal:
	@test -n "$(REQUEST)" || { echo 'Specify REQUEST=path/to/request.json'; exit 2; }
	$(PYTHON) "$(EDA_HERE)fsdb_formal/engine.py" --action $(if $(filter fsdb-inspect,$@),inspect,export) --request "$(REQUEST)"

doctor selftest list:
	$(EDA) $@ $(if $(filter selftest,$@),--jobs "$(JOBS)" --timeout "$(TIMEOUT)")

constraints:
	$(EDA) constraints

constraint-test:
	$(EDA) constraint-test --jobs "$(JOBS)" --timeout "$(TIMEOUT)"

tc-test:
	$(EDA) tc-test --jobs "$(JOBS)" --timeout "$(TIMEOUT)"

compile build:
	$(EDA) build

run:
	$(EDA) run --case "$(EDA_RUN_CASE)" --seed "$(SEED)" $(EDA_CYCLES) --timeout "$(TIMEOUT)" --waves "$(WAVES)"

regress:
	$(EDA) regress $(if $(strip $(SEEDS)),--seeds "$(SEEDS)") $(EDA_CYCLES) \
	  $(if $(strip $(RANDOM_CASE)),--random-case "$(RANDOM_CASE)") \
	  $(if $(strip $(CASES)),--cases "$(CASES)") $(if $(filter 1,$(RANDOM_ONLY)),--random-only) \
	  --jobs "$(JOBS)" --timeout "$(TIMEOUT)" --waves "$(WAVES)" $(if $(strip $(PLAN)),--plan "$(PLAN)")

wave:
	$(EDA) view $(EDA_REGRESSION) $(EDA_SELECT) $(EDA_GUI)

coverage:
	$(EDA) coverage $(EDA_REGRESSION) $(EDA_GUI)

report open-report:
	$(EDA) $@ $(EDA_REGRESSION)

new-case:
	@test -n "$(CASE)" || { echo 'Usage: make new-case CASE=my_case [TEMPLATE=tc_retry]'; exit 2; }
	$(EDA) new-case "$(CASE)" --template "$(TEMPLATE)"

validate explain:
	$(EDA) $@ --case "$(EDA_RUN_CASE)" --seed "$(SEED)" $(EDA_CYCLES)

diagram:
	$(EDA) diagram --case "$(if $(strip $(CASE)),$(CASE),wavedrom_read)"

trace:
	$(EDA) trace $(EDA_REGRESSION) $(EDA_SELECT) --limit "$(LIMIT)"

replay:
	@test -n "$(CASE)$(RUN_ID)" || { echo 'Usage: make replay CASE=tc_random SEED=42 [REGRESSION=batch_id]'; exit 2; }
	$(EDA) replay $(EDA_REGRESSION) $(EDA_SELECT) --timeout "$(TIMEOUT)"

export:
	$(EDA) export $(EDA_REGRESSION) $(if $(filter 1,$(ALL)),--all)
