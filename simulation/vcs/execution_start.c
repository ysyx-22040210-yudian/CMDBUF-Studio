/* VCS calls this before HDL initial blocks, after simulation can start. */
#include <stdio.h>
#include <stdlib.h>
#ifdef test_mode_RTL_COVER
#include <string.h>
#include "sv_vpi_user.h"
#else
#include "vpi_user.h"
#endif

static PLI_INT32 studio_simulation_started(p_cb_data unused)
{
    const char *path = getenv("test_mode_STUDIO_START_FILE");
    FILE *file;
    (void)unused;
    if (!path || !*path) return 0; /* Also safe when simv is run manually. */
    file = fopen(path, "w");
    if (!file) {
        vpi_printf("EDA_EXECUTION_MARKER_ERROR: cannot record simulation start\n");
        vpi_control(vpiFinish, 1);
        return 0;
    }
    fputs("simulation_started\n", file);
    if (fclose(file)) {
        vpi_printf("EDA_EXECUTION_MARKER_ERROR: cannot finish start record\n");
        vpi_control(vpiFinish, 1);
        return 0;
    }
    vpi_printf("EDA_SIMULATION_STARTED\n");
    return 0;
}

#ifdef test_mode_RTL_COVER
static PLI_INT32 studio_collect_cover(p_cb_data unused)
{
    const char *enabled = getenv("test_mode_STUDIO_RTL_COVER");
    const char *scope = "tb_test_mode_rd.dut.";
    (void)unused;
    if (!enabled || strcmp(enabled, "1")) return 0;
    vpiHandle list = vpi_iterate(vpiAssertion, NULL);
    vpiHandle assertion;
    while (list && (assertion = vpi_scan(list))) {
        int kind = vpi_get(vpiType, assertion);
        int is_cover = kind == vpiCover || kind == vpiImmediateCover;
#ifdef vpiCoverSequence
        is_cover = is_cover || kind == vpiCoverSequence;
#endif
        if (!is_cover) continue;
        const char *name = vpi_get_str(vpiFullName, assertion);
        if (!name || strncmp(name, scope, strlen(scope))) continue;
        /* Some simulators reuse the output buffer of vpi_get_str. */
        char *qualified = strdup(name);
        if (!qualified) continue;
        int line = vpi_get(vpiLineNo, assertion);
        int hits = vpi_get(vpiAssertSuccessCovered, assertion);
        const char *file = vpi_get_str(vpiFile, assertion);
        vpi_printf((PLI_BYTE8 *)"FUNCTIONAL_RTL_COVER\t%s\t%s\t%d\t%d\n",
                   qualified, file ? file : "?", line, hits);
        free(qualified);
    }
    vpi_printf((PLI_BYTE8 *)"FUNCTIONAL_RTL_COMPLETE\n");
    return 0;
}
#endif

#ifdef __cplusplus
extern "C"
#endif
void test_mode_studio_register(void)
{
    s_cb_data callback = {0};
    callback.reason = cbStartOfSimulation;
    callback.cb_rtn = studio_simulation_started;
    vpi_register_cb(&callback);
#ifdef test_mode_RTL_COVER
    callback.reason = cbEndOfSimulation;
    callback.cb_rtn = studio_collect_cover;
    vpi_register_cb(&callback);
#endif
}
