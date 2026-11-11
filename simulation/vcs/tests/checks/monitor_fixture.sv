`timescale 1ns/1ps
module monitor_fixture;
    reg clk=0, rst_n=0, arvalid=0;
    reg [2:0] arready=7, rvalid=0, rready=7;
    reg [17:0] araddr=0;
    reg [1:0] src=0, arresp=0, rresp=0;
    reg [7:0] arid=0, rid=0, req=0, ar_rd_gnt=255, rw_rd_gnt=255;
    reg [4:0] ar_port_id=0, r_port_id=0;
    reg [127:0] rdata=0, mem_rdata=0;
    reg [2:0] raddr=0;
    reg [11:0] arb_addr=0;
    reg [3:0] pending_cnt=0;
    reg [1:0] rd_mem_status[7:0];
    reg pc_err=0;
    string scenario="snapshot";
    integer ticks=0;
    cmdbuf_check_monitor monitor(.*);
    // The consumed R payload is correct. A post-NBA read would see corruption.
    always @(posedge clk)
        if (scenario=="snapshot" && rvalid!=0) begin rid<=8'hff; rdata<='1; end
    task tick;
        #5; clk=1; #2; clk=0; #3; ticks++;
    endtask
    task offer(input integer lane,idnum,portnum);
        src=lane; arid=idnum; ar_port_id=portnum; arvalid=1;
        tick(); arvalid=0;
    endtask
    task response(input integer lane,idnum,portnum,input logic[127:0] payload);
        rvalid=1<<lane; rid=idnum; r_port_id=portnum; rdata=payload;
        tick(); rvalid=0;
    endtask
    initial begin
        if ($value$plusargs("SCENARIO=%s",scenario)) begin end
        for (integer b=0;b<8;b++) rd_mem_status[b]=1;
        tick(); rst_n=1;
        offer(0,8'h11,5'h03);
        if (scenario=="unfinished") begin monitor.finish_checks(); $finish; end
        if (scenario=="reset_cancel") begin
            #1; rst_n=0; #1; rst_n=1; tick();
            monitor.finish_checks(); $finish;
        end
        if (scenario=="ambiguous" || scenario=="ambiguous_bad_data" || scenario=="same_id_order") begin
            // Same visible issue address, different source. B issues before A.
            offer(scenario=="same_id_order"?0:1,scenario=="same_id_order"?8'h11:8'h22,5'h04);
            req=1; tick(); tick(); req=0;
            tick(); tick();
            mem_rdata=128'hbbbb; tick(); mem_rdata=128'haaaa; tick();
            response(0,8'h11,scenario=="same_id_order"?5'h04:5'h03,scenario=="ambiguous_bad_data" ? 128'hdead : 128'haaaa);
            response(scenario=="same_id_order"?0:1,scenario=="same_id_order"?8'h11:8'h22,scenario=="same_id_order"?5'h03:5'h04,128'hbbbb);
        end else begin
            req=1; tick(); req=0;
            tick(); tick(); tick();
            mem_rdata=128'h12345678; tick();
            if (scenario=="late") repeat(6) tick();
            if (scenario=="reuse") begin arvalid=1; arid=8'h33; ar_port_id=5'h05; end
            response(0,(scenario=="last_bad" || scenario=="two_errors" || scenario=="reset_masks_error")?8'h99:8'h11,
                     scenario=="two_errors"?5'h1f:5'h03,scenario=="last_x"?'x:128'h12345678);
            if (scenario=="reuse") begin
                arvalid=0;req=1;tick();req=0;tick();tick();tick();
                mem_rdata=128'h98765432;tick();response(0,8'h33,5'h05,128'h98765432);
            end
            if (scenario=="duplicate") response(0,8'h11,5'h03,128'h12345678);
            if (scenario=="reset_masks_error") begin #1;rst_n=0;#1;rst_n=1; end
        end
        // No extra posedge: the last response MUST be checked by explicit flush.
        monitor.finish_checks();
        $display("FIXTURE_DONE scenario=%s failures=%0d",scenario,monitor.failures);
        $finish;
    end
endmodule
