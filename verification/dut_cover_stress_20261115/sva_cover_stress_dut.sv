// Current SystemVerilog RTL: protocol corrections confirmed 2026-10-21.
// Compile rtl/files.f; the sibling .v files are the historical implementation.
`default_nettype none
module cmdbuf_rd (
    input wire clk,
    input wire rst_n,
    input wire arvalid,
    output wire [2:0] arready,
    input wire [17:0] araddr,
    input wire [1:0] src,
    input wire [7:0] arid,
    input wire [4:0] ar_port_id,
    input wire [1:0] arresp,
    output wire [2:0] rvalid,
    input wire [2:0] rready,
    output wire [127:0] rdata,
    output wire [7:0] rid,
    output wire [4:0] r_port_id,
    output wire [2:0] raddr,
    output wire [1:0] rresp,
    output wire [7:0] req,
    output wire [11:0] arb_addr,
    output wire [3:0] pending_cnt, // Driven directly by the command module register.
    input wire [7:0] ar_rd_gnt,
    // 每个 Bank 两个四态状态位：0 空闲，1 忙；保留输入的 X/Z。
    input wire [1:0] rd_mem_status [7:0],
    input wire [127:0] mem_rdata,
    input wire pc_err,
    input wire [7:0] rw_rd_gnt
);
    wire alloc_fire, retire_fire, issue_success, capture_valid;
    wire [3:0] alloc_entry, retire_entry, issue_entry, capture_entry, rsp_entry;
    wire [127:0] rsp_data;
    wire rsp_pc_err;

    cmdbuf_rd_cmd u_cmd (
        .clk(clk), .rst_n(rst_n), .arvalid(arvalid), .arready(arready), .araddr(araddr), .src(src), .arid(arid),
        .ar_port_id(ar_port_id), .arresp(arresp), .rvalid(rvalid), .rready(rready), .rid(rid), .r_port_id(r_port_id),
        .raddr(raddr), .rresp(rresp), .req(req), .arb_addr(arb_addr), .pending_cnt(pending_cnt), .ar_rd_gnt(ar_rd_gnt),
        .rd_mem_status(rd_mem_status), .rw_rd_gnt(rw_rd_gnt), .alloc_fire(alloc_fire), .alloc_entry(alloc_entry),
        .retire_fire(retire_fire), .retire_entry(retire_entry), .issue_success(issue_success), .issue_entry(issue_entry),
        .capture_valid(capture_valid), .capture_entry(capture_entry), .rsp_entry(rsp_entry), .rsp_pc_err(rsp_pc_err)
    );
    cmdbuf_rd_otb u_otb (
        .clk(clk), .rst_n(rst_n), .alloc_fire(alloc_fire), .alloc_entry(alloc_entry), .retire_fire(retire_fire),
        .retire_entry(retire_entry), .issue_success(issue_success), .issue_entry(issue_entry), .capture_valid(capture_valid),
        .capture_entry(capture_entry), .mem_rdata(mem_rdata), .pc_err(pc_err), .rsp_entry(rsp_entry), .rsp_data(rsp_data),
        .rsp_pc_err(rsp_pc_err)
    );
    assign rdata = (|rvalid) ? rsp_data : 128'b0;
reg [5:0] sample_count;
always @(posedge clk or negedge rst_n)
  if (!rst_n) sample_count <= 0;
  else if (sample_count < 6'd20) sample_count <= sample_count + 1'b1;
wire e2 = sample_count == 2, e3 = sample_count == 3, e4 = sample_count == 4,
     e5 = sample_count == 5, e6 = sample_count == 6, e7 = sample_count == 7;
wire window_hit = e2 || e4;
wire sample_gate = e2 || e4;
localparam int MIN_D = 2, MAX_D = 3;
default clocking cb @(posedge clk); endclocking
sequence named_seq; e2 ##1 e3; endsequence
property named_prop; @(posedge clk) disable iff (!rst_n) e2 ##1 e3; endproperty
C_REP: cover property (@(posedge clk) disable iff (!rst_n) (e2 || e3 || e4)[*3] ##1 e5);
C_REP_BAD: cover property (@(posedge clk) disable iff (!rst_n) (e2 || e3 || e4)[*4] ##1 e5);
C_GOTO: cover property (@(posedge clk) disable iff (!rst_n) window_hit[->2] ##1 e5);
C_GOTO_BAD: cover property (@(posedge clk) disable iff (!rst_n) window_hit[->2] ##1 e6);
C_NONCONSEC: cover property (@(posedge clk) disable iff (!rst_n) window_hit[=2] ##1 e7);
C_FIRST: cover property (@(posedge clk) disable iff (!rst_n) first_match(e2 ##[1:4] (e3 || e5)) ##1 e4);
C_FIRST_BAD: cover property (@(posedge clk) disable iff (!rst_n) first_match(e2 ##[1:4] (e3 || e5)) ##1 e6);
C_WITHIN: cover property (@(posedge clk) disable iff (!rst_n) e3 within (e2 ##3 e5));
C_INTERSECT: cover property (@(posedge clk) disable iff (!rst_n) (e2 ##1 e3) intersect (e2 ##1 e3));
C_INTERSECT_BAD: cover property (@(posedge clk) disable iff (!rst_n) (e2 ##1 e3) intersect (e2 ##2 e4));
C_THROUGHOUT: cover property (@(posedge clk) disable iff (!rst_n) (e2 || e3 || e4) throughout (e2 ##2 e4));
C_UNTIL: cover property (@(posedge clk) disable iff (!rst_n) (e2 || e3 || e4) until_with e4);
C_VARIABLE: cover property (@(posedge clk) disable iff (!rst_n) e2 ##[MIN_D:MAX_D] e5);
C_ALIAS: cover property (@(posedge clk) disable iff (!rst_n) e2 ##[+] e5);
C_IMPLICATION: cover property (@(posedge clk) disable iff (!rst_n) e2 |=> e3);
C_VACUOUS_BAD: cover property (@(posedge clk) disable iff (!rst_n) 1'b0 |-> e5);
C_GATED: cover property (@(posedge clk) disable iff (!rst_n) $past(e2, 2, sample_gate, @(posedge clk)) && e5);
C_ROSE: cover property (@(posedge clk) disable iff (!rst_n) $rose(e2));
C_ROSE_BAD: cover property (@(posedge clk) disable iff (!rst_n) $rose(e2) && e3);
C_NAMED_SEQ: cover property (@(posedge clk) disable iff (!rst_n) named_seq);
C_NAMED_PROP: cover property (named_prop);
C_DEFAULT: cover property (e2 ##1 e3);
A_CHECK: assert property (@(posedge clk) disable iff (!rst_n) (e2 ##1 e3) |-> e3);

CQ_NATIVE: cover sequence (@(posedge clk) disable iff (!rst_n) e2 ##[1:3] (e3 || e4 || e5))
    begin $display("DUT_SEQUENCE_MATCH"); end
`define MAKE_TYPED(NAME) NAME: cover sequence (@(posedge clk) disable iff (!rst_n) e2 ##[1:3] (e3 || e4 || e5)) begin $display("MACRO_SEQUENCE_MATCH"); end
`MAKE_TYPED(CQ_MACRO)
C_NEXT_INDEXED: cover property (@(posedge clk) disable iff (!rst_n) e2 |-> nexttime[2] e4);
C_FINITE_ALWAYS: cover property (@(posedge clk) disable iff (!rst_n) e2 |-> s_always[1:2] (e3 || e4));
C_FINITE_ALWAYS_BAD: cover property (@(posedge clk) disable iff (!rst_n) e2 |-> s_always[1:2] e3);
C_FINITE_EVENTUALLY: cover property (@(posedge clk) disable iff (!rst_n) e2 |-> s_eventually[1:3] e5);
C_ACCEPT_BOOL: cover property (@(posedge clk) disable iff (!rst_n) accept_on (e3) 1'b0);
A_WINDOW: assert property (@(posedge clk) disable iff (!rst_n) e2 |-> s_always[1:2] (e3 || e4));
A_ACCEPT_BOOL: assert property (@(posedge clk) disable iff (!rst_n) accept_on (1'b1) 1'b0);
A_INDEXED: assert property (@(posedge clk) disable iff (!rst_n) e2 |-> nexttime[2] e4);
always @(posedge clk) if (rst_n) begin C_IMMEDIATE: cover (e3); end
C_BOOL_COMB: cover property (@(posedge clk) disable iff (!rst_n) ((e2 ##1 e3) and (e2 ##2 e4)) or (e5 ##1 e6));
C_WEAK: cover property (@(posedge clk) disable iff (!rst_n) weak(e2 ##[1:$] e4));
C_STRONG: cover property (@(posedge clk) disable iff (!rst_n) strong(e2 ##2 e4));
wire signed [3:0] sample_signed = e2 ? -4'sd1 : 4'sd1;
C_PAST_SIGNED: cover property (@(posedge clk) disable iff (!rst_n) $past(sample_signed, 2, sample_gate, @(posedge clk)) < 0 && e5);
C_FELL: cover property (@(posedge clk) disable iff (!rst_n) $fell(e2, @(posedge clk)) && e3);
C_STABLE: cover property (@(posedge clk) disable iff (!rst_n) $stable(e2) && e4);
C_CHANGED: cover property (@(posedge clk) disable iff (!rst_n) $changed(e2) && e2);
C_SAMPLED: cover property (@(posedge clk) disable iff (!rst_n) $sampled(e3) && e3);
C_STAR_ALIAS: cover property (@(posedge clk) disable iff (!rst_n) e2 ##[*] e2);
C_PARAM_FIXED: cover property (@(posedge clk) disable iff (!rst_n) e2 ##MIN_D e4);
C_PARAM_UNBOUNDED: cover property (@(posedge clk) disable iff (!rst_n) e2 ##[MIN_D:$] e4);
C_LEADING: cover property (@(posedge clk) disable iff (!rst_n) ##2 e4);
CQ_BOOL: cover sequence (@(posedge clk) disable iff (!rst_n) e3);
// Cover checks on actual DUT handshakes, including a macro that takes a sequence.
C_DUT_READ_OPEN: cover property (@(posedge clk) disable iff (!rst_n)
    alloc_fire ##[1:$] retire_fire);
`define SVA_STRESS_COVER(NAME, SEQ) NAME: cover property (@(posedge clk) disable iff (!rst_n) SEQ);
`SVA_STRESS_COVER(C_DUT_MACRO_READ_OPEN, alloc_fire ##[1:$] retire_fire)

// Counter-based controls exercise sequence operators and known misses.
C_DELAY_OPEN_BAD: cover property (@(posedge clk) disable iff (!rst_n) e4 ##[1:$] e2);
C_REP_RANGE: cover property (@(posedge clk) disable iff (!rst_n) (e2 || e3 || e4)[*2:4] ##1 e5);
C_NONCONSEC_RANGE: cover property (@(posedge clk) disable iff (!rst_n) window_hit[=1:2] ##1 e7);
C_GOTO_RANGE: cover property (@(posedge clk) disable iff (!rst_n) window_hit[->1:2] ##1 e5);
C_EVENTUALLY_OPEN: cover property (@(posedge clk) disable iff (!rst_n) e2 |-> s_eventually e5);

// Inner layer adds the open-ended delay and records the final match.
`define SVA_STRESS_NESTED_INNER(NAME, PREFIX_SEQ, FINISH, OUTER_ACTION) \
    NAME: cover property (@(posedge clk) disable iff (!rst_n) \
        (PREFIX_SEQ) ##[1:$] (FINISH)) begin \
        $display("DUT_NESTED_INNER %s", `"NAME`"); \
        OUTER_ACTION \
    end

// Outer layer selects the first issue within four cycles and supplies its own action.
`define SVA_STRESS_NESTED_OUTER(NAME, REQUEST, ISSUE, FINISH) \
    `SVA_STRESS_NESTED_INNER(NAME, first_match((REQUEST) ##[1:4] (ISSUE)), FINISH, \
        $display("DUT_NESTED_OUTER %s", `"NAME`");)

`SVA_STRESS_NESTED_OUTER(C_DUT_NESTED_MACRO_READ,
    alloc_fire, issue_success, retire_fire)
`SVA_STRESS_NESTED_OUTER(C_DUT_NESTED_MACRO_LATE_RETIRE,
    e2, (e3 || e4), e7)
// The only issue is five cycles after the request.
`SVA_STRESS_NESTED_OUTER(C_DUT_NESTED_MACRO_ISSUE_WINDOW_BAD,
    e2, e7, (sample_count == 6'd8))
// The finish pulse coincides with the issue, with no later finish.
`SVA_STRESS_NESTED_OUTER(C_DUT_NESTED_MACRO_SAME_CYCLE_BAD,
    e2, e3, e3)
endmodule
