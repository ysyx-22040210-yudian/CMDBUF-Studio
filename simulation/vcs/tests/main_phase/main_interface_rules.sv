// Fill all nine entries under R backpressure, then release selected lanes.
class main_interface_rules extends cmdbuf_main_tc;
    function new(); num_cycles=48; endfunction

    virtual task main_phase;
        string violation;
        if ($value$plusargs("INJECT=%s", violation)) begin end
        for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin
            @(posedge clk);
            #1;
            begin_cycle(cycle);
            rst_n = 1;
            if (cycle < 24 || cycle == 28) rready = 3'b000;
            else if (cycle < 27) rready = 3'b010;
            else rready = 3'b111;
            clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;
            drive_control();
            #1;
            sample_context();

            src = 3; arvalid = 0;
            if (cycle < 7 && arready[0]) begin src = 0; arvalid = 1; end
            if (cycle == 9 && arready[1]) begin src = 1; arvalid = 1; end
            if (cycle == 10 && arready[2]) begin src = 2; arvalid = 1; end
            // Lane 0 has used its quota, while lanes 1 and 2 can still accept.
            if (cycle == 7 && arready[1]) src = 1;
            if (cycle == 8 && violation == "src_ready_idle") src = 0;
            arresp = 0; arid = cycle; ar_port_id = 0;
            araddr = 18'h12001 + (cycle % 8)*8;
            rw_rd_gnt = 8'hff;
            for (int b=0; b<8; b++) begin
                if (req[b] && cycle != 2) ar_rd_gnt[b] = 1;
                else ar_rd_gnt[b] = 0;
                if (ar_rd_gnt[b]) rd_mem_status[b] = 2'b01 << arb_addr[0];
                else rd_mem_status[b] = 0;
            end
            if (cycle == 0 && violation == "grant_without_req") begin
                ar_rd_gnt[7] = 1;
                rw_rd_gnt = 0;
            end
            issue_data = {96'h0123456789abcdef00112233, cycle};
            issue_error = 0;
            if (mem_due) begin mem_rdata = due_data; pc_err = due_error; end
            else begin mem_rdata = 0; pc_err = 0; end
            drive_inputs();
        end
    endtask
endclass
