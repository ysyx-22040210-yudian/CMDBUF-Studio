.DEFAULT_GOAL := help
.PHONY: help gui doctor compile build run regress check-test env-fatal-test wave coverage report open-report list \
        new-case validate explain diagram trace replay selftest export constraints constraint-test tc-test fsdb-inspect fsdb-formal smoke dist configure
PYTHON ?= python3
# Keep Chinese diagnostics readable on Linux installations with a C locale.
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
# License wait is always unlimited. TIMEOUT starts at cbStartOfSimulation; 0 disables it.
REGRESSION ?= latest
RUN_ID ?=
TEMPLATE ?= tc_directed
DISPLAY ?=
XAUTHORITY ?=
LIMIT ?= 50
ALL ?= 0
WAVES ?= 1
CHECK_MODE ?=
ASSERT_FATAL ?=
ENV_FATAL ?=
PLAN ?=
REQUEST ?=
DUT_SCOPE ?=
REGRESSIONS ?=
COVERAGE_SET ?=
SOLVE_SECONDS ?= 0
FORMAL_ID ?=
MAX_EDGES ?= 100000
EDA_RUN_CASE = $(if $(strip $(CASE)),$(CASE),tc_directed)
EDA_SELECT = $(if $(strip $(RUN_ID)),--case "$(RUN_ID)",$(if $(strip $(CASE)),--case "$(CASE)_seed_$(SEED)"))
EDA_CYCLES = $(if $(strip $(CYCLES)),--cycles "$(CYCLES)")
EDA_CHECKS = $(if $(strip $(CHECK_MODE)),--check-mode "$(CHECK_MODE)") $(if $(strip $(ASSERT_FATAL)),--assert-fatal "$(ASSERT_FATAL)") $(if $(strip $(ENV_FATAL)),--env-fatal "$(ENV_FATAL)")
EDA_REGRESSION = --regression "$(REGRESSION)"
EDA_GUI = --display "$(DISPLAY)" $(if $(strip $(XAUTHORITY)),--xauthority "$(XAUTHORITY)")
EDA_WAVE_SCOPE = $(if $(strip $(DUT_SCOPE)),--dut-scope "$(DUT_SCOPE)")
EDA_COVERAGE = $(PYTHON) "$(EDA_HERE)code_coverage/engine.py"
EDA_COVERAGE_BATCHES = $(EDA_REGRESSION) $(if $(strip $(REGRESSIONS)),--regressions "$(REGRESSIONS)")
EDA_COVERAGE_SET = $(if $(strip $(COVERAGE_SET)),--coverage-set "$(COVERAGE_SET)")

help:
	@echo 'CMDBUF Studio: native Tk desktop and local Linux EDA'
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
	@echo '       CHECK_MODE=transaction|strict ENV_FATAL=0|1 ASSERT_FATAL=0|1'
	@echo '       New runs default to transaction + fatal; old plans retain strict scope.'
	@echo '       ENV_FATAL=0 continues after recoverable driver AND assertion errors; the run still FAILS.'
	@echo '       ASSERT_FATAL=0 only disables assertion aborts when ENV_FATAL=1 (the default).'
	@echo '       Invalid clocks, files, solver failures and watchdogs remain fatal.'
	@echo '       CYCLES=2000 CASES=tc_directed,tc_retry RANDOM_ONLY=1 TIMEOUT=120'
	@echo '       License wait is unlimited. TIMEOUT counts simulation execution only; 0 disables it.'
	@echo '       VCS automatically uses -lca -licqueue +vcs+lic+wait; simv/URG queue licenses too.'
	@echo '       Unspecified SEEDS/CYCLES/CASES use regression.json'
	@echo '       RANDOM_CASE=my_random RANDOM_ONLY=1  Seed sweep of your own SV TC'
	@echo '  make wave CASE=tc_random SEED=42        Open selected FSDB in Verdi'
	@echo '  make wave                            First failure, otherwise first case'
	@echo '       Automatically add every dumped signal at DUT top (ports + internal signals).'
	@echo '  make wave-file FSDB=/path/wave.fsdb  Open imported FSDB with the same preset'
	@echo '       DUT_SCOPE=tb_cmdbuf_rd.dut      Optional explicit instance for imported FSDB'
	@echo '  make coverage                        Open merged code coverage in Verdi'
	@echo '       REGRESSION=latest|batch_id DISPLAY=:0 XAUTHORITY=/path (optional)'
	@echo '  make coverage-details REGRESSION=latest   Inspect each code coverage item in Tk'
	@echo '  make coverage-minimize REGRESSION=latest  Prove the smallest set and remerge with URG'
	@echo '  make coverage-formal COVERAGE_SET=id      Generate one Formal pattern per selected case'
	@echo '       REGRESSIONS=id1,id2 combines compatible histories; SOLVE_SECONDS=0 searches without a limit'
	@echo '       COVERAGE_SET=id reopens a saved analysis with coverage-details or its verified VDB with coverage'
	@echo '  make report                          Print verdicts and report path'
	@echo '  make open-report                     Open results in the native Tk window'
	@echo '  make new-case CASE=my_retry TEMPLATE=tc_retry'
	@echo '  make new-case CASE=my_read            Copy a complete main_phase TC'
	@echo '  make constraints                      Audit complete versioned contract inventory'
	@echo '  make constraint-test JOBS=2            Per-rule positive/negative/interaction checks'
	@echo '  make tc-test JOBS=2                    main_phase timing/if/clock/negative acceptance'
	@echo '  make env-fatal-test JOBS=2            Environment fatal / continue / coverage acceptance'
	@echo '  make check-test JOBS=2                 Snapshot / transaction / fatal positive and negative VCS tests'
	@echo '       TEMPLATE=tc_random                 Start from constrained random inputs'
	@echo '  make explain CASE=my_retry            Explain main_phase assignments and timing'
	@echo '  make validate CASE=my_retry           Check SV TC with VCS compilation'
	@echo '  make trace CASE=my_retry SEED=17      Show observed events and counters'
	@echo '  make replay CASE=tc_random SEED=42      Replay archived inputs and binary'
	@echo '       RUN_ID=tc_random_seed_42           Alternative full run selector'
	@echo '  make list | selftest | export ALL=1  List cases / negative checks / archive'
	@echo 'Windows PowerShell: .\make.cmd <same target and NAME=value arguments>'
	@echo 'Windows: make.cmd gui supports project editing; EDA execution requires Linux.'

