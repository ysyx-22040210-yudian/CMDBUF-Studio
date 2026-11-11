// Generated from direct NPI FSDB samples; values are sampled BEFORE posedge.
// A pattern identifies one FSDB. reset_segment selects a complete post-reset interval.
// Initialize the DUT using its reset in the formal tool before checking these properties.
// disable iff is not initialization. Verify every C_END cover to detect vacuity.
// Unspecified ARB/R payloads on invalid cycles are recorded but are not asserted.
`default_nettype none
module cmdbuf_fsdb_properties (
    input wire [31:0] pattern,
    input wire [31:0] reset_segment,
    input wire clk,
    input wire rst_n,
    input wire arvalid,
    input wire [2:0] arready,
    input wire [17:0] araddr,
    input wire [1:0] src,
    input wire [7:0] arid,
    input wire [4:0] ar_port_id,
    input wire [1:0] arresp,
    input wire [2:0] rvalid,
    input wire [2:0] rready,
    input wire [127:0] rdata,
    input wire [7:0] rid,
    input wire [4:0] r_port_id,
    input wire [2:0] raddr,
    input wire [1:0] rresp,
    input wire [7:0] req,
    input wire [11:0] arb_addr,
    input wire [3:0] pending_cnt,
    input wire [7:0] ar_rd_gnt,
    input wire [7:0] rw_rd_gnt,
    input wire [1:0] rd_mem_status [7:0],
    input wire [127:0] mem_rdata,
    input wire pc_err
);
    reg [31:0] trace_cycle = 32'd0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) trace_cycle <= 32'd0;
        else if (trace_cycle < 32'd17) trace_cycle <= trace_cycle + 32'd1;

// Selectors are symbolic constants within each reset interval.
    M_PATTERN_STABLE: assume property (@(posedge clk) disable iff (!rst_n)
        (trace_cycle != 32'd0) |-> $stable(pattern));
    M_SEGMENT_STABLE: assume property (@(posedge clk) disable iff (!rst_n)
        (trace_cycle != 32'd0) |-> $stable(reset_segment));
    M_SELECTION: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment < 32'd1));

// pattern 7, reset_segment 0; FSDB release tick 1000; 17 cycles.
    // FSDB posedge 10000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00000: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd0) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h0 &&
            ar_rd_gnt == 8'h00 &&
            rw_rd_gnt == 8'h00 &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00000: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd0) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 20000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00001: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd1) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h1 &&
            araddr == 18'h12001 &&
            src == 2'h0 &&
            arid == 8'h2A &&
            ar_port_id == 5'h06 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00001: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd1) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h1));
    // FSDB posedge 30000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00002: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd2) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h12001 &&
            src == 2'h0 &&
            arid == 8'h2A &&
            ar_port_id == 5'h06 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00002: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd2) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h1));
    // FSDB posedge 40000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00003: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd3) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h12001 &&
            src == 2'h0 &&
            arid == 8'h2A &&
            ar_port_id == 5'h06 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h1 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00003: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd3) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h01 &&
            arb_addr == 12'h480 &&
            pending_cnt == 4'h0));
    // FSDB posedge 50000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00004: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd4) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h12001 &&
            src == 2'h0 &&
            arid == 8'h2A &&
            ar_port_id == 5'h06 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00004: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd4) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 60000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00005: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd5) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00005: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd5) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 70000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00006: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd6) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00006: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd6) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 80000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00007: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd7) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h0123456789ABCDEFFEDCBA9876543210 &&
            pc_err == 1'h0));
    A_P7_S0_C00007: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd7) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 90000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00008: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd8) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00008: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd8) |-> (
            arready == 3'h7 &&
            rvalid == 3'h1 &&
            rdata == 128'h0123456789ABCDEFFEDCBA9876543210 &&
            rid == 8'h2A &&
            r_port_id == 5'h06 &&
            raddr == 3'h1 &&
            rresp == 2'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 100000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00009: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd9) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00009: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd9) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 110000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00010: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd10) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00010: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd10) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 120000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00011: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd11) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00011: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd11) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 130000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00012: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd12) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00012: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd12) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 140000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00013: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd13) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00013: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd13) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 150000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00014: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd14) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00014: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd14) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 160000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00015: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd15) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00015: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd15) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    // FSDB posedge 170000 1ps; snapshot uses the preceding time tick.
    M_P7_S0_C00016: assume property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd16) |-> (
            rst_n == 1'h1 &&
            arvalid == 1'h0 &&
            araddr == 18'h00000 &&
            src == 2'h0 &&
            arid == 8'h00 &&
            ar_port_id == 5'h00 &&
            arresp == 2'h0 &&
            rready == 3'h7 &&
            ar_rd_gnt == 8'hFF &&
            rw_rd_gnt == 8'hFF &&
            rd_mem_status[0] == 2'h0 &&
            rd_mem_status[1] == 2'h0 &&
            rd_mem_status[2] == 2'h0 &&
            rd_mem_status[3] == 2'h0 &&
            rd_mem_status[4] == 2'h0 &&
            rd_mem_status[5] == 2'h0 &&
            rd_mem_status[6] == 2'h0 &&
            rd_mem_status[7] == 2'h0 &&
            mem_rdata == 128'h00000000000000000000000000000000 &&
            pc_err == 1'h0));
    A_P7_S0_C00016: assert property (@(posedge clk) disable iff (!rst_n)
        (pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd16) |-> (
            arready == 3'h7 &&
            rvalid == 3'h0 &&
            req == 8'h00 &&
            pending_cnt == 4'h0));
    C_END_P7_S0: cover property (@(posedge clk) disable iff (!rst_n)
        pattern == 32'd7 && reset_segment == 32'd0 && trace_cycle == 32'd16);

endmodule
`default_nettype wire
