// Procedural TC API. No rand fields, constraint blocks or randomize calls.
// Pin variables keep their four-state types. main_phase is an ordinary virtual
// SV task in this lightweight environment, not an implicit UVM phase.
class cmdbuf_main_tc;
    int unsigned num_cycles=64, cycle=0, edge_count=0, phase=0;
    int unsigned clk_low_ns=5, clk_high_ns=5, clk_pause_ns=0;
    // Environment releases reset at 1 ns; procedural cycles must keep it high.
    logic rst_n=1;
    logic [2:0] rready=0;
    logic arvalid=0;
    logic [1:0] src=0, arresp=0;
    logic [17:0] araddr=0;
    logic [7:0] arid=0, ar_rd_gnt=0, rw_rd_gnt=0;
    logic [4:0] ar_port_id=0;
    logic [1:0] rd_mem_status[8];
    logic [127:0] mem_rdata=0, issue_data=0;
    logic pc_err=0, issue_error=0;
    // Refreshed after driving reset/rready and letting combinational logic settle.
    logic [2:0] arready=0;
    logic [7:0] req=0;
    logic [11:0] arb_addr=0;
    logic mem_due=0, due_error=0;
    logic [127:0] due_data=0;

    task begin_cycle(input int unsigned index, input int unsigned run_phase=0);
        cycle=index; phase=run_phase;
        main_begin(this);
    endtask
    task drive_control;
        main_control(this);
    endtask
    task sample_context;
        main_context(this);
    endtask
    task drive_inputs;
        main_drive(this);
    endtask
    virtual task main_phase;
        $fatal(1,"MAIN_PHASE_NOT_IMPLEMENTED");
    endtask
endclass
