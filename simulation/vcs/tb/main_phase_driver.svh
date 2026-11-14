// Main-phase stimulus is driven after sampling, in the rising edge's time slot.
// A separate posedge observer records the inputs consumed by the DUT.
`include "main_phase_base.svh"
`include "sv_tc_factory.svh"
`include "functional_width.svh"
`include "formal_cover_driver.svh"
reg sv_tc_mode=0, main_clock_start=0, main_clock_stop=0, main_have_inputs=0;
integer sv_frames=0, sv_body_slots=0, sv_active=0;
integer main_samples, main_assignments, main_clock_trace=0, main_stage=0;
integer main_sample_cycle, main_sample_phase;
logic [127:0] main_sample_issue_data;
logic main_sample_issue_error;
integer main_low_ns=5, main_high_ns=5, main_pause_ns=0, main_current_high_ns=5;
longint main_edge_ps=-1, main_ready_ps=-1;
bit main_settle_marker=0;
string main_name;
cmdbuf_main_tc main_tc;

// Input #0 is sampled in Observed, after DUT NBA and combinational evaluation.
clocking main_edge_cb @(posedge clk);
    input #0 rst_n;
endclocking
clocking main_feedback_cb @(main_settle_marker);
    input #0 arready, req, arb_addr;
endclocking

task automatic main_wait_for_feedback;
    main_settle_marker <= ~main_settle_marker;
    @(main_feedback_cb);
endtask

initial begin : main_clock
    integer lo,hi,pause_ns;
    wait(main_clock_start);
    while (!main_clock_stop) begin
        hi=main_high_ns; main_current_high_ns=hi;
        #(hi); clk=0;
        // Read the next low phase after this edge's control assignment.
        lo=main_low_ns; pause_ns=main_pause_ns;
        #(lo+pause_ns);
        if (!main_clock_stop) clk=1;
    end
end

// Record actual pin transitions even when FSDB generation is disabled.
always @(clk or rst_n) if (main_clock_trace && $realtime>0)
    $fdisplay(main_clock_trace,"%0.0f,%b,%b",$realtime*1000.0,clk,rst_n);

task automatic main_fail(input string reason, input bit unrecoverable=0);
    begin
        $display("MAIN_PHASE_CONTEXT case=%s cycle=%0d edge=%0d stage=%0d phase=%0d rst_n=%b rready=%b arready=%b req=%h arb_addr=%h mem_due=%b due_data=%h due_error=%b",
            main_name,main_tc.cycle,cycle,main_stage,main_tc.phase,main_tc.rst_n,main_tc.rready,
            arready,req,arb_addr,main_tc.mem_due,main_tc.due_data,main_tc.due_error);
        $display("MAIN_PROPOSED_INPUT arvalid=%b src=%b arresp=%b araddr=%h arid=%h port=%h argrant=%h rwgrant=%h status=%p mem_rdata=%h pc_err=%b",
            main_tc.arvalid,main_tc.src,main_tc.arresp,main_tc.araddr,main_tc.arid,main_tc.ar_port_id,
            main_tc.ar_rd_gnt,main_tc.rw_rd_gnt,main_tc.rd_mem_status,main_tc.mem_rdata,main_tc.pc_err);
        env_fail(reason,$sformatf("case=%s cycle=%0d edge=%0d stage=%0d",main_name,main_tc.cycle,cycle,main_stage),unrecoverable);
    end
endtask

task automatic main_begin(input cmdbuf_main_tc t);
    longint now_ps;
    begin
        now_ps=$realtime/1ps;
        // Let the active-region observer identify the edge, then wait past NBA.
        #0;
        if (now_ps==main_edge_ps && now_ps!=main_ready_ps) @(main_edge_cb);
        if (main_have_inputs || main_stage!=0 || t.cycle!=sv_frames ||
            now_ps!=main_edge_ps) main_fail("MAIN_ONCE_AFTER_POSEDGE");
        main_stage=1;
    end
endtask

