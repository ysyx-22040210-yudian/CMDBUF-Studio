// Included inside tb_cmdbuf_rd. WaveJSON drives every DUT input literally.
// No free-running clock, implicit reset, automatic busy/AR fixup or tail drain.
integer wave_clock_edges = 0, wave_active_edges = 0, wave_resets = 0;
integer wave_aborted = 0, wave_aborted_unissued = 0, wave_aborted_mem = 0;
integer wave_aborted_response = 0, wave_samples, wave_driven;
longint wave_tick_ps, wave_duration_ps, wave_skew_ps;
reg wave_started = 0;
`include "protocol_driver.svh"

// Reset may arrive while the clock is stopped. Flush only observation history;
// the canonical DUT and reference model implement their own asynchronous reset.
always @(negedge rst_n) if (wave_mode && wave_started) begin
    if (protocol_mode) protocol_reset();
    wave_resets = wave_resets + 1;
    wave_aborted = accepted - retired;
    wave_aborted_unissued = accepted - issued;
    wave_aborted_mem = issued - captured;
    wave_aborted_response = captured - retired;
    due_data.delete(); due_parity.delete();
    $fdisplay(events, "%0d,RESET,-1,0,0,0,0,%0d", cycle, wave_aborted);
end

always @(posedge clk) if (wave_mode && wave_started) begin : wave_observe
    integer b, s, x;
    longint sample_ps, column;
    reg success, returning;
    sample_ps = $realtime * 1000.0;
    column = (sample_ps - wave_skew_ps) / wave_tick_ps;
    // Pre-NBA values are exactly those seen by the native concurrent SVA.
    $fdisplay(wave_samples, "%0d,%0d,%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h",
        cycle, column, sample_ps, clk, rst_n, arvalid, src, araddr, arid, ar_port_id, arresp,
        rready, ar_rd_gnt, rw_rd_gnt, rd_mem_status[0], rd_mem_status[1], rd_mem_status[2],
        rd_mem_status[3], rd_mem_status[4], rd_mem_status[5], rd_mem_status[6], rd_mem_status[7],
        mem_rdata, pc_err, arready, req, arb_addr, pending_cnt, rvalid, rdata, rid, r_port_id, raddr, rresp);
    wave_clock_edges = wave_clock_edges + 1;
    if (protocol_mode) protocol_observe();
    if (rst_n) begin
        wave_active_edges = wave_active_edges + 1;
        did_accept = arvalid && src < 3 && arready[src];
        success = |(req & ar_rd_gnt & rw_rd_gnt);
        returning = |(rvalid & rready);
        if (accepted - retired - wave_aborted == 9) begin
            full = full + 1;
            if (did_accept && returning) full_reuse = full_reuse + 1;
        end
        if (rready == 0 && accepted - retired - wave_aborted > 0) ready_zero = ready_zero + 1;
        for (s = 0; s < 9; s = s + 1)
            if (checks.m_live[s] && checks.m_done[s]) begin
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
            due_data.delete(cycle);
        end
        if (success) begin
            if (!protocol_mode && due_data.exists(cycle+4)) $fatal(1, "ENV_CALENDAR_COLLISION");
            // Calendar tracks timing only. MEM data and pc_err stay user-owned.
            if (!protocol_mode) due_data[cycle+4] = 0;
            for (b = 0; b < 8; b = b + 1)
                if (req[b]) bank_hits[b+8*arb_addr[0]] = bank_hits[b+8*arb_addr[0]] + 1;
            $fdisplay(events, "%0d,ISSUE,-1,%03h,0,0,0,0", cycle, arb_addr);
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
            $fdisplay(events, "%0d,AR,%0d,%05h,%02h,%02h,0,%0d", cycle, src, araddr, arid, ar_port_id, arresp);
            accepted = accepted + 1;
        end
    end
    cycle <= cycle + 1; // Other posedge monitors must still see this edge's index.
end

task automatic run_wavedrom;
    string magic;
    integer rows, require_empty, fields_read, row;
    longint frame_ps, previous_ps;
    reg w_clk, w_rst_n, w_arvalid, w_pc_err;
    reg [1:0] w_src, w_arresp, w_status[0:7];
    reg [17:0] w_araddr;
    reg [7:0] w_arid, w_argrant, w_rwgrant;
    reg [4:0] w_port;
    reg [2:0] w_ready;
    reg [127:0] w_mem;
    begin
        fields_read = $fscanf(fd, "%s %d %d %d %d %d\n", magic, rows, wave_tick_ps,
                             wave_duration_ps, wave_skew_ps, require_empty);
        if (fields_read != 6 || magic != "WAVEDROM_V1" || rows < 1)
            $fatal(1, "Malformed WaveDrom program header");
        wave_samples = $fopen("samples.csv", "w");
        wave_driven = $fopen("driven.csv", "w");
        if (!wave_samples || !wave_driven) $fatal(1, "Cannot open WaveDrom audit files");
        $fdisplay(wave_samples, "cycle,column,time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp");
        $fdisplay(wave_driven, "time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err");
        previous_ps = 0;
        wave_started = 1;
        for (row = 0; row < rows; row = row + 1) begin
            fields_read = $fscanf(fd, "%d %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b\n",
                frame_ps, w_clk, w_rst_n, w_arvalid, w_src, w_araddr, w_arid, w_port, w_arresp,
                w_ready, w_argrant, w_rwgrant, w_status[0], w_status[1], w_status[2], w_status[3],
                w_status[4], w_status[5], w_status[6], w_status[7], w_mem, w_pc_err);
            if (fields_read != 22 || frame_ps < previous_ps) $fatal(1, "Malformed WaveDrom frame %0d", row);
            #((frame_ps - previous_ps) / 1000.0);
            rst_n = w_rst_n; arvalid = w_arvalid; src = w_src; araddr = w_araddr;
            arid = w_arid; ar_port_id = w_port; arresp = w_arresp; rready = w_ready;
            ar_rd_gnt = w_argrant; rw_rd_gnt = w_rwgrant; mem_rdata = w_mem; pc_err = w_pc_err;
            for (integer b = 0; b < 8; b = b + 1) rd_mem_status[b] = w_status[b];
            clk = w_clk;
            $fdisplay(wave_driven, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", frame_ps,
                clk, rst_n, arvalid, src, araddr, arid, ar_port_id, arresp, rready, ar_rd_gnt, rw_rd_gnt,
                rd_mem_status[0], rd_mem_status[1], rd_mem_status[2], rd_mem_status[3],
                rd_mem_status[4], rd_mem_status[5], rd_mem_status[6], rd_mem_status[7], mem_rdata, pc_err);
            previous_ps = frame_ps;
        end
        $fclose(fd);
        #0.001; // Let final event and concurrent assertion actions settle; no clock is added.
        if (require_empty && (accepted != retired + wave_aborted ||
            accepted != issued + wave_aborted_unissued || issued != captured + wave_aborted_mem ||
            captured != retired + wave_aborted_response || due_data.num() != 0 || req != 0 || pending_cnt != 0))
            $fatal(1, "WAVEDROM_PENDING accepted=%0d retired=%0d aborted=%0d", accepted, retired, wave_aborted);
        $fclose(wave_samples); $fclose(wave_driven);
    end
endtask
