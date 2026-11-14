`timescale 1ns/1ps
`default_nettype none
// Legacy sequences use falling-edge drive. WaveJSON controls its own clock.
module tb_cmdbuf_rd;
    // Explicit legacy waveform playback supplies its own first transition.
    reg clk = !$test$plusargs("WAVEDROM");
    initial if (!$test$plusargs("WAVEDROM") && !$test$plusargs("SV_TC")) forever #5 clk = ~clk;
    reg rst_n;
    // The ordinary SV/sequence environment owns the startup reset. #1 is 1 ns.
    // Legacy explicit-waveform playback retains the levels recorded in its file.
    initial if (!$test$plusargs("WAVEDROM")) begin
        rst_n = 0;
        #1 rst_n = 1;
    end
    reg wave_mode = 0;
    reg arvalid = 0;
    wire [2:0] arready;
    reg [17:0] araddr = 0;
    reg [1:0] src = 2'd3;
    reg [7:0] arid = 0;
    reg [4:0] ar_port_id = 0;
    reg [1:0] arresp = 0;
    wire [2:0] rvalid;
    reg [2:0] rready = 0;
    wire [127:0] rdata;
    wire [7:0] rid;
    wire [4:0] r_port_id;
    wire [2:0] raddr;
    wire [1:0] rresp;
    wire [7:0] req;
    wire [11:0] arb_addr;
    wire [3:0] pending_cnt;
    reg [7:0] ar_rd_gnt = 0, rw_rd_gnt = 0;
    reg [1:0] rd_mem_status [7:0];
    reg [127:0] mem_rdata = 0;
    reg pc_err = 0;
    `include "dut_instance.svh"

    string inject = "";
    wire [7:0] checked_rid = (inject == "rid" && |rvalid) ? (rid ^ 8'h01) : rid;
    // Regression fault: expose the former parity response to both checkers.
    wire [1:0] checked_rresp = (inject == "rresp_legacy" && |rvalid && rresp == 2'b10) ? 2'b01 : rresp;
    // Fault injection for early (lookahead) and late (extra register) counts.
    reg [3:0] delayed_pending;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) delayed_pending <= 0;
        else delayed_pending <= pending_cnt;
    wire pending_ar_fire = arvalid && ((src < 3) ? arready[src] : 1'b0);
    wire pending_issue_fire = |(req & ar_rd_gnt & rw_rd_gnt);
    wire [3:0] lookahead_pending = pending_cnt + {3'b0, pending_ar_fire} - {3'b0, pending_issue_fire};
    wire [3:0] checked_pending = inject == "pending_lookahead" ? lookahead_pending :
                                 inject == "pending_delayed" ? delayed_pending : pending_cnt;
    wire [2:0] checked_rvalid = (inject == "rvalid_unready" && !rready[0]) ? (rvalid | 3'b001) : rvalid;
    cmdbuf_check_monitor monitor (.rid(checked_rid), .rvalid(checked_rvalid), .rresp(checked_rresp),
                                  .pending_cnt(checked_pending), .*);
    // The canonical Formal properties remain unchanged. The simulation copy
    // selects common/full scope and sends sampled failures to the monitor.
    cmdbuf_rd_port_sva checks (.rid(checked_rid), .rvalid(checked_rvalid), .rresp(checked_rresp),
                               .pending_cnt(checked_pending), .*);
    `include "functional_properties.svh"

    integer cycle = 0, seed = 1, accepted = 0, issued = 0, retired = 0;
    integer captured = 0, issue_failed = 0, ar_rejected = 0, ar_gated = 0;
    integer ar_only = 0, rw_only = 0, full = 0, full_reuse = 0;
    integer ready_zero = 0, head_blocked = 0;
    integer src_hits[0:2], resp_hits[0:3], bank_hits[0:15];
    reg [127:0] due_data [integer];
    reg due_parity [integer];
    integer events, fd, metrics, n, mode, count, offer, source, parity;
    integer address, ident, portnum, response, ready_mask, status_mask, ag, rg;
    integer drain_limit = 200, global_limit = 10000000;
    reg did_accept;
    string program_path, wave_path;
    `include "environment_policy.svh"
    `include "wavedrom_driver.svh"
    `include "constrained_random_driver.svh"
    `include "main_phase_driver.svh"

    function automatic [127:0] memory_pattern(input integer serial, input [11:0] addr);
        reg [31:0] a, b, c, d;
        begin
            // Seed and transaction serial are recorded; every issued transfer has
            // a distinct deterministic 128-bit payload, including repeated IDs.
            a = 32'h81000000 ^ (serial * 32'h9e3779b9) ^ seed;
            b = 32'h1234abcd ^ (serial * 32'h7f4a7c15) ^ {20'd0, addr};
            c = 32'hfedc9876 ^ (serial * 32'h6c8e9cf5) ^ (seed << 7);
            d = 32'h55aa00ff ^ (serial * 32'h85ebca6b) ^ (seed >> 3);
            memory_pattern = {d, c, b, a};
        end
    endfunction

    // Native E03--E06 check sampled input contracts; monitor keeps four-state
    // snapshots. ENV_FATAL selects abort or diagnostic continuation for TC errors.

    task automatic drain_failure(input string reason);
        if (monitor.strict_mode) monitor.fail_check("A_DRAIN_COMPLETION", monitor.edge_count, $realtime/1ps, reason);
        else monitor.mark_incomplete(reason);
    endtask

    task automatic step(input integer v, sx, addr, idnum, pn, resp,
                        ready, status, argrant, rwgrant, pe);
        integer b, x, s;
        reg success, returning;
        begin
            @(negedge clk);
            if (strict_random_mode) begin random_started=1; random_phase=0; end
            arvalid = 0;
            src = 2'd3; araddr = addr; arid = idnum; ar_port_id = pn; arresp = resp;
            rready = ready; rw_rd_gnt = rwgrant;
            // JSON status bit [b] = even, [8+b] = odd, matching the existing adapter.
            for (b = 0; b < 8; b = b + 1)
                rd_mem_status[b] = {status[8+b], status[b]};
            // A MEM response has no valid pin. Drive its payload on exactly N+4;
            // poison every other cycle to expose an incorrect capture edge.
            if (due_data.exists(cycle)) begin
                mem_rdata = due_data[cycle]; pc_err = due_parity[cycle];
            end else begin
                mem_rdata = {32'hdeadc0de, 32'hbaadf00d, 32'h55555555, 32'haaaaaaaa} ^ cycle;
                pc_err = cycle[0];
            end
            #1;
            ar_rd_gnt = argrant[7:0] & req;
            for (b = 0; b < 8; b = b + 1)
                if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b])
                    rd_mem_status[b][arb_addr[0]] = (inject == "busy") ? 0 : 1;
            #1;
            // A sequence command keeps its source and waits for that lane.
            arvalid = v && arready[sx];
            if (arvalid) src = sx;
            if (v && !arvalid) ar_gated = ar_gated + 1;
            if (inject == "src" && arvalid) src = 3;
            if (strict_random_mode) random_record_directed(memory_pattern(issued,arb_addr),pe);
            observe_sequence(pe,memory_pattern(issued,arb_addr));
        end
    endtask

    task automatic observe_sequence(input integer pe,input reg [127:0] issued_payload);
        begin
            @(posedge clk);
            observe_active(pe,issued_payload);
        end
    endtask

    task automatic observe_active(input integer pe,input reg [127:0] issued_payload);
        integer b,x,s;
        reg success,returning;
        begin
            did_accept = arvalid && src < 3 && arready[src];
            success = |(req & ar_rd_gnt & rw_rd_gnt);
            returning = |(rvalid & rready);
            if (cycle >= global_limit) env_fail("GLOBAL_WATCHDOG","simulation cycle safety bound exceeded",1);
            if (accepted - retired - wave_aborted == 9) begin
                full = full + 1;
                if (did_accept && returning) full_reuse = full_reuse + 1;
            end
            if (rready == 0 && accepted > retired) ready_zero = ready_zero + 1;
            // Read checker state for functional coverage only, never stimulus.
            for (s = 0; s < 9; s = s + 1)
                if (monitor.strict_mode && checks.m_live[s] && checks.m_done[s]) begin
                    x = checks.m_src[s];
                    if (!checks.m_done[checks.head[x]]) head_blocked = head_blocked + 1;
                end
            if (arvalid && !did_accept) ar_rejected = ar_rejected + 1;
            if ((|req) && !success) begin
                issue_failed = issue_failed + 1;
                if (|(req & ar_rd_gnt)) ar_only = ar_only + 1;
                if (|(req & rw_rd_gnt)) rw_only = rw_only + 1;
            end
            if (due_data.exists(cycle)) begin
                captured = captured + 1;
                $fdisplay(events, "%0d,MEM,-1,0,0,0,%032h,%0d", cycle, mem_rdata, pc_err);
                due_data.delete(cycle); due_parity.delete(cycle);
            end
            if (success) begin
                if (due_data.exists(cycle+4)) env_fail("ENV_CALENDAR_COLLISION","cannot schedule two MEM values at one edge",1);
                due_data[cycle+4] = issued_payload;
                due_parity[cycle+4] = pe;
                for (b = 0; b < 8; b = b + 1)
                    if (req[b]) bank_hits[b+8*arb_addr[0]] = bank_hits[b+8*arb_addr[0]] + 1;
                $fdisplay(events, "%0d,ISSUE,-1,%03h,0,0,%032h,%0d", cycle, arb_addr,
                          due_data[cycle+4], pe);
                issued = issued + 1;
            end
            if (returning) begin
                for (x = 0; x < 3; x = x + 1) if (rvalid[x]) begin
                    src_hits[x] = src_hits[x] + 1;
                    $fdisplay(events, "%0d,R,%0d,%01h,%02h,%02h,%032h,%0d",
                              cycle, x, raddr, rid, r_port_id, rdata, rresp);
                end
                resp_hits[rresp] = resp_hits[rresp] + 1;
                retired = retired + 1;
            end
            if (did_accept) begin
                $fdisplay(events, "%0d,AR,%0d,%05h,%02h,%02h,0,%0d",
                          cycle, src, araddr, arid, ar_port_id, arresp);
                accepted = accepted + 1;
            end
            // Main-phase drive waits for Observed in the same time slot.
            // Legacy sequence/solver drivers keep their original delay.
            if (!sv_tc_mode) #1;
            cycle = cycle + 1;
        end
    endtask

    initial begin
        env_open();
        wave_mode = $test$plusargs("WAVEDROM");
        protocol_mode = $test$plusargs("PROTOCOL");
        strict_random_mode = $test$plusargs("STRICT_RANDOM");
        sv_tc_mode = $test$plusargs("SV_TC");
        for (n = 0; n < 8; n = n + 1) rd_mem_status[n] = 0;
        for (n = 0; n < 3; n = n + 1) src_hits[n] = 0;
        for (n = 0; n < 4; n = n + 1) resp_hits[n] = 0;
        for (n = 0; n < 16; n = n + 1) bank_hits[n] = 0;
        if (!$value$plusargs("PROGRAM=%s", program_path)) env_fail("ENV_MISSING_PROGRAM","missing PROGRAM plusarg",1);
        if (!$value$plusargs("SEED=%d", seed)) seed = 1;
        if (strict_random_mode) random_open();
        if ($value$plusargs("INJECT=%s", inject)) $display("SELFTEST injection=%s", inject);
        if ($value$plusargs("FSDB=%s", wave_path)) begin
            $fsdbDumpfile(wave_path);
            $fsdbDumpvars(0, tb_cmdbuf_rd, "+all");
            $fsdbDumpMDA();
        end
        events = $fopen("events.csv", "w");
        if (!events) env_fail("ENV_ARCHIVE_OPEN","events.csv",1);
        $fdisplay(events, "cycle,event,src,addr_hex,id_hex,port_hex,data_hex,status");
        fd = $fopen(program_path, "r");
        if (!fd) env_fail("ENV_PROGRAM_OPEN",program_path,1);
        if (sv_tc_mode) begin
            run_sv_tc();
        end else if (protocol_mode) begin
            run_protocol();
        end else if (wave_mode) begin
            run_wavedrom();
        end else begin
        // Wait for the shared 1 ns reset pulse before preparing the first edge.
        wait (rst_n === 1'b1);
        while (!$feof(fd)) begin
            n = $fscanf(fd, "%d %d %d %d %h %h %h %h %h %h %h %h %d\n",
                        mode, count, offer, source, address, ident, portnum, response,
                        ready_mask, status_mask, ag, rg, parity);
            if (n == -1) break;
            if (n != 13 || count < 1 || !(mode == 0 || mode == 1 || mode == 2))
                env_fail("ENV_BAD_INSTRUCTION",$sformatf("fields=%0d",n),1);
            for (integer j = 0; j < count; j = j + 1) begin
                if (mode == 2) begin
                    if (!strict_random_mode || source != seed) env_fail("RND_PROGRAM_SEED_MISMATCH","random program mode or seed differs",1);
                    constrained_random_step(2);
                end else step(offer, source, address, ident, portnum, response,
                     ready_mask, status_mask, ag, rg, parity);
                if (mode == 1 && did_accept) break;
            end
            if (mode == 1 && !did_accept) env_fail("SEND_TIMEOUT",$sformatf("cycle=%0d",cycle));
        end
        $fclose(fd);
        for (n = 0; n < drain_limit &&
             (accepted != retired || due_data.num() != 0 || req != 0 || (monitor.strict_mode && pending_cnt != 0));
             n = n + 1)
            if (strict_random_mode) constrained_random_step(3);
            else step(0, 0, 0, 0, 0, 0, 7, 0, 255, 255, 0);
        if (accepted != retired || accepted != issued || issued != captured ||
            due_data.num() != 0 || req != 0 || (monitor.strict_mode && pending_cnt != 0))
            drain_failure($sformatf("DRAIN_FAILED accepted=%0d issued=%0d captured=%0d retired=%0d", accepted, issued, captured, retired));
        repeat (8) begin
            if (strict_random_mode) constrained_random_step(4);
            else step(0, 0, 0, 0, 0, 0, 7, 0, 255, 255, 0);
        end
        if (strict_random_mode) begin $fclose(random_samples); $fclose(random_solutions); end
        end
        // Let the final sampled property actions finish before closing evidence.
        #1ps;
        monitor.finish_checks();
        metrics = $fopen("metrics.json", "w");
        if (!metrics) env_fail("ENV_ARCHIVE_OPEN","metrics.json",1);
        $fdisplay(metrics, "{");
        $fdisplay(metrics, "\"tc_assignments\":%0d,\"tc_body_cycles\":%0d,\"tc_active_edges\":%0d,",sv_frames,sv_body_slots,sv_active);
        $fdisplay(metrics, "\"random_cycles\":%0d,\"random_solves\":%0d,", random_cycles,random_solves);
        $fdisplay(metrics, "\"submitted\":%0d,\"queued_cancelled\":%0d,\"queue_pending\":%0d,",p_submitted,p_qcancel,p_queue.size());
        $fdisplay(metrics, "\"seed\":%0d,\"cycles\":%0d,\"accepted\":%0d,\"issued\":%0d,\"captured\":%0d,\"retired\":%0d,", seed, cycle, accepted, issued, captured, retired);
        $fdisplay(metrics, "\"clock_edges\":%0d,\"active_edges\":%0d,\"reset_assertions\":%0d,\"aborted\":%0d,", wave_mode ? wave_clock_edges : cycle, sv_tc_mode ? sv_active : wave_mode ? wave_active_edges : cycle, wave_resets, wave_aborted);
        $fdisplay(metrics, "\"aborted_unissued\":%0d,\"aborted_mem\":%0d,\"aborted_response\":%0d,\"pending_at_end\":%0d,", wave_aborted_unissued, wave_aborted_mem, wave_aborted_response, accepted-retired-wave_aborted);
        $fdisplay(metrics, "\"issue_failed\":%0d,\"ar_rejected\":%0d,\"ar_gated\":%0d,\"ar_only\":%0d,\"rw_only\":%0d,", issue_failed, ar_rejected, ar_gated, ar_only, rw_only);
        $fdisplay(metrics, "\"full\":%0d,\"full_reuse\":%0d,\"ready_zero\":%0d,\"head_blocked\":%0d,", full, full_reuse, ready_zero, head_blocked);
        $fdisplay(metrics, "\"src0\":%0d,\"src1\":%0d,\"src2\":%0d,\"resp0\":%0d,\"resp1\":%0d,\"resp2\":%0d,\"resp3\":%0d,",
            src_hits[0], src_hits[1], src_hits[2], resp_hits[0], resp_hits[1], resp_hits[2], resp_hits[3]);
        $fwrite(metrics, "\"bank_half_hits\":[");
        for (n = 0; n < 16; n = n + 1) $fwrite(metrics, "%s%0d", n ? "," : "", bank_hits[n]);
        $fdisplay(metrics, "]}");
        $fclose(metrics); $fclose(events);
        env_finished=1; env_write_summary();
        $display("ENV_RUN_COMPLETE failures=%0d",env_failures);
        $display("FUNCTIONAL_COMPLETE");
        if (env_failures) $display("TEST_FAIL seed=%0d environment_errors=%0d assertions=%0d",seed,env_failures,monitor.failures);
        else if (monitor.failures) $display("TEST_FAIL seed=%0d assertions=%0d", seed, monitor.failures);
        else if (monitor.incomplete) $display("TEST_INCOMPLETE seed=%0d reasons=%0d", seed, monitor.incomplete);
        else $display("TEST_PASS seed=%0d accepted=%0d retired=%0d cycles=%0d", seed, accepted, retired, cycle);
        $finish;
    end
endmodule
`default_nettype wire
