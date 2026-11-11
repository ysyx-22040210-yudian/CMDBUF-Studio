# Primary EDA entry point on the local Linux workstation.
.DEFAULT_GOAL := help
empty :=
space := $(empty) $(empty)
EDA_PROJECT_ROOT ?= $(CURDIR)
include $(subst $(space),\$(space),$(EDA_PROJECT_ROOT))/simulation/vcs/Makefile
