// VCS constraint solver owns the final random pin values. No post-solve fixup.
// rready is solved first because it changes the DUT's same-cycle arready.
class cmdbuf_random_ready;
    rand logic [2:0] value;
    logic drain;
    constraint ready_policy { if (drain) value == 3'b111; }
endclass

class cmdbuf_random_inputs;
    rand logic valid;
    rand logic [1:0] source, response;
    rand logic [17:0] address;
    rand logic [7:0] ident, argrant, rwgrant;
    rand logic [4:0] portnum;
    rand logic [1:0] bank_status[8];
    rand logic [127:0] mem_data, next_data;
    rand logic mem_error, next_error;
    logic [2:0] ready_context;
    logic [7:0] req_context;
    logic half_context, mem_due, due_error, drain, contradiction;
    logic [127:0] due_payload;

    constraint input_contracts {
        solve valid before source, response;
        solve argrant, rwgrant before bank_status;
        // E03/E04 apply when valid. Inactive fields retain their full bit range.
        if (valid) { source inside {0,1,2}; response inside {0,3}; }
        // E05: this is an input to the solve, never a mask applied afterwards.
        if (ready_context == 0) valid == 0;
        // E06: jointly solve grants and all 16 bank/half status bits.
        foreach (bank_status[b])
            if (req_context[b] && argrant[b] && rwgrant[b])
                bank_status[b][half_context] == 1;
        // The memory has no valid pin. Only a previously booked N+4 payload
        // is fixed; other cycles retain unconstrained random 128-bit data.
        if (mem_due) { mem_data == due_payload; mem_error == due_error; }
        // Explicit completion phase, excluded from random-phase statistics.
        if (drain) {
            valid == 0; argrant == 8'hff; rwgrant == 8'hff;
            foreach (bank_status[b])
                if (req_context[b]) bank_status[b] == (2'b01 << half_context);
                else bank_status[b] == 0;
        }
        // Acceptance-test-only unsatisfiable constraint; no retry or fallback.
        if (contradiction) { valid == 0; valid == 1; }
    }
endclass

reg strict_random_mode = 0, random_started = 0;
integer random_phase = 0, random_samples, random_solutions;
integer random_cycles = 0, random_solves = 0;
cmdbuf_random_ready random_ready;
cmdbuf_random_inputs random_inputs;

task automatic random_open;
    begin
        random_ready = new(); random_inputs = new();
        random_ready.srandom(seed ^ 32'h51a7c0de);
        random_inputs.srandom(seed);
        random_samples = $fopen("samples.csv", "w");
        random_solutions = $fopen("random_solutions.csv", "w");
        if (!random_samples || !random_solutions) $fatal(1,"RND_ARCHIVE_OPEN");
        $fdisplay(random_samples,"cycle,phase,time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp");
        $fdisplay(random_solutions,"cycle,phase,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,next_data,next_error");
    end
endtask

// Record solver values, independently of the actual port sampler below.
task automatic random_record_solution;
    begin
        $fdisplay(random_solutions,"%0d,%0d,1,1,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%032h,%0d",
            cycle,random_phase,random_inputs.valid,random_inputs.source,random_inputs.address,
            random_inputs.ident,random_inputs.portnum,random_inputs.response,random_ready.value,
            random_inputs.argrant,random_inputs.rwgrant,random_inputs.bank_status[0],
            random_inputs.bank_status[1],random_inputs.bank_status[2],random_inputs.bank_status[3],
            random_inputs.bank_status[4],random_inputs.bank_status[5],random_inputs.bank_status[6],
            random_inputs.bank_status[7],random_inputs.mem_data,random_inputs.mem_error,
            random_inputs.ready_context,random_inputs.req_context,arb_addr,
            random_inputs.next_data,random_inputs.next_error);
    end
endtask

// Mixed sequence cases may contain explicitly directed steps. They are labelled
// separately; they must not be counted as constraint-randomized samples.
task automatic random_record_directed(input logic [127:0] future_data,input integer future_error);
    begin
        $fdisplay(random_solutions,"%0d,0,1,1,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%032h,%0d",
            cycle,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,
            rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],
            rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,
            arready,req,arb_addr,future_data,future_error);
    end
endtask

always @(posedge clk) if (strict_random_mode && rst_n && random_started) begin
    $fdisplay(random_samples,"%0d,%0d,%0.0f,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h",
        cycle,random_phase,$realtime*1000.0,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,
        rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],
        rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],
        mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp);
end

task automatic constrained_random_step(input integer phase);
    begin
        @(negedge clk);
        random_phase=phase; random_started=1;
        random_ready.drain=(phase!=2);
        if (!random_ready.randomize()) $fatal(1,"RND_READY_SOLVE_FAILED cycle=%0d",cycle);
        rready=random_ready.value;
        #1; // rready -> R retirement -> same-cycle capacity/AR ready must settle.
        if ($isunknown({arready,req}) || ((|req) && $isunknown(arb_addr)))
            $fatal(1,"RND_UNKNOWN_CONTEXT cycle=%0d",cycle);
        random_inputs.ready_context=arready; random_inputs.req_context=req;
        random_inputs.half_context=(|req) ? arb_addr[0] : 0;
        random_inputs.mem_due=due_data.exists(cycle);
        random_inputs.due_payload=due_data.exists(cycle) ? due_data[cycle] : 0;
        random_inputs.due_error=due_parity.exists(cycle) ? due_parity[cycle] : 0;
        random_inputs.drain=(phase!=2);
        random_inputs.contradiction=(inject=="random_unsat");
        if (!random_inputs.randomize()) $fatal(1,"RND_SOLVE_FAILED cycle=%0d",cycle);
        random_solves=random_solves+1;
        if (phase==2) random_cycles=random_cycles+1;
        random_record_solution();
        // Copy the final satisfying assignment exactly once. There is no mask,
        // OR of busy bits, channel substitution or discard/retry of this solve.
        arvalid=random_inputs.valid; src=random_inputs.source; araddr=random_inputs.address;
        arid=random_inputs.ident; ar_port_id=random_inputs.portnum; arresp=random_inputs.response;
        ar_rd_gnt=random_inputs.argrant; rw_rd_gnt=random_inputs.rwgrant;
        for (integer b=0;b<8;b=b+1) rd_mem_status[b]=random_inputs.bank_status[b];
        mem_rdata=random_inputs.mem_data; pc_err=random_inputs.mem_error;
        if (inject=="random_post") arid=arid ^ 8'h01;
        #1;
        if ({arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err} !==
            {random_inputs.valid,random_inputs.source,random_inputs.address,random_inputs.ident,
             random_inputs.portnum,random_inputs.response,random_ready.value,
             random_inputs.argrant,random_inputs.rwgrant,random_inputs.mem_data,random_inputs.mem_error})
            $fatal(1,"RND_POST_SOLVE_MODIFICATION cycle=%0d",cycle);
        for (integer b=0;b<8;b=b+1)
            if (rd_mem_status[b] !== random_inputs.bank_status[b])
                $fatal(1,"RND_POST_SOLVE_MODIFICATION cycle=%0d bank=%0d",cycle,b);
        if (arready !== random_inputs.ready_context || req !== random_inputs.req_context)
            $fatal(1,"RND_CONTEXT_CHANGED cycle=%0d",cycle);
        observe_sequence(random_inputs.next_error,random_inputs.next_data);
    end
endtask