task automatic main_control(input cmdbuf_main_tc t);
    begin
        if (main_stage!=1) main_fail("MAIN_CONTROL_ORDER");
        if ($isunknown({t.rst_n,t.rready})) main_fail("MAIN_CONTROL_INVALID");
        // A broken clock cannot execute the remaining procedural cycles safely.
        if ($isunknown({t.clk_low_ns,t.clk_high_ns,t.clk_pause_ns}) ||
            t.clk_low_ns<3 || t.clk_high_ns<3 || t.clk_low_ns>1000000 ||
            t.clk_high_ns>1000000 || t.clk_pause_ns>1000000) main_fail("MAIN_CONTROL_INVALID",1);
        if (t.rst_n !== 1'b1) main_fail("MAIN_RESET_FIXED_HIGH_AFTER_1NS");
        rready=t.rready;
        main_low_ns=t.clk_low_ns; main_high_ns=t.clk_high_ns; main_pause_ns=t.clk_pause_ns;
        main_stage=2;
    end
endtask

task automatic main_context(input cmdbuf_main_tc t);
    begin
        if (main_stage!=2 || $realtime/1ps != main_edge_ps) main_fail("MAIN_CONTEXT_TIMING");
        main_wait_for_feedback();
        if ($isunknown({arready,req}) || ((|req) && $isunknown(arb_addr))) main_fail("MAIN_UNKNOWN_CONTEXT");
        t.edge_count=cycle; t.arready=arready; t.req=req; t.arb_addr=arb_addr;
        t.mem_due=due_data.exists(cycle);
        t.due_data=due_data.exists(cycle) ? due_data[cycle] : 0;
        t.due_error=due_parity.exists(cycle) ? due_parity[cycle] : 0;
        main_stage=3;
    end
endtask

task automatic main_record_assignment(input cmdbuf_main_tc t);
    $fdisplay(main_assignments,"%0d,%0d,%0d,%0.0f,%0d,%0d,%b,%0d,%0d,%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%032h,%0d",
        t.cycle,cycle,t.phase,$realtime*1000.0,main_edge_ps,main_edge_ps+1000*(main_current_high_ns+t.clk_low_ns+t.clk_pause_ns),1'b1,
        t.clk_low_ns,t.clk_high_ns,t.clk_pause_ns,t.rst_n,t.arvalid,t.src,t.araddr,
        t.arid,t.ar_port_id,t.arresp,t.rready,t.ar_rd_gnt,t.rw_rd_gnt,t.rd_mem_status[0],
        t.rd_mem_status[1],t.rd_mem_status[2],t.rd_mem_status[3],t.rd_mem_status[4],t.rd_mem_status[5],t.rd_mem_status[6],t.rd_mem_status[7],
        t.mem_rdata,t.pc_err,t.arready,t.req,t.arb_addr,t.issue_data,t.issue_error);
endtask

task automatic main_record_sample;
    $fdisplay(main_samples,"%0d,%0d,%0d,%0.0f,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h",
        main_sample_cycle,cycle,main_sample_phase,$realtime*1000.0,clk,rst_n,arvalid,
        src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,
        rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],
        rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,
        pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp);
endtask

