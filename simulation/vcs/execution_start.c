/* VCS calls this before HDL initial blocks, after simulation can start. */
#include <stdio.h>
#include <stdlib.h>
#include "vpi_user.h"

static PLI_INT32 studio_simulation_started(p_cb_data unused)
{
    const char *path = getenv("CMDBUF_STUDIO_START_FILE");
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

#ifdef __cplusplus
extern "C"
#endif
void cmdbuf_studio_register(void)
{
    s_cb_data callback = {0};
    callback.reason = cbStartOfSimulation;
    callback.cb_rtn = studio_simulation_started;
    vpi_register_cb(&callback);
}
