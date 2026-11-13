// Four combinations of saved ARRESP and captured PC_ERR.
class main_response_codes extends cmdbuf_main_tc;
    function new(); num_cycles=40; endfunction

    virtual task main_phase;
        for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin
            @(posedge clk);
            begin_cycle(cycle);
            rst_n = 1; rready = 7;
            clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;
            drive_control();
            sample_context();

            arvalid = 0; src = 3; arresp = 0;
            araddr = 18'h12001 + 64 * (cycle / 8);
            arid = 8'h20 + (cycle / 8);
            ar_port_id = 6;
            if (cycle < 32 && cycle % 8 == 0 && arready[0] === 1'b1) begin
                arvalid = 1; src = 0;
                if (cycle >= 16) arresp = 3;
            end

            rw_rd_gnt = 8'hff;
            issue_error = 0;
            for (int bank=0; bank<8; bank++) begin
                ar_rd_gnt[bank] = req[bank];
                rd_mem_status[bank] = 0;
                if (req[bank]) begin
                    rd_mem_status[bank][arb_addr[0]] = 1;
                    issue_error = arb_addr[0];
                end
            end
            issue_data = 128'h0123456789abcdef_fedcba9876543210;
            mem_rdata = 0; pc_err = 0;
            if (mem_due) begin
                mem_rdata = due_data; pc_err = due_error;
            end
            drive_inputs();
        end
    endtask
endclass
