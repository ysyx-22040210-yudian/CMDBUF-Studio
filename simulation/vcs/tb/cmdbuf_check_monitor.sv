`timescale 1ns/1ps
`default_nettype none
// Simulation observer only. No signal is driven, repaired, or inferred from DUT internals.
// Clocking inputs freeze one complete PRE-edge frame. The previous frame is
// consumed at the next edge; finish_checks explicitly consumes the final frame.
module cmdbuf_check_monitor (
    input wire clk, rst_n, arvalid,
    input wire [2:0] arready,
    input wire [17:0] araddr,
    input wire [1:0] src,
    input wire [7:0] arid,
    input wire [4:0] ar_port_id,
    input wire [1:0] arresp,
    input wire [2:0] rvalid, rready,
    input wire [127:0] rdata,
    input wire [7:0] rid,
    input wire [4:0] r_port_id,
    input wire [2:0] raddr,
    input wire [1:0] rresp,
    input wire [7:0] req,
    input wire [11:0] arb_addr,
    input wire [3:0] pending_cnt,
    input wire [7:0] ar_rd_gnt, rw_rd_gnt,
    input wire [1:0] rd_mem_status [7:0],
    input wire [127:0] mem_rdata,
    input wire pc_err
);
    string check_mode = "transaction";
    integer assert_fatal = 1, strict_mode = 0;
    integer failures = 0, incomplete = 0, edge_count = 0, checked_frames = 0;
    integer frames_fd, errors_fd, epoch = 0, model_epoch = -1;
    integer peak_states = 1, state_limit = 4096;
    integer total_ar = 0, total_issue = 0, total_r = 0, total_cancelled = 0;
    bit pending_frame = 0, finished = 0, poisoned = 0, uncertain = 0;

    typedef struct packed {
        longint edge_id, time_ps;
        integer reset_epoch;
        logic rst_n, arvalid;
        logic [2:0] arready;
        logic [17:0] araddr;
        logic [1:0] src;
        logic [7:0] arid;
        logic [4:0] ar_port_id;
        logic [1:0] arresp;
        logic [2:0] rvalid, rready;
        logic [127:0] rdata;
        logic [7:0] rid;
        logic [4:0] r_port_id;
        logic [2:0] raddr;
        logic [1:0] rresp;
        logic [7:0] req;
        logic [11:0] arb_addr;
        logic [3:0] pending_cnt;
        logic [7:0] ar_rd_gnt, rw_rd_gnt;
        logic [7:0][1:0] status;
        logic [127:0] mem_rdata;
        logic pc_err;
    } frame_t;
    frame_t saved;

    // Each hypothesis associates visible issues with accepted request tokens.
    // Multiple requests can have the same visible Bank/address: keep ALL possible
    // associations, never choose a token by comparing the DUT's response ID.
    typedef struct packed {
        logic [8:0] sent, done, pc;
        logic [8:0][127:0] data;
        logic [3:0] due;
        logic [3:0][3:0] slots;
    } state_t;
    state_t states[$], candidates[$];
    bit seen[state_t];
    logic [8:0] live = 0;
    logic [17:0] tx_addr[0:8];
    logic [1:0] tx_src[0:8], tx_err[0:8];
    logic [7:0] tx_id[0:8];
    logic [4:0] tx_port[0:8];
    longint tx_age[0:8], serial = 0;

    always @(negedge rst_n) epoch = epoch + 1;
    clocking sample_cb @(posedge clk);
        default input #1step;
        input rst_n, arvalid, arready, araddr, src, arid, ar_port_id, arresp;
        input rvalid, rready, rdata, rid, r_port_id, raddr, rresp;
        input req, arb_addr, pending_cnt, ar_rd_gnt, rw_rd_gnt;
        input rd_mem_status, mem_rdata, pc_err, epoch;
    endclocking

    task automatic write_summary;
        integer f;
        begin
            f = $fopen("check_runtime.json", "w");
            if (f) begin
                $fdisplay(f, "{\"version\":1,\"mode\":\"%s\",\"assert_fatal\":%0d,\"failures\":%0d,\"incomplete\":%0d,\"finished\":%0d,\"sampled_frames\":%0d,\"checked_frames\":%0d,\"outstanding\":%0d,\"accepted\":%0d,\"issued\":%0d,\"returned\":%0d,\"cancelled\":%0d,\"peak_hypotheses\":%0d,\"hypothesis_limit\":%0d}",
                    check_mode, assert_fatal, failures, incomplete, finished,
                    edge_count, checked_frames, $countones(live), total_ar, total_issue,
                    total_r, total_cancelled, peak_states, state_limit);
                $fclose(f);
            end
        end
    endtask

    task automatic fail_check(input string label, input longint at_edge,
                              input longint at_time, input string detail);
        begin
            failures++;
            $fdisplay(errors_fd, "{\"label\":\"%s\",\"edge\":%0d,\"time_ps\":%0d,\"detail\":\"%s\"}", label, at_edge, at_time, detail);
            $fflush(errors_fd);
            $display("SVA_FAIL %s edge=%0d time_ps=%0d %s", label, at_edge, at_time, detail);
            write_summary();
            if (assert_fatal) $fatal(1, "CHECK_STOP %s edge=%0d", label, at_edge);
            else $error("CHECK_CONTINUE %s edge=%0d", label, at_edge);
        end
    endtask

    // Native SVA has its own Preponed sampling. Its action passes sampled data,
    // so no expression is re-evaluated using post-NBA DUT/reference values.
    task automatic native_failure(input string label, input longint at_time, input string detail);
        fail_check(label, edge_count, at_time, detail);
    endtask

    task automatic require_check(input logic good, input string label,
                                 input frame_t f, input string detail);
        assert (good === 1'b1) else fail_check(label, f.edge_id, f.time_ps, detail);
    endtask

    task automatic mark_incomplete(input string reason);
        begin
            incomplete++;
            $display("CHECK_INCOMPLETE %s edge=%0d", reason, edge_count);
        end
    endtask

    task automatic reset_transactions;
        state_t empty_state;
        begin
            total_cancelled += $countones(live);
            live = 0; poisoned = 0; uncertain = 0;
            states.delete(); candidates.delete(); seen.delete();
            empty_state = '0; states.push_back(empty_state);
        end
    endtask

    task automatic add_candidate(input state_t value);
        begin
            if (!uncertain && !seen.exists(value)) begin
                if (candidates.size() >= state_limit) begin
                    uncertain = 1;
                    mark_incomplete("ISSUE_IDENTITY_LIMIT: visible interface remains ambiguous");
                end else begin
                    seen[value] = 1;
                    candidates.push_back(value);
                    if (candidates.size() > peak_states) peak_states = candidates.size();
                end
            end
        end
    endtask

    task automatic check_transactions(input frame_t f);
        integer head, lane, alloc, bank, match_count;
        bit af, rf, issue;
        logic [1:0] expected_resp;
        state_t s, branch;
        begin
            af = f.arvalid && f.src < 3 && f.arready[f.src];
            rf = |(f.rvalid & f.rready);
            issue = |(f.req & f.ar_rd_gnt & f.rw_rd_gnt);
            lane = -1; head = -1; alloc = -1; bank = -1;
            for (integer x=0; x<3; x++) if (f.rvalid[x] && f.rready[x]) lane=x;
            for (integer b=0; b<8; b++) if (f.req[b]) bank=b;
            if (rf) begin
                for (integer e=0; e<9; e++)
                    if (live[e] && tx_src[e]==lane && (head<0 || tx_age[e]<tx_age[head])) head=e;
                require_check(head>=0, "T_R_HAS_PENDING", f, $sformatf("source=%0d", lane));
                if (head>=0) begin
                    require_check(f.rid==tx_id[head], "T_R_ID_ORDER", f, $sformatf("actual=%h expected=%h token=%0d", f.rid,tx_id[head],tx_age[head]));
                    require_check(f.r_port_id==tx_port[head], "T_R_PORT_ORDER", f, $sformatf("actual=%h expected=%h", f.r_port_id,tx_port[head]));
                    require_check(f.raddr==tx_addr[head][2:0], "T_R_ADDR_ORDER", f, $sformatf("actual=%h expected=%h", f.raddr,tx_addr[head][2:0]));
                end
            end
            // Complete the N+4 MEM capture before considering an observed R.
            // Generic mode permits capture-edge forwarding; baseline N+5 and RR
            // scheduling remain explicit STRICT checks, not inferred latencies.
            if (!poisoned && !uncertain) begin
                candidates.delete(); seen.delete();
                foreach (states[i]) begin
                    s=states[i];
                    if (s.due[3]) begin
                        s.done[s.slots[3]]=1;
                        s.data[s.slots[3]]=f.mem_rdata; s.pc[s.slots[3]]=f.pc_err;
                    end
                    s.due={s.due[2:0],1'b0}; s.slots={s.slots[2:0],4'd0};
                    if (rf) begin
                        if (head<0) continue;
                        expected_resp=tx_err[head]==3 ? 2'b11 : (s.pc[head] ? 2'b01 : 2'b00);
                        if (!s.done[head] || f.rdata !== s.data[head] || f.rresp !== expected_resp) continue;
                        s.sent[head]=0; s.done[head]=0; s.pc[head]=0; s.data[head]=0;
                    end
                    add_candidate(s);
                end
                if (candidates.size()==0 && !uncertain) begin
                    require_check(0, "T_R_DATA_RESP_OR_EARLY", f, $sformatf("source=%0d data=%h resp=%h no consistent accepted/issued transaction",lane,f.rdata,f.rresp));
                    poisoned=1;
                end
                states=candidates;
            end
            if (rf && head>=0) begin live[head]=0; total_r++; end
            if (af) begin
                for (integer e=0; e<9; e++) if (!live[e] && alloc<0) alloc=e;
                require_check(alloc>=0, "T_CAPACITY", f, "accepted beyond nine outstanding transactions");
                if (alloc>=0) begin
                    live[alloc]=1; tx_src[alloc]=f.src; tx_addr[alloc]=f.araddr;
                    tx_id[alloc]=f.arid; tx_port[alloc]=f.ar_port_id;
                    tx_err[alloc]=f.arresp; tx_age[alloc]=serial; serial++; total_ar++;
                end else poisoned=1;
            end
            if (issue) total_issue++;
            if (!poisoned && !uncertain) begin
                candidates.delete(); seen.delete();
                foreach (states[i]) begin
                    s=states[i];
                    if (alloc>=0) begin s.sent[alloc]=0; s.done[alloc]=0; s.pc[alloc]=0; s.data[alloc]=0; end
                    if (bank<0) add_candidate(s);
                    else begin
                        match_count=0;
                        for (integer e=0; e<9; e++)
                            if (live[e] && !s.sent[e] && tx_addr[e][5:3]==bank && tx_addr[e][17:6]==f.arb_addr) begin
                                match_count++;
                                if (issue) begin
                                    branch=s; branch.sent[e]=1; branch.due[0]=1; branch.slots[0]=e;
                                    add_candidate(branch);
                                end
                            end
                        if (!issue && match_count>0) add_candidate(s);
                    end
                end
                if (candidates.size()==0 && !uncertain) begin
                    require_check(0, "T_REQ_HAS_ACCEPTED", f, $sformatf("req=%h addr=%h no unissued accepted request",f.req,f.arb_addr));
                    poisoned=1;
                end
                states=candidates;
            end
        end
    endtask

    task automatic consume_frame;
        bit known;
        begin
            if (pending_frame) begin
                pending_frame=0; checked_frames++;
                // A reset never erases a completed frame or a recorded failure.
                if (saved.reset_epoch!=model_epoch) begin
                    reset_transactions(); model_epoch=saved.reset_epoch;
                end
                if (saved.rst_n===1'b0) reset_transactions();
                else begin
                    known=!$isunknown({saved.rst_n,saved.arvalid,saved.arready,saved.src,saved.araddr,saved.arid,
                        saved.ar_port_id,saved.arresp,saved.rready,saved.ar_rd_gnt,saved.rw_rd_gnt,saved.status,
                        saved.mem_rdata,saved.pc_err,saved.req,saved.rvalid,saved.pending_cnt});
                    if (saved.req!=0 && $isunknown(saved.arb_addr)) known=0;
                    if (saved.rvalid!=0 && $isunknown({saved.rdata,saved.rid,saved.r_port_id,saved.raddr,saved.rresp})) known=0;
                    require_check(known, "P_KNOWN_FRAME", saved, "unknown active interface value; four-state snapshot retained");
                    if (!strict_mode && known) check_transactions(saved);
                    else if (!known) poisoned=1;
                end
            end
        end
    endtask

    always @(sample_cb) begin
        consume_frame();
        edge_count++;
        saved.edge_id=edge_count; saved.time_ps=$realtime/1ps; saved.reset_epoch=sample_cb.epoch;
        saved.rst_n=sample_cb.rst_n; saved.arvalid=sample_cb.arvalid; saved.arready=sample_cb.arready;
        saved.araddr=sample_cb.araddr; saved.src=sample_cb.src; saved.arid=sample_cb.arid;
        saved.ar_port_id=sample_cb.ar_port_id; saved.arresp=sample_cb.arresp;
        saved.rvalid=sample_cb.rvalid; saved.rready=sample_cb.rready; saved.rdata=sample_cb.rdata;
        saved.rid=sample_cb.rid; saved.r_port_id=sample_cb.r_port_id; saved.raddr=sample_cb.raddr; saved.rresp=sample_cb.rresp;
        saved.req=sample_cb.req; saved.arb_addr=sample_cb.arb_addr; saved.pending_cnt=sample_cb.pending_cnt;
        saved.ar_rd_gnt=sample_cb.ar_rd_gnt; saved.rw_rd_gnt=sample_cb.rw_rd_gnt;
        for (integer b=0;b<8;b++) saved.status[b]=sample_cb.rd_mem_status[b];
        saved.mem_rdata=sample_cb.mem_rdata; saved.pc_err=sample_cb.pc_err;
        pending_frame=1;
        $fdisplay(frames_fd,"%0d,%0d,%0d,%b,%b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h",
            saved.edge_id,saved.time_ps,saved.reset_epoch,saved.rst_n,saved.arvalid,saved.arready,saved.araddr,
            saved.src,saved.arid,saved.ar_port_id,saved.arresp,saved.rvalid,saved.rready,saved.rdata,saved.rid,
            saved.r_port_id,saved.raddr,saved.rresp,saved.req,saved.arb_addr,saved.pending_cnt,
            saved.ar_rd_gnt,saved.rw_rd_gnt,saved.status,saved.mem_rdata,saved.pc_err);
        $fflush(frames_fd);
    end

    task automatic finish_checks;
        begin
            consume_frame();
            // Account for an asynchronous reset after the last sampling edge.
            if (epoch!=model_epoch) begin reset_transactions(); model_epoch=epoch; end
            if (!strict_mode && live!=0) mark_incomplete("OUTSTANDING_TRANSACTIONS at end of observation");
            finished=1; write_summary();
            $display("CHECK_COMPLETE mode=%s failures=%0d incomplete=%0d frames=%0d checked=%0d",check_mode,failures,incomplete,edge_count,checked_frames);
        end
    endtask

    initial begin
        if ($value$plusargs("CHECK_MODE=%s",check_mode)) begin end
        if ($value$plusargs("ASSERT_FATAL=%d",assert_fatal)) begin end
        if ($value$plusargs("CHECK_STATE_LIMIT=%d",state_limit)) begin end
        if (!(check_mode=="strict" || check_mode=="transaction") || !(assert_fatal==0 || assert_fatal==1) || state_limit<1)
            $fatal(1,"CHECK_CONFIGURATION_INVALID");
        strict_mode=(check_mode=="strict");
        frames_fd=$fopen("check_frames.csv","w"); errors_fd=$fopen("assertions.jsonl","w");
        if (!frames_fd || !errors_fd) $fatal(1,"CHECK_ARCHIVE_OPEN");
        $fdisplay(frames_fd,"edge,time_ps,reset_epoch,rst_n,arvalid,arready,araddr,src,arid,ar_port_id,arresp,rvalid,rready,rdata,rid,r_port_id,raddr,rresp,req,arb_addr,pending_cnt,ar_rd_gnt,rw_rd_gnt,status,mem_rdata,pc_err");
        reset_transactions();
        $display("CHECK_POLICY mode=%s assert_fatal=%0d sampling=pre_edge snapshot_pipeline=1",check_mode,assert_fatal);
    end
    final begin
        write_summary();
        if (frames_fd) $fclose(frames_fd);
        if (errors_fd) $fclose(errors_fd);
    end
endmodule
`default_nettype wire
