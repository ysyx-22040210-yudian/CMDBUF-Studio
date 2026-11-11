// Shared variables and non-optional project contracts. Included inside the TB.
// All pin fields remain four-state logic. VCS randomize produces known values;
// the independent pin sampler and native assertions detect X/Z or modification.
class cmdbuf_sv_tc;
    rand logic rst_n, clk_enable;
    rand int unsigned clk_low_ns, clk_high_ns, num_cycles;
    rand logic [2:0] rready;
    rand logic arvalid;
    rand logic [1:0] src, arresp;
    rand logic [17:0] araddr;
    rand logic [7:0] arid, ar_rd_gnt, rw_rd_gnt;
    rand logic [4:0] ar_port_id;
    rand logic [1:0] rd_mem_status[8];
    rand logic [127:0] mem_rdata, issue_data;
    rand logic pc_err, issue_error;

    // Read-only context for case constraints. cycle counts TC slots, including
    // stopped-clock slots; edge counts actual rising edges, including reset.
    int unsigned cycle, edge_count, stage, phase;
    logic [2:0] arready = 0;
    logic [7:0] req = 0;
    logic [11:0] arb_addr = 0;
    logic mem_due = 0, due_error = 0;
    logic [127:0] due_data = 0;

    // stage: 2=config, 0=clock/reset/rready, 1=remaining inputs.
    // phase: 0=user TC, 1=automatic drain, 2=eight quiet completion slots.
    constraint framework_bounds {
        num_cycles inside {[1:1000000]};
        clk_low_ns inside {[3:1000000]};
        clk_high_ns inside {[1:1000000]};
        if (stage == 2) soft num_cycles == 64;
    }
    constraint framework_contracts {
        if (stage == 1 && rst_n) {
            if (arvalid) { src inside {0,1,2}; arresp inside {0,3}; }
            if (arready == 0) arvalid == 0; // E05
            foreach (rd_mem_status[b]) // E06, all eight banks, both halves
                if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b])
                    rd_mem_status[b][arb_addr[0]] == 1;
            if (mem_due) { mem_rdata == due_data; pc_err == due_error; }
        }
    }
    constraint framework_completion {
        if (phase != 0) {
            if (stage == 0) {
                rst_n == 1; clk_enable == 1; clk_low_ns == 5; clk_high_ns == 5;
                rready == 7;
            }
            if (stage == 1) {
                arvalid == 0; ar_rd_gnt == 255; rw_rd_gnt == 255;
                foreach (rd_mem_status[b])
                    if (req[b]) rd_mem_status[b] == (2'b01 << arb_addr[0]);
                    else rd_mem_status[b] == 0;
            }
        }
    }
endclass