gui:
	$(PYTHON) "$(EDA_HERE)gui/desktop.py"

dist:
	$(PYTHON) "$(EDA_HERE)portable.py"

configure:
	$(PYTHON) "$(EDA_HERE)launcher.py" configure $(if $(strip $(TOOL_SETUP)),"TOOL_SETUP=$(TOOL_SETUP)") $(if $(strip $(VCS_HOME)),"VCS_HOME=$(VCS_HOME)") $(if $(strip $(VERDI_HOME)),"VERDI_HOME=$(VERDI_HOME)")

.PHONY: wave-file wave-convert
wave-file wave-convert:
	$(PYTHON) "$(EDA_HERE)wave_tools.py" $(if $(filter wave-file,$@),view,convert) --fsdb "$(FSDB)" $(if $(strip $(OUTPUT)),--output "$(OUTPUT)") $(EDA_WAVE_SCOPE)

.PHONY: env-info
env-info:
	$(PYTHON) "$(EDA_HERE)platform_config.py" $(if $(strip $(INFO)),--output "$(INFO)")

smoke:
	$(EDA) regress --cases tc_directed --seeds 17,42 --cycles 64 --jobs "$(JOBS)" --timeout "$(TIMEOUT)" --waves "$(WAVES)" $(EDA_CHECKS)

fsdb-inspect fsdb-formal:
	@test -n "$(REQUEST)" || { echo 'Specify REQUEST=path/to/request.json'; exit 2; }
	$(PYTHON) "$(EDA_HERE)fsdb_formal/engine.py" --action $(if $(filter fsdb-inspect,$@),inspect,export) --request "$(REQUEST)"

doctor selftest list:
	$(EDA) $@ $(if $(filter selftest,$@),--jobs "$(JOBS)" --timeout "$(TIMEOUT)" $(EDA_CHECKS))

constraints:
	$(EDA) constraints

constraint-test:
	$(EDA) constraint-test --jobs "$(JOBS)" --timeout "$(TIMEOUT)"

tc-test:
	$(EDA) tc-test --jobs "$(JOBS)" --timeout "$(TIMEOUT)"

env-fatal-test:
	$(PYTHON) "$(EDA_HERE)environment_suite.py" --jobs "$(JOBS)"

check-test:
	$(PYTHON) "$(EDA_HERE)check_suite.py" --jobs "$(JOBS)"

compile build:
	$(EDA) build

run:
	$(EDA) run --case "$(EDA_RUN_CASE)" --seed "$(SEED)" $(EDA_CYCLES) --timeout "$(TIMEOUT)" --waves "$(WAVES)" $(EDA_CHECKS)

regress:
	$(EDA) regress $(if $(strip $(SEEDS)),--seeds "$(SEEDS)") $(EDA_CYCLES) \
	  $(if $(strip $(RANDOM_CASE)),--random-case "$(RANDOM_CASE)") \
	  $(if $(strip $(CASES)),--cases "$(CASES)") $(if $(filter 1,$(RANDOM_ONLY)),--random-only) \
	  --jobs "$(JOBS)" --timeout "$(TIMEOUT)" --waves "$(WAVES)" $(if $(strip $(PLAN)),--plan "$(PLAN)") $(EDA_CHECKS)

wave:
	$(EDA) view $(EDA_REGRESSION) $(EDA_SELECT) $(EDA_GUI) $(EDA_WAVE_SCOPE)

coverage:
	$(EDA) coverage $(EDA_REGRESSION) $(EDA_GUI) $(EDA_COVERAGE_SET)

.PHONY: coverage-details coverage-minimize coverage-formal
coverage-details:
	$(EDA_COVERAGE) --action details $(EDA_COVERAGE_BATCHES) $(if $(strip $(COVERAGE_SET)),--existing "$(COVERAGE_SET)")

coverage-minimize:
	$(EDA_COVERAGE) --action minimize $(EDA_COVERAGE_BATCHES) --seconds "$(SOLVE_SECONDS)"

coverage-formal:
	@test -n "$(COVERAGE_SET)" || { echo 'Specify COVERAGE_SET=analysis_id'; exit 2; }
	$(PYTHON) "$(EDA_HERE)fsdb_formal/coverage_export.py" --coverage-set "$(COVERAGE_SET)" --max-edges "$(MAX_EDGES)" --timeout "$(TIMEOUT)" $(if $(strip $(FORMAL_ID)),--id "$(FORMAL_ID)")

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
