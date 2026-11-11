# Primary EDA entry point. Paths also work when included by the VM launcher.
.DEFAULT_GOAL := help
empty :=
space := $(empty) $(empty)
EDA_PROJECT_ROOT ?= $(CURDIR)
include $(subst $(space),\$(space),$(EDA_PROJECT_ROOT))/simulation/vcs/Makefile
