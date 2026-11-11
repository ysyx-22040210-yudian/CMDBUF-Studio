#!/usr/bin/env bash
# Portable site environment. No installation, host or license defaults.
if [ -n "${EDA_TOOL_SETUP:-}" ]; then
    source "$EDA_TOOL_SETUP" || return $?
fi
if [ -n "${VCS_HOME:-}" ]; then export PATH="$VCS_HOME/bin:$PATH"; fi
if [ -n "${VERDI_HOME:-}" ]; then export PATH="$VERDI_HOME/bin:$PATH"; fi