task automatic main_drive(input cmdbuf_main_tc t);
    begin
        if (main_stage!=3 || $realtime/1ps != main_edge_ps) main_fail("MAIN_DRIVE_ORDER");
        // Validate, never repair. Legal values are assigned by the TC's if blocks.
        if ($isunknown({t.rst_n,t.rready,t.arvalid,t.src,t.araddr,t.arid,t.ar_port_id,
            t.arresp,t.ar_rd_gnt,t.rw_rd_gnt,t.mem_rdata,t.pc_err,t.issue_data,t.issue_error})) main_fail("MAIN_P_KNOWN");
        for (integer b=0;b<8;b++) if ($isunknown(t.rd_mem_status[b])) main_fail("MAIN_P_KNOWN");
        if (t.rst_n) begin
            if (t.arvalid && t.src>2) main_fail("MAIN_E03_SRC");
            if (t.arvalid && t.arresp!=0 && t.arresp!=3) main_fail("MAIN_E04_ARRESP");
            if (t.arready==0 && t.arvalid) main_fail("MAIN_E05_ZERO_READY");
            if (t.src<3 && t.arready[t.src] !== 1'b1) main_fail("MAIN_E07_SRC_READY");
            if ((t.ar_rd_gnt & ~t.req) !== 8'b0) main_fail("MAIN_E08_AR_GRANT_REQ");
            for (integer b=0;b<8;b++)
                if (t.req[b] && t.ar_rd_gnt[b] && t.rw_rd_gnt[b] &&
                    t.rd_mem_status[b][t.arb_addr[0]]!==1'b1) main_fail("MAIN_E06_SUCCESS_BUSY");
            if (t.mem_due && {t.mem_rdata,t.pc_err} !== {t.due_data,t.due_error}) main_fail("MAIN_T_MEM4");
        end
        if ({arready,req,arb_addr} !== {t.arready,t.req,t.arb_addr}) main_fail("MAIN_CONTEXT_CHANGED");
        main_record_assignment(t);
        arvalid=t.arvalid; src=t.src; araddr=t.araddr; arid=t.arid;
        ar_port_id=t.ar_port_id; arresp=t.arresp;
        ar_rd_gnt=t.ar_rd_gnt; rw_rd_gnt=t.rw_rd_gnt;
        for (integer b=0;b<8;b++) rd_mem_status[b]=t.rd_mem_status[b];
        mem_rdata=t.mem_rdata; pc_err=t.pc_err;
        main_sample_cycle=t.cycle; main_sample_phase=t.phase;
        main_sample_issue_data=t.issue_data; main_sample_issue_error=t.issue_error;
        sv_frames++;
        if (t.phase==0) sv_body_slots++;
        main_have_inputs=1; main_stage=0;
        main_wait_for_feedback();
        if (inject=="main_post") arid=arid ^ 8'h01;
        if ({rst_n,rready,arvalid,src,araddr,arid,ar_port_id,arresp,ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err} !==
            {t.rst_n,t.rready,t.arvalid,t.src,t.araddr,t.arid,t.ar_port_id,t.arresp,t.ar_rd_gnt,t.rw_rd_gnt,t.mem_rdata,t.pc_err})
            begin
                $display("MAIN_PIN_DETAIL actual=%h expected=%h",
                    {rst_n,rready,arvalid,src,araddr,arid,ar_port_id,arresp,ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err},
                    {t.rst_n,t.rready,t.arvalid,t.src,t.araddr,t.arid,t.ar_port_id,t.arresp,t.ar_rd_gnt,t.rw_rd_gnt,t.mem_rdata,t.pc_err});
                main_fail("MAIN_PIN_MODIFICATION");
            end
        for (integer b=0;b<8;b++)
            if (rd_mem_status[b] !== t.rd_mem_status[b]) begin
                $display("MAIN_PIN_DETAIL bank=%0d actual=%b expected=%b",b,rd_mem_status[b],t.rd_mem_status[b]);
                main_fail("MAIN_PIN_MODIFICATION");
            end
        if ({arready,req,arb_addr} !== {t.arready,t.req,t.arb_addr}) main_fail("MAIN_CONTEXT_CHANGED");
    end
endtask

// This observer is an independent posedge process. It never drives any input.
always @(posedge clk) if (sv_tc_mode && main_clock_start) begin
    main_edge_ps=$realtime/1ps;
    if (main_have_inputs) begin
        main_record_sample();
        main_have_inputs=0;
        if (rst_n) begin
            sv_active++;
            observe_active(main_sample_issue_error,main_sample_issue_data);
        end else cycle++;
    end else if (cycle==0 && main_edge_ps==10000 && rst_n===1'b1) begin
        // First normal edge is idle and launches the first main_phase assignment.
        $display("MAIN_BOOTSTRAP time_ps=%0d rst_n=1",main_edge_ps);
        cycle++;
    end else begin
        main_fail("MAIN_MISSING_ASSIGNMENT");
        // Continue observing actual held pins; do not invent a missing assignment.
        if (rst_n) observe_active(main_sample_issue_error,main_sample_issue_data);
        else cycle++;
    end
end

always @(main_edge_cb) if (sv_tc_mode && main_clock_start) main_ready_ps=$realtime/1ps;

task automatic main_tail(input integer tail_phase);
    begin
        main_tc.begin_cycle(sv_frames,tail_phase);
        main_tc.rst_n=1; main_tc.rready=7;
        main_tc.clk_low_ns=5; main_tc.clk_high_ns=5; main_tc.clk_pause_ns=0;
        main_tc.drive_control();
        main_tc.sample_context();
        main_tc.arvalid=0; main_tc.src=3; main_tc.arresp=0;
        main_tc.araddr=0; main_tc.arid=0; main_tc.ar_port_id=0;
        main_tc.ar_rd_gnt=main_tc.req; main_tc.rw_rd_gnt=255;
        for (integer b=0;b<8;b++) begin
            if (main_tc.req[b]) main_tc.rd_mem_status[b]=(2'b01 << main_tc.arb_addr[0]);
            else main_tc.rd_mem_status[b]=0;
        end
        main_tc.issue_data=memory_pattern(issued,arb_addr); main_tc.issue_error=0;
        if (main_tc.mem_due) begin main_tc.mem_rdata=main_tc.due_data; main_tc.pc_err=main_tc.due_error; end
        else begin main_tc.mem_rdata=0; main_tc.pc_err=0; end
        main_tc.drive_inputs();
    end
endtask

task automatic run_sv_tc;
    string magic;
    integer override_cycles, header_seed, config_fd;
    cmdbuf_formal_cover_tc formal_tc;
    process rng;
    begin
        if ($fscanf(fd,"%s %s %d %d\n",magic,main_name,header_seed,override_cycles)!=4 ||
            !(magic inside {"MAIN_PHASE_V1","FORMAL_COVER_V1"}) || header_seed!=seed)
            env_fail("MAIN_BAD_PROGRAM","invalid TC header or seed",1);
        if (magic=="FORMAL_COVER_V1") begin
            if (override_cycles<1 || override_cycles>1000000)
                env_fail("FORMAL_BAD_FRAME_COUNT","invalid number of samples",1);
            formal_tc=new(override_cycles);
            formal_tc.load_frames(fd);
            main_tc=formal_tc;
        end else begin
            main_tc=create_sv_tc(main_name);
            if (main_tc==null) env_fail("MAIN_UNKNOWN_NAME",main_name,1);
            if (override_cycles!=0) main_tc.num_cycles=override_cycles;
        end
        $fclose(fd);
        if (main_tc.num_cycles<1 || main_tc.num_cycles>1000000) main_fail("MAIN_CYCLES_INVALID",1);
        rng=process::self(); rng.srandom(seed);
        config_fd=$fopen("tc_config.json","w");
        if (!config_fd) env_fail("MAIN_ARCHIVE_OPEN","tc_config.json",1);
        $fdisplay(config_fd,"{\"name\":\"%s\",\"seed\":%0d,\"num_cycles\":%0d,\"bootstrap_edges\":1,\"drive_offset_ps\":0,\"startup\":{\"clock_initial\":1,\"reset_release_ps\":1000,\"first_posedge_ps\":10000}}",main_name,seed,main_tc.num_cycles);
        $fclose(config_fd);
        main_samples=$fopen("samples.csv","w"); main_assignments=$fopen("assigned.csv","w");
        if (!main_samples || !main_assignments) env_fail("MAIN_ARCHIVE_OPEN","samples.csv or assigned.csv",1);
        $fdisplay(main_assignments,"cycle,edge,phase,time_ps,launch_ps,expected_sample_ps,clk,clk_low_ns,clk_high_ns,clk_pause_ns,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,issue_data,issue_error");
        $fdisplay(main_samples,"cycle,edge,phase,time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp");
        // Startup reset is driven only by the common t=0 / t=1 ns process.
        rready=0; arvalid=0; src=3; arresp=0; araddr=0; arid=0; ar_port_id=0;
        ar_rd_gnt=0; rw_rd_gnt=0; mem_rdata=0; pc_err=0;
        for (integer b=0;b<8;b++) rd_mem_status[b]=0;
        main_clock_trace=$fopen("clock_reset.csv","w");
        if (!main_clock_trace) env_fail("MAIN_CLOCK_ARCHIVE_OPEN","clock_reset.csv",1);
        $fdisplay(main_clock_trace,"time_ps,clk,rst_n");
        #0; // Allow the time-zero reset assignment to settle before recording pins.
        $fdisplay(main_clock_trace,"%0.0f,%b,%b",$realtime*1000.0,clk,rst_n);
        wave_resets=1; main_clock_start=1;
        main_tc.main_phase();
        if (sv_body_slots!=main_tc.num_cycles) main_fail("MAIN_BODY_COUNT");
        // Consume the final body assignment before examining the drain condition.
        @(main_edge_cb);
        for (integer k=0;formal_tc==null && k<drain_limit &&
             (accepted!=retired+wave_aborted || due_data.num()!=0 || req!=0 || (monitor.strict_mode && pending_cnt!=0));k++) begin
            main_tail(1);
            @(main_edge_cb);
        end
        if (accepted!=retired+wave_aborted || accepted!=issued+wave_aborted_unissued ||
            issued!=captured+wave_aborted_mem || captured!=retired+wave_aborted_response ||
            due_data.num()!=0 || req!=0 || (monitor.strict_mode && pending_cnt!=0)) drain_failure("MAIN_DRAIN_FAILED");
        if (formal_tc==null) repeat (8) begin
            main_tail(2);
            @(main_edge_cb);
        end
        // Settle the last monitor sample before flushing it in finish_checks.
        main_wait_for_feedback();
        main_clock_stop=1;
        $fclose(main_samples); $fclose(main_assignments); $fclose(main_clock_trace); main_clock_trace=0;
        $display("MAIN_PHASE_DONE case=%s assignments=%0d edges=%0d active_edges=%0d resets=%0d cancelled=%0d",
            main_name,sv_frames,cycle,sv_active,wave_resets,wave_aborted);
    end
endtask
